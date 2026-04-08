//! Microphone capture via `cpal` with VAD silence detection.
//!
//! `AudioRecorder` records on a dedicated OS thread (cpal's `Stream` is not
//! `Send` on macOS).  The capture callback both accumulates samples AND
//! updates a shared VAD state (first/last voiced timestamps).  The recording
//! thread polls that state to decide when to auto-stop so the user doesn't
//! have to hold the hotkey — press once to start, then just pause speaking.
//!
//! Stop conditions (first match wins):
//! 1. Explicit `stop_now()` signal from the UI layer (`StopReason::Manual`).
//! 2. After at least one voiced frame, `silence_after_voice` of continuous
//!    silence (`StopReason::Silence` — the normal case).
//! 3. `initial_grace` elapsed and no voice has ever been detected
//!    (`StopReason::NoVoice`).
//! 4. `max_duration` safety cap hit (`StopReason::MaxDuration`).

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::SampleFormat;

use crate::error::{AppError, AppResult};

// ──────────────────────────────────────────────────────────────────────────────
// VAD tuning

/// Knobs for the silence-triggered stop behaviour.
#[derive(Debug, Clone, Copy)]
pub struct VadConfig {
    /// RMS (normalised to [-1, 1]) at or below which a chunk counts as
    /// silence.  ~0.01 ≈ -40 dBFS; tweak if background noise triggers
    /// false positives.
    pub rms_threshold: f32,
    /// Once at least one voiced chunk has been seen, this much continuous
    /// quiet ends the recording.  This is the "breath room" knob — 2 s
    /// lets the user pause mid-sentence without cutting them off.
    pub silence_after_voice: Duration,
    /// If no voice has been heard yet, give up after this much.  Prevents
    /// a fat-fingered hotkey press from holding the mic forever.
    pub initial_grace: Duration,
    /// Absolute cap on a single recording session.
    pub max_duration: Duration,
}

impl Default for VadConfig {
    fn default() -> Self {
        Self {
            rms_threshold: 0.01,
            silence_after_voice: Duration::from_millis(2_000),
            initial_grace: Duration::from_millis(5_000),
            max_duration: Duration::from_millis(60_000),
        }
    }
}

/// Reason a recording ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StopReason {
    /// Explicit stop from the UI (second hotkey press, etc.).
    Manual,
    /// Natural end: user went quiet for `silence_after_voice`.
    Silence,
    /// User never spoke; `initial_grace` expired.
    NoVoice,
    /// Hit the `max_duration` safety cap.
    MaxDuration,
}

/// Result returned from a completed recording session.
pub struct RecordingResult {
    pub wav: Vec<u8>,
    pub reason: StopReason,
    pub duration_ms: u64,
}

// ──────────────────────────────────────────────────────────────────────────────
// Shared VAD state (cpal callback ↔ recording thread)

/// Timestamps are stored as "milliseconds since `start`", using `0` as the
/// sentinel for "not yet observed".  The first observed frame is clamped to
/// `1 ms` so the sentinel stays reserved.
struct VadState {
    first_voiced_ms: AtomicU64,
    last_voiced_ms: AtomicU64,
    start: Instant,
}

impl VadState {
    fn new(start: Instant) -> Self {
        Self {
            first_voiced_ms: AtomicU64::new(0),
            last_voiced_ms: AtomicU64::new(0),
            start,
        }
    }

