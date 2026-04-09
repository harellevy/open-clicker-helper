//! open-clicker-helper — Tauri shell.
//!
//! Boundaries:
//! - `platform/` holds OS-specific code behind traits so the Windows port
//!   later only adds a sibling module.
//! - `ipc` exposes `tauri::command`s consumed by the React frontend.
//! - `sidecar` spawns the Python AI service over stdio JSON-RPC.
//! - `store` defines the settings types persisted via tauri-plugin-store.
//! - `audio` records mic audio via cpal (P3).

mod audio;
mod error;
mod ipc;
mod platform;
mod sidecar;
mod store;

use std::path::PathBuf;
use std::sync::Arc;

use base64::Engine;
use serde::Serialize;
use tauri::utils::config::Color;
use tauri::{Emitter, Manager};
use tracing_subscriber::EnvFilter;

use crate::sidecar::Sidecar;

// ──────────────────────────────────────────────────────────────────────────────

/// Shared application state held by Tauri's manager.
pub struct AppState {
    pub sidecar: tokio::sync::Mutex<Option<Arc<Sidecar>>>,
    /// Active recording session (None when idle).
    pub recorder: tokio::sync::Mutex<Option<audio::AudioRecorder>>,
}

/// Frontend-visible state of the push-to-talk activation.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase", tag = "state")]
pub enum HotkeyState {
    Idle,
    Recording,
    Processing,
    #[serde(rename_all = "camelCase")]
    Error {
        message: String,
    },
}

// ──────────────────────────────────────────────────────────────────────────────

pub fn run() {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_target(false)
        .init();

    tauri::Builder::default()
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(AppState {
            sidecar: tokio::sync::Mutex::new(None),
            recorder: tokio::sync::Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            ipc::get_permissions,
            ipc::open_system_settings,
            ipc::get_settings,
            ipc::save_settings,
            ipc::reset_settings,
            ipc::ping_sidecar,
            ipc::sidecar_call,
            ipc::capture_screen,
            ipc::ax_locate,
            ipc::click_at_normalized,
        ])
        .setup(|app| {
            // Configure the transparent overlay window with native macOS flags.
            if let Some(overlay) = app.get_webview_window("overlay") {
                // Explicitly clear the WebView background so it doesn't
                // default to white even when `transparent: true` is set.
                let _ = overlay.set_background_color(Some(Color(0, 0, 0, 0)));

                // Size overlay to the full primary monitor.
                if let Some(monitor) = overlay.primary_monitor().ok().flatten() {
                    let size = monitor.size();
                    let _ = overlay.set_size(tauri::Size::Physical(tauri::PhysicalSize {
                        width: size.width,
                        height: size.height,
                    }));
                    let _ =
                        overlay.set_position(tauri::Position::Physical(tauri::PhysicalPosition {
                            x: 0,
                            y: 0,
                        }));
                }

                #[cfg(target_os = "macos")]
                {
                    let configurator = platform::current().overlay();
                    if let Err(e) = configurator.make_click_through_topmost(&overlay) {
                        tracing::warn!("overlay configuration failed: {e}");
                    }
                }
                // Show overlay now (P3 uses it for the HUD bubble).
                let _ = overlay.show();
            }

            // Spawn the Python sidecar + start the progress relay.
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                match resolve_sidecar_dir(&handle) {
                    Ok((uv, dir)) => match Sidecar::spawn(&uv, &dir).await {
                        Ok(sc) => {
                            tracing::info!("sidecar spawned (uv={uv:?}, dir={dir:?})");

                            let mut progress_rx = sc.progress();
                            let relay = handle.clone();
                            tauri::async_runtime::spawn(async move {
                                while let Ok(p) = progress_rx.recv().await {
                                    let _ = relay.emit("sidecar://progress", &p);
                                }
                                tracing::debug!("sidecar progress relay ended");
                            });

                            let state = handle.state::<AppState>();
                            *state.sidecar.lock().await = Some(Arc::new(sc));
                        }
                        Err(e) => tracing::error!("sidecar spawn failed: {e}"),
                    },
                    Err(e) => tracing::error!("sidecar dir resolution failed: {e}"),
                }
            });

            // Register the global push-to-talk hotkey from settings.
            register_hotkey(app);

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

// ──────────────────────────────────────────────────────────────────────────────
// Global hotkey (push-to-talk)

fn register_hotkey(app: &mut tauri::App) {
    use tauri_plugin_global_shortcut::GlobalShortcutExt;
    use tauri_plugin_store::StoreExt;

    // Read the configured hotkey accelerator from the store.
    let accelerator: String = app
        .store(store::STORE_FILE)
        .ok()
        .and_then(|s| s.get(store::SETTINGS_KEY))
        .and_then(|v| serde_json::from_value::<store::Settings>(v).ok())
        .map(|s| s.hotkey)
        .unwrap_or_else(|| "CommandOrControl+Shift+Space".into());

    let result = app.handle().global_shortcut().on_shortcut(
        accelerator.as_str(),
        |app, _shortcut, event| {
            use tauri_plugin_global_shortcut::ShortcutState;
            // Press-to-toggle + VAD auto-stop: one press starts the
            // recording, a second press (or silence) ends it.  Key-up is
            // intentionally ignored so the user doesn't have to hold the
            // shortcut while speaking a long question.
            if matches!(event.state, ShortcutState::Pressed) {
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    on_press(&handle).await;
                });
            }
        },
    );

    match result {
        Ok(()) => tracing::info!("hotkey registered: {accelerator}"),
        Err(e) => tracing::warn!("hotkey registration failed: {e}"),
    }
}

