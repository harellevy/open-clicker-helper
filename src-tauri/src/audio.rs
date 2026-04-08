//! Microphone capture via `cpal`.
//!
//! `AudioRecorder` records on a dedicated OS thread (cpal's `Stream` is not
//! `Send` on macOS) and communicates with the async runtime through sync
//! channels. `start()` is non-blocking; `stop_and_encode()` blocks until the
//! recording thread finishes writing the WAV — call it from
//! `tokio::task::spawn_blocking`.

use std::sync::{Arc, Mutex};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::SampleFormat;

use crate::error::{AppError, AppResult};

// ──────────────────────────────────────────────────────────────────────────────

/// A live recording session.  Drop without calling `stop_and_encode` to
/// discard the captured audio (the background thread exits when the stop
/// channel closes).
pub struct AudioRecorder {
    stop_tx: std::sync::mpsc::SyncSender<()>,
    result_rx: std::sync::mpsc::Receiver<AppResult<Vec<u8>>>,
}

// SAFETY: the channel ends are Send; the actual cpal stream lives on its own
// thread and never crosses thread boundaries.
unsafe impl Send for AudioRecorder {}

impl AudioRecorder {
    /// Spawn a recording thread and start capturing from the default input
    /// device.  Returns immediately; samples accumulate until `stop_and_encode`
    /// is called.
    pub fn start() -> AppResult<Self> {
        let (stop_tx, stop_rx) = std::sync::mpsc::sync_channel(1);
        let (result_tx, result_rx) = std::sync::mpsc::channel();

        std::thread::spawn(move || {
            let result = record_thread(stop_rx);
            let _ = result_tx.send(result);
        });

        Ok(Self { stop_tx, result_rx })
    }

    /// Signal the recording thread to stop, wait for it to flush, and return
    /// the captured audio as a 32-bit float WAV byte vector.
    ///
    /// **Blocking** — run in `tokio::task::spawn_blocking`.
    pub fn stop_and_encode(self) -> AppResult<Vec<u8>> {
        let _ = self.stop_tx.send(());
        self.result_rx
            .recv()
            .map_err(|_| AppError::Platform("recording thread disconnected".into()))
            .and_then(|r| r)
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// Recording thread

fn record_thread(stop_rx: std::sync::mpsc::Receiver<()>) -> AppResult<Vec<u8>> {
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

    let stream = build_stream(&device, &config, Arc::clone(&samples))?;
    stream
        .play()
        .map_err(|e| AppError::Platform(format!("start audio stream: {e}")))?;

    // Block until the stop signal arrives (or the sender is dropped).
    let _ = stop_rx.recv();
    drop(stream);

    let data = samples.lock().unwrap().clone();
    encode_wav_f32(&data, channels, sample_rate)
}

fn build_stream(
    device: &cpal::Device,
    config: &cpal::SupportedStreamConfig,
    samples: Arc<Mutex<Vec<f32>>>,
) -> AppResult<cpal::Stream> {
    let stream_config = config.config();

    macro_rules! int_stream {
        ($t:ty, $max:expr) => {{
            let s = Arc::clone(&samples);
            device.build_input_stream(
                &stream_config,
                move |data: &[$t], _| {
                    let mut buf = s.lock().unwrap();
                    buf.extend(data.iter().map(|&v| v as f32 / $max as f32));
                },
                |e| tracing::error!("audio stream error: {e}"),
                None,
            )
        }};
    }

    let stream = match config.sample_format() {
        SampleFormat::F32 => {
            let s = Arc::clone(&samples);
            device.build_input_stream(
                &stream_config,
                move |data: &[f32], _| s.lock().unwrap().extend_from_slice(data),
                |e| tracing::error!("audio stream error: {e}"),
                None,
            )
        }
        SampleFormat::I16 => int_stream!(i16, i16::MAX),
        SampleFormat::I32 => int_stream!(i32, i32::MAX),
        SampleFormat::U8 => {
            let s = Arc::clone(&samples);
            device.build_input_stream(
                &stream_config,
                move |data: &[u8], _| {
                    let mut buf = s.lock().unwrap();
                    buf.extend(data.iter().map(|&v| v as f32 / 128.0 - 1.0));
                },
                |e| tracing::error!("audio stream error: {e}"),
                None,
            )
        }
        SampleFormat::U16 => {
            let s = Arc::clone(&samples);
            device.build_input_stream(
                &stream_config,
                move |data: &[u16], _| {
                    let mut buf = s.lock().unwrap();
                    buf.extend(data.iter().map(|&v| v as f32 / 32768.0 - 1.0));
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
