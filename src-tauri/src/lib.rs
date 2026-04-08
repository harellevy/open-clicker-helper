//! open-clicker-helper — Tauri shell.
//!
//! Boundaries:
//! - `platform/` holds OS-specific code behind traits so the Windows port
//!   later only adds a sibling module.
//! - `ipc` exposes `tauri::command`s consumed by the React frontend.
//! - `sidecar` spawns the Python AI service over stdio JSON-RPC.
//! - `store` defines the settings types persisted via tauri-plugin-store.

mod error;
mod ipc;
mod platform;
mod sidecar;
mod store;

use std::path::PathBuf;
use std::sync::Arc;

use tauri::{Emitter, Manager};
use tracing_subscriber::EnvFilter;

use crate::sidecar::Sidecar;

/// Shared application state held by Tauri's manager.
pub struct AppState {
    pub sidecar: tokio::sync::Mutex<Option<Arc<Sidecar>>>,
}

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
        })
        .invoke_handler(tauri::generate_handler![
            ipc::get_permissions,
            ipc::open_system_settings,
            ipc::get_settings,
            ipc::save_settings,
            ipc::ping_sidecar,
            ipc::sidecar_call,
        ])
        .setup(|app| {
            // Configure the transparent overlay window with native macOS flags
            // (joinAllSpaces, fullScreenAuxiliary, screen-saver level, ignore
            // mouse events). Tauri's window options alone are not sufficient.
            if let Some(overlay) = app.get_webview_window("overlay") {
                #[cfg(target_os = "macos")]
                {
                    let configurator = platform::current().overlay();
                    if let Err(e) = configurator.make_click_through_topmost(&overlay) {
                        tracing::warn!("overlay configuration failed: {e}");
                    }
                }
                // Keep the overlay hidden until P4 needs it.
                let _ = overlay.hide();
            }

            // Spawn the Python sidecar in the background, then start the
            // progress-notification relay so sidecar events (downloads, pipeline
            // stages) flow to the React frontend as `sidecar://progress` events.
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                match resolve_sidecar_dir(&handle) {
                    Ok((uv, dir)) => match Sidecar::spawn(&uv, &dir).await {
                        Ok(sc) => {
                            tracing::info!("sidecar spawned (uv={uv:?}, dir={dir:?})");

                            // Start progress relay before storing the sidecar so
                            // we never miss an early notification.
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

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Resolve the path to the bundled `uv` binary and the `sidecar/` project dir.
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