/// Press handler: toggle the recorder.  First press starts capture; a
/// second press before VAD silence fires acts as an explicit stop.  After
/// starting a fresh recording we spawn a background task that waits for
/// it to complete (either naturally or via stop_now) and then runs the
/// pipeline.
async fn on_press(app: &tauri::AppHandle) {
    let state = app.state::<AppState>();
    let mut rec_slot = state.recorder.lock().await;

    if let Some(existing) = rec_slot.as_ref() {
        // Already recording → treat this press as "I'm done talking".
        existing.stop_now();
        tracing::debug!("second press → explicit stop");
        return;
    }

    match audio::AudioRecorder::start(audio::VadConfig::default()) {
        Ok(recorder) => {
            *rec_slot = Some(recorder);
            drop(rec_slot);
            let _ = app.emit("hotkey-state", HotkeyState::Recording);
            tracing::debug!("recording started");

            // Waiter: block on the recording thread, then run the pipeline.
            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                process_recording(&handle).await;
            });
        }
        Err(e) => {
            tracing::error!("start recording: {e}");
            let _ = app.emit(
                "hotkey-state",
                HotkeyState::Error {
                    message: e.to_string(),
                },
            );
        }
    }
}

/// Waiter task: blocks until the active recorder finishes, then either
/// drops the result (no speech) or runs the full pipeline.
async fn process_recording(app: &tauri::AppHandle) {
    let state = app.state::<AppState>();
    let recorder = match state.recorder.lock().await.take() {
        Some(r) => r,
        None => return,
    };

    // wait_for_completion is blocking (mpsc::recv) — hop off the async runtime.
    let recording = match tokio::task::spawn_blocking(move || recorder.wait_for_completion()).await
    {
        Ok(Ok(r)) => r,
        Ok(Err(e)) => {
            tracing::error!("wait_for_completion: {e}");
            let _ = app.emit(
                "hotkey-state",
                HotkeyState::Error {
                    message: e.to_string(),
                },
            );
            return;
        }
        Err(e) => {
            tracing::error!("spawn_blocking: {e}");
            let _ = app.emit(
                "hotkey-state",
                HotkeyState::Error {
                    message: e.to_string(),
                },
            );
            return;
        }
    };

    tracing::info!(
        "recording ended: reason={:?} duration_ms={} bytes={}",
        recording.reason,
        recording.duration_ms,
        recording.wav.len()
    );

    // User never spoke — skip STT/VLM/TTS entirely and return to idle.
    if matches!(recording.reason, audio::StopReason::NoVoice) {
        tracing::info!("no voice detected — cancelling pipeline");
        let _ = app.emit("hotkey-state", HotkeyState::Idle);
        return;
    }

    let _ = app.emit("hotkey-state", HotkeyState::Processing);

    let wav_bytes = recording.wav;
    tracing::info!("recorded {} bytes of audio", wav_bytes.len());

    // Load settings to pass to the pipeline.
    let settings_val = load_settings_json(app);

    let audio_b64 = base64::engine::general_purpose::STANDARD.encode(&wav_bytes);

    // Capture a screenshot concurrently with the audio encode.  We do it in
    // spawn_blocking because screencapture spawns a child process.
    let image_b64: Option<String> = match tokio::task::spawn_blocking(move || {
        platform::current().capture().capture_focused_window()
    })
    .await
    {
        Ok(Ok(png)) => {
            tracing::info!("captured screenshot ({} bytes)", png.len());
            Some(base64::engine::general_purpose::STANDARD.encode(&png))
        }
        Ok(Err(e)) => {
            tracing::warn!("screenshot failed (continuing without image): {e}");
            None
        }
        Err(e) => {
            tracing::warn!("spawn_blocking for screenshot: {e}");
            None
        }
    };

    // Collect AX-tree candidates for the focused window. The call itself is
    // fast (raw CF pointer walk) but it hits the WindowServer, so run it in
    // spawn_blocking to stay off the async runtime. Candidates are normalised
    // to the logical screen's [0, 1] coordinate space before we forward them
    // to the sidecar, mirroring the pipeline's convention for VLM coordinates.
    let ax_candidates =
        match tokio::task::spawn_blocking(|| platform::current().ax().focused_window_candidates())
            .await
        {
            Ok(Ok(v)) => v,
            Ok(Err(e)) => {
                tracing::warn!("ax_locate failed (continuing without AX): {e}");
                Vec::new()
            }
            Err(e) => {
                tracing::warn!("spawn_blocking for ax_locate: {e}");
                Vec::new()
            }
        };
    let ax_candidates_json = normalise_ax_candidates(app, ax_candidates);
    tracing::info!(
        "AX candidates: {} (normalised)",
        ax_candidates_json.as_array().map(Vec::len).unwrap_or(0)
    );

    // Call the sidecar pipeline.
    let sidecar = state.sidecar.lock().await.clone();
    let Some(sidecar) = sidecar else {
        tracing::warn!("sidecar not ready");
        let _ = app.emit(
            "hotkey-state",
            HotkeyState::Error {
                message: "sidecar not ready".into(),
            },
        );
        return;
    };

    match sidecar
        .call(
            "pipeline.run",
            serde_json::json!({
                "audio_b64": audio_b64,
                "image_b64": image_b64,
                "settings": settings_val,
                "ax_candidates": ax_candidates_json,
            }),
        )
        .await
    {
        Ok(result) => {
            let _ = app.emit("pipeline-result", result);
            tracing::debug!("pipeline completed");
        }
        Err(e) => {
            tracing::error!("pipeline: {e}");
            let _ = app.emit(
                "hotkey-state",
                HotkeyState::Error {
                    message: e.to_string(),
                },
            );
        }
    }

    let _ = app.emit("hotkey-state", HotkeyState::Idle);
}