    /// Called from the cpal capture callback on every chunk.  Cheap: one
    /// elapsed call, one atomic store, and (only on the first voiced chunk)
    /// one CAS.
    fn observe(&self, rms: f32, threshold: f32) {
        if !(rms > threshold) {
            // `>` (not `>=`) so an explicit threshold of 0 still ignores
            // truly-silent frames; `!` form so NaN is treated as silence.
            return;
        }
        let now_ms = self.start.elapsed().as_millis() as u64;
        let now_ms = now_ms.max(1); // reserve 0 for "unset"
        self.last_voiced_ms.store(now_ms, Ordering::Relaxed);
        let _ = self.first_voiced_ms.compare_exchange(
            0,
            now_ms,
            Ordering::Relaxed,
            Ordering::Relaxed,
        );
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// Public recorder handle

/// A live recording session.  Drop without waiting to discard the capture;
/// the background thread exits when its stop channel closes.
pub struct AudioRecorder {
    stop_tx: std::sync::mpsc::SyncSender<()>,
    result_rx: std::sync::mpsc::Receiver<AppResult<RecordingResult>>,
}

// SAFETY: only the channel halves cross threads; the cpal stream itself
// lives on its own thread and never escapes it.
unsafe impl Send for AudioRecorder {}

impl AudioRecorder {
    /// Spawn a recording thread and start capturing from the default input
    /// device.  Returns immediately; samples accumulate until either
    /// `stop_now()` is called or the VAD policy decides the user is done.
    pub fn start(vad: VadConfig) -> AppResult<Self> {
        let (stop_tx, stop_rx) = std::sync::mpsc::sync_channel(1);
        let (result_tx, result_rx) = std::sync::mpsc::channel();

        std::thread::spawn(move || {
            let result = record_thread(vad, stop_rx);
            let _ = result_tx.send(result);
        });

        Ok(Self { stop_tx, result_rx })
    }

    /// Explicit stop signal (e.g. second hotkey press).  Idempotent — calling
    /// it twice is harmless.  The result is still delivered via
    /// `wait_for_completion`.
    pub fn stop_now(&self) {
        let _ = self.stop_tx.try_send(());
    }

    /// Block until the recording thread finishes (VAD-triggered or manual
    /// stop) and return the captured WAV bytes plus the reason it ended.
    ///
    /// **Blocking** — run from `tokio::task::spawn_blocking`.
    pub fn wait_for_completion(self) -> AppResult<RecordingResult> {
        self.result_rx
            .recv()
            .map_err(|_| AppError::Platform("recording thread disconnected".into()))
            .and_then(|r| r)
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// Recording thread

/// How often the recording thread wakes to check the stop signal and VAD
/// state.  Fine enough to react within ~50 ms of a silence/voice transition.
const POLL_INTERVAL: Duration = Duration::from_millis(50);

fn record_thread(
    vad: VadConfig,
    stop_rx: std::sync::mpsc::Receiver<()>,
) -> AppResult<RecordingResult> {
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .ok_or_else(|| AppError::Platform("no default audio input device".into()))?;
    let config = device
        .default_input_config()
        .map_err(|e| AppError::Platform(format!("audio config: {e}")))?;

    let channels = config.channels();
    let sample_rate = config.sample_rate().0;
    let samples: Arc<Mutex<Vec<f32>>> = Arc::new(Mutex::new(Vec::new()));
    let start = Instant::now();
    let state = Arc::new(VadState::new(start));

    let stream = build_stream(
        &device,
        &config,
        Arc::clone(&samples),
        Arc::clone(&state),
        vad.rms_threshold,
    )?;
    stream
        .play()
        .map_err(|e| AppError::Platform(format!("start audio stream: {e}")))?;

    let reason = poll_until_stop(&vad, &stop_rx, &state, start);

    drop(stream);

    let data = samples.lock().unwrap().clone();
    let wav = encode_wav_f32(&data, channels, sample_rate)?;
    Ok(RecordingResult {
        wav,
        reason,
        duration_ms: start.elapsed().as_millis() as u64,
    })
}

/// Decide (on this thread) when to stop the recording based on the shared
/// VAD state plus the explicit stop signal.  Pulled out of `record_thread`
/// so it can be unit-tested without touching cpal.
fn poll_until_stop(
    vad: &VadConfig,
    stop_rx: &std::sync::mpsc::Receiver<()>,
    state: &VadState,
    start: Instant,
) -> StopReason {
    use std::sync::mpsc::TryRecvError;

    loop {
        match stop_rx.try_recv() {
            Ok(_) | Err(TryRecvError::Disconnected) => return StopReason::Manual,
            Err(TryRecvError::Empty) => {}
        }

        let elapsed = start.elapsed();
        if elapsed >= vad.max_duration {
            return StopReason::MaxDuration;
        }

        let first = state.first_voiced_ms.load(Ordering::Relaxed);
        if first == 0 {
            if elapsed >= vad.initial_grace {
                return StopReason::NoVoice;
            }
        } else {
            let elapsed_ms = elapsed.as_millis() as u64;
            let last = state.last_voiced_ms.load(Ordering::Relaxed);
            let since_last = elapsed_ms.saturating_sub(last);
            if since_last >= vad.silence_after_voice.as_millis() as u64 {
                return StopReason::Silence;
            }
        }

        std::thread::sleep(POLL_INTERVAL);
    }
}

fn build_stream(
    device: &cpal::Device,
    config: &cpal::SupportedStreamConfig,
    samples: Arc<Mutex<Vec<f32>>>,
    state: Arc<VadState>,
    rms_threshold: f32,
) -> AppResult<cpal::Stream> {
    let stream_config = config.config();

    macro_rules! int_stream {
        ($t:ty, $max:expr) => {{
            let s = Arc::clone(&samples);
            let st = Arc::clone(&state);
            device.build_input_stream(
                &stream_config,
                move |data: &[$t], _| {
                    let scale = 1.0f32 / ($max as f32);
                    let mut sum_sq = 0.0f32;
                    {
                        let mut buf = s.lock().unwrap();
                        buf.reserve(data.len());
                        for &v in data {
                            let f = v as f32 * scale;
                            buf.push(f);
                            sum_sq += f * f;
                        }
                    }
                    let n = data.len().max(1) as f32;
                    let rms = (sum_sq / n).sqrt();
                    st.observe(rms, rms_threshold);
                },
                |e| tracing::error!("audio stream error: {e}"),
                None,
            )
        }};
    }

    let stream = match config.sample_format() {
        SampleFormat::F32 => {
            let s = Arc::clone(&samples);
            let st = Arc::clone(&state);
            device.build_input_stream(
                &stream_config,
                move |data: &[f32], _| {
                    {
                        let mut buf = s.lock().unwrap();
                        buf.extend_from_slice(data);
                    }
                    let mut sum_sq = 0.0f32;
                    for &f in data {
                        sum_sq += f * f;
                    }
                    let n = data.len().max(1) as f32;
                    let rms = (sum_sq / n).sqrt();
                    st.observe(rms, rms_threshold);
                },
                |e| tracing::error!("audio stream error: {e}"),
                None,
            )
        }
        SampleFormat::I16 => int_stream!(i16, i16::MAX),
        SampleFormat::I32 => int_stream!(i32, i32::MAX),
        SampleFormat::U8 => {
            let s = Arc::clone(&samples);
            let st = Arc::clone(&state);
            device.build_input_stream(
                &stream_config,
                move |data: &[u8], _| {
                    let mut sum_sq = 0.0f32;
                    {
                        let mut buf = s.lock().unwrap();
                        buf.reserve(data.len());
                        for &v in data {
                            let f = v as f32 / 128.0 - 1.0;
                            buf.push(f);
                            sum_sq += f * f;
                        }
                    }
                    let n = data.len().max(1) as f32;
                    let rms = (sum_sq / n).sqrt();
                    st.observe(rms, rms_threshold);
                },
                |e| tracing::error!("audio stream error: {e}"),
                None,
            )
        }
        SampleFormat::U16 => {
            let s = Arc::clone(&samples);
            let st = Arc::clone(&state);
            device.build_input_stream(
                &stream_config,
                move |data: &[u16], _| {
                    let mut sum_sq = 0.0f32;
                    {
                        let mut buf = s.lock().unwrap();
                        buf.reserve(data.len());
                        for &v in data {
                            let f = v as f32 / 32768.0 - 1.0;
                            buf.push(f);
                            sum_sq += f * f;
                        }
                    }
                    let n = data.len().max(1) as f32;
                    let rms = (sum_sq / n).sqrt();
                    st.observe(rms, rms_threshold);
                },
                |e| tracing::error!("audio stream error: {e}"),
                None,
            )
        }
        fmt => {
            return Err(AppError::Platform(format!(
                "unsupported sample format: {fmt:?}"
            )))
        }
    }
    .map_err(|e| AppError::Platform(format!("build audio stream: {e}")))?;

    Ok(stream)
}

fn encode_wav_f32(samples: &[f32], channels: u16, sample_rate: u32) -> AppResult<Vec<u8>> {
    use std::io::Cursor;

    let spec = hound::WavSpec {
        channels,
        sample_rate,
        bits_per_sample: 32,
        sample_format: hound::SampleFormat::Float,
    };
    let mut buf = Cursor::new(Vec::new());
    {
        let mut writer = hound::WavWriter::new(&mut buf, spec)
            .map_err(|e| AppError::Platform(format!("wav writer: {e}")))?;
        for &s in samples {
            writer
                .write_sample(s)
                .map_err(|e| AppError::Platform(format!("wav write sample: {e}")))?;
        }
    }
    Ok(buf.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── encode_wav_f32 ───────────────────────────────────────────────────

    #[test]
    fn encode_empty_samples_produces_valid_wav_header() {
        let wav = encode_wav_f32(&[], 1, 16000).unwrap();
        assert!(wav.len() >= 44, "WAV must have at least a 44-byte header");
        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
    }

    #[test]
    fn encode_samples_data_length_matches() {
        let samples = vec![0.0f32; 16000]; // 1 second mono at 16 kHz
        let wav = encode_wav_f32(&samples, 1, 16000).unwrap();
        assert!(wav.len() > 44 + 16000 * 4 - 8);
        assert_eq!(&wav[0..4], b"RIFF");
    }

    #[test]
    fn encode_stereo_wav_has_riff_magic() {
        let samples = vec![0.5f32; 64];
        let wav = encode_wav_f32(&samples, 2, 44100).unwrap();
        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
    }

    #[test]
    fn encode_wav_is_decodable_by_hound() {
        let original: Vec<f32> = (0..100).map(|i| i as f32 / 100.0).collect();
        let wav = encode_wav_f32(&original, 1, 22050).unwrap();

        let mut reader = hound::WavReader::new(std::io::Cursor::new(wav)).unwrap();
        let spec = reader.spec();
        assert_eq!(spec.channels, 1);
        assert_eq!(spec.sample_rate, 22050);
        assert_eq!(spec.bits_per_sample, 32);

        let decoded: Vec<f32> = reader.samples::<f32>().map(|s| s.unwrap()).collect();
        assert_eq!(decoded.len(), original.len());
        for (a, b) in original.iter().zip(decoded.iter()) {
            assert!((a - b).abs() < 1e-6, "sample mismatch: {a} vs {b}");
        }
    }

    // ── VadConfig defaults ───────────────────────────────────────────────

    #[test]
    fn vad_default_has_breath_friendly_silence_window() {
        let v = VadConfig::default();
        // The user asked for 2 s of silence before stopping — verify the
        // default actually reflects that.
        assert_eq!(v.silence_after_voice, Duration::from_millis(2_000));
        // And that the safety rails are sane.
        assert!(v.initial_grace >= Duration::from_secs(3));
        assert!(v.max_duration >= Duration::from_secs(30));
        assert!(v.rms_threshold > 0.0 && v.rms_threshold < 0.2);
    }

    // ── VadState ─────────────────────────────────────────────────────────

    #[test]
    fn vad_state_ignores_subthreshold_frames() {
        let st = VadState::new(Instant::now());
        st.observe(0.001, 0.01);
        assert_eq!(st.first_voiced_ms.load(Ordering::Relaxed), 0);
        assert_eq!(st.last_voiced_ms.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn vad_state_records_first_voiced_and_preserves_it() {
        // Rewind the start so elapsed() returns a comfortably non-zero value.
        let st = VadState::new(Instant::now() - Duration::from_millis(500));
        st.observe(0.5, 0.01);
        let first = st.first_voiced_ms.load(Ordering::Relaxed);
        assert!(first >= 500, "first_voiced_ms should reflect elapsed");

        std::thread::sleep(Duration::from_millis(10));
        st.observe(0.5, 0.01);
        let last = st.last_voiced_ms.load(Ordering::Relaxed);
        // Second observation updates last_voiced_ms...
        assert!(last >= first);
        // ...but leaves first_voiced_ms unchanged.
        assert_eq!(st.first_voiced_ms.load(Ordering::Relaxed), first);
    }

    #[test]
    fn vad_state_ignores_nan_frames() {
        let st = VadState::new(Instant::now());
        st.observe(f32::NAN, 0.01);
        assert_eq!(st.first_voiced_ms.load(Ordering::Relaxed), 0);
    }

    // ── poll_until_stop ──────────────────────────────────────────────────

    fn _short_vad() -> VadConfig {
        VadConfig {
            rms_threshold: 0.01,
            silence_after_voice: Duration::from_millis(120),
            initial_grace: Duration::from_millis(120),
            max_duration: Duration::from_millis(600),
        }
    }

    #[test]
    fn poll_returns_manual_on_explicit_stop() {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        let start = Instant::now();
        let state = VadState::new(start);
        tx.send(()).unwrap();
        let reason = poll_until_stop(&_short_vad(), &rx, &state, start);
        assert_eq!(reason, StopReason::Manual);
    }

    #[test]
    fn poll_returns_manual_when_sender_dropped() {
        let (tx, rx) = std::sync::mpsc::sync_channel::<()>(1);
        drop(tx);
        let start = Instant::now();
        let state = VadState::new(start);
        let reason = poll_until_stop(&_short_vad(), &rx, &state, start);
        assert_eq!(reason, StopReason::Manual);
    }

    #[test]
    fn poll_returns_no_voice_after_initial_grace() {
        let (_tx, rx) = std::sync::mpsc::sync_channel::<()>(1);
        let start = Instant::now();
        let state = VadState::new(start);
        let reason = poll_until_stop(&_short_vad(), &rx, &state, start);
        // Silence the whole time → NoVoice (grace < max_duration).
        assert_eq!(reason, StopReason::NoVoice);
    }

    #[test]
    fn poll_returns_silence_after_voice_then_quiet() {
        let (_tx, rx) = std::sync::mpsc::sync_channel::<()>(1);
        let start = Instant::now();
        let state = VadState::new(start);
        // Simulate a voiced chunk right now.
        state.observe(0.5, 0.01);
        // Now the poll loop is waiting for silence_after_voice of quiet.
        let reason = poll_until_stop(&_short_vad(), &rx, &state, start);
        assert_eq!(reason, StopReason::Silence);
    }

    #[test]
    fn poll_returns_max_duration_when_voice_never_stops() {
        // A thread keeps "observing" voice so last_voiced_ms stays fresh.
        // The loop should eventually bail out on max_duration.
        let (_tx, rx) = std::sync::mpsc::sync_channel::<()>(1);
        let start = Instant::now();
        let state = Arc::new(VadState::new(start));

        let st_bg = Arc::clone(&state);
        let stop_flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let stop_bg = Arc::clone(&stop_flag);
        let bg = std::thread::spawn(move || {
            while !stop_bg.load(Ordering::Relaxed) {
                st_bg.observe(0.5, 0.01);
                std::thread::sleep(Duration::from_millis(20));
            }
        });

        let reason = poll_until_stop(&_short_vad(), &rx, &state, start);
        stop_flag.store(true, Ordering::Relaxed);
        bg.join().unwrap();
        assert_eq!(reason, StopReason::MaxDuration);
    }
}
