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
            ipc::ping_sidecar,
            ipc::sidecar_call,
            ipc::capture_screen,
            ipc::click_at_normalized,
        ])
        .setup(|app| {
            // Configure the transparent overlay window with native macOS flags.
            if let Some(overlay) = app.get_webview_window("overlay") {
                // Explicitly clear the WebView background so it doesn't
                // default to white even when `transparent: true` is set.
                let _ = overlay.set_background_color(Some(Color(0, 0, 0, 0)));

                // DEBUG: bottom-half only so the screen is still usable.
                // TODO(P5): restore full-screen once click-through is stable.
                if let Some(monitor) = overlay.primary_monitor().ok().flatten() {
                    let size = monitor.size();
                    let half_h = size.height / 2;
                    let _ = overlay.set_size(tauri::Size::Physical(tauri::PhysicalSize {
                        width: size.width,
                        height: half_h,
                    }));
                    let _ =
                        overlay.set_position(tauri::Position::Physical(tauri::PhysicalPosition {
                            x: 0,
                            y: half_h as i32,
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
            let handle = app.clone();
            match event.state {
                ShortcutState::Pressed => {
                    tauri::async_runtime::spawn(async move {
                        on_press(&handle).await;
                    });
                }
                ShortcutState::Released => {
                    tauri::async_runtime::spawn(async move {
                        on_release(&handle).await;
                    });
                }
            }
        },
    );

    match result {
        Ok(()) => tracing::info!("hotkey registered: {accelerator}"),
        Err(e) => tracing::warn!("hotkey registration failed: {e}"),
    }
}

async fn on_press(app: &tauri::AppHandle) {
    let state = app.state::<AppState>();
    let mut rec = state.recorder.lock().await;
    if rec.is_some() {
        return; // already recording
    }
    match audio::AudioRecorder::start() {
        Ok(recorder) => {
            *rec = Some(recorder);
            let _ = app.emit("hotkey-state", HotkeyState::Recording);
            tracing::debug!("recording started");
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

async fn on_release(app: &tauri::AppHandle) {
    let state = app.state::<AppState>();
    let recorder = state.recorder.lock().await.take();
    let Some(recorder) = recorder else { return };

    let _ = app.emit("hotkey-state", HotkeyState::Processing);

    // Encode WAV in a blocking task (stop_and_encode calls mpsc::recv).
    let wav_bytes = match tokio::task::spawn_blocking(move || recorder.stop_and_encode()).await {
        Ok(Ok(b)) => b,
        Ok(Err(e)) => {
            tracing::error!("encode audio: {e}");
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
        .unwrap_or(serde_json::Value::Null)
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