fn load_settings_json(app: &tauri::AppHandle) -> serde_json::Value {
    use tauri_plugin_store::StoreExt;
    app.store(store::STORE_FILE)
        .ok()
        .and_then(|s| s.get(store::SETTINGS_KEY))
        .unwrap_or_else(|| serde_json::json!({}))
}

/// Convert a batch of raw AX candidates (screen-space logical points) into
/// JSON blobs with normalised [0, 1] coordinates, ready for the sidecar.
///
/// The focus-capture side of the pipeline captures the whole primary display,
/// so the grounding layer normalises to the full-screen extents. AX also
/// reports coordinates in the screen's top-left origin — no window math
/// required here.
///
/// Logical (points), *not* physical (pixels), is the right denominator
/// because `AXUIElementCopyAttributeValue` returns values in points on
/// Retina displays. `monitor.size()` is physical pixels in Tauri 2, so we
/// divide by `scale_factor()` to recover the logical extents. Failing that
/// we fall back to a reasonable 1440×900 default so we at least emit *some*
/// normalisation rather than crash.
fn normalise_ax_candidates(
    app: &tauri::AppHandle,
    candidates: Vec<platform::AxCandidate>,
) -> serde_json::Value {
    if candidates.is_empty() {
        return serde_json::json!([]);
    }

    let (logical_w, logical_h) = logical_screen_size(app).unwrap_or((1440.0, 900.0));
    if logical_w <= 0.0 || logical_h <= 0.0 {
        return serde_json::json!([]);
    }

    let normalised: Vec<serde_json::Value> = candidates
        .into_iter()
        .map(|c| {
            let x = (c.x / logical_w).clamp(0.0, 1.0);
            let y = (c.y / logical_h).clamp(0.0, 1.0);
            let w = (c.width / logical_w).clamp(0.0, 1.0);
            let h = (c.height / logical_h).clamp(0.0, 1.0);
            serde_json::json!({
                "role": c.role,
                "title": c.title,
                "description": c.description,
                "x": x,
                "y": y,
                "width": w,
                "height": h,
            })
        })
        .collect();

    serde_json::Value::Array(normalised)
}

/// Return the primary monitor's logical (point) size as `(width, height)`,
/// or `None` when the monitor can't be queried. Used to normalise AX
/// candidates into the pipeline's [0, 1] coordinate space.
fn logical_screen_size(app: &tauri::AppHandle) -> Option<(f64, f64)> {
    let window = app.get_webview_window("overlay")?;
    let monitor = window.primary_monitor().ok().flatten()?;
    let physical = monitor.size();
    let scale = monitor.scale_factor();
    if scale <= 0.0 {
        return None;
    }
    let w = f64::from(physical.width) / scale;
    let h = f64::from(physical.height) / scale;
    Some((w, h))
}

// ──────────────────────────────────────────────────────────────────────────────
// Sidecar resolution

fn resolve_sidecar_dir(_handle: &tauri::AppHandle) -> error::AppResult<(PathBuf, PathBuf)> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repo_root = manifest_dir
        .parent()
        .ok_or_else(|| error::AppError::Sidecar("no parent of manifest dir".into()))?;
    let sidecar_dir = repo_root.join("sidecar");
    let uv = which_uv()?;
    Ok((uv, sidecar_dir))
}

fn which_uv() -> error::AppResult<PathBuf> {
    if let Ok(p) = std::env::var("OCH_UV_BINARY") {
        return Ok(PathBuf::from(p));
    }
    for dir in std::env::var_os("PATH")
        .as_ref()
        .map(|p| std::env::split_paths(p).collect::<Vec<_>>())
        .unwrap_or_default()
    {
        let candidate = dir.join("uv");
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    Err(error::AppError::Sidecar(
        "uv binary not found on PATH (set OCH_UV_BINARY to override)".into(),
    ))
}
