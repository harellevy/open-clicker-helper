//! open-clicker-helper — Tauri shell.
//!
//! Boundaries:
//! - `platform/` holds OS-specific code behind traits so the Windows port
//!   later only adds a sibling module.
//! - `ipc` exposes `tauri::command`s consumed by the React frontend.
//! - `sidecar` will spawn the Python AI service over stdio JSON-RPC (P1).

mod error;
mod ipc;
mod platform;

use tauri::Manager;
use tracing_subscriber::EnvFilter;

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
        .invoke_handler(tauri::generate_handler![
            ipc::get_permissions,
            ipc::ping_sidecar,
        ])
        .setup(|app| {
            // Configure the transparent overlay window with native macOS flags
            // (joinAllSpaces, fullScreenAuxiliary, screen-saver level, ignore
            // mouse events). Tauri's window options alone are not sufficient.
            if let Some(overlay) = app.get_webview_window("overlay") {
                #[cfg(target_os = "macos")]
                {
                    use crate::platform::OverlayWindow;
                    let configurator = platform::current().overlay();
                    if let Err(e) = configurator.make_click_through_topmost(&overlay) {
                        tracing::warn!("overlay configuration failed: {e}");
                    }
                }
                // P0: keep the overlay hidden until P4 needs it.
                let _ = overlay.hide();
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
