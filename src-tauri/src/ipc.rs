use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::State;

use crate::error::{AppError, AppResult};
use crate::platform;
use crate::store::{Settings, SETTINGS_KEY, STORE_FILE};
use crate::AppState;

// ──────────────────────────────────────────────────────────────────────────────
// Permissions
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize, Clone)]
pub struct Permissions {
    pub screen_recording: &'static str,
    pub accessibility: &'static str,
    pub microphone: &'static str,
}

#[tauri::command]
pub fn get_permissions() -> AppResult<Permissions> {
    let p = platform::current().permissions();
    Ok(Permissions {
        screen_recording: p.screen_recording().as_str(),
        accessibility: p.accessibility().as_str(),
        microphone: p.microphone().as_str(),
    })
}

/// Open a specific System Settings pane.
///
/// `pane` values: `"screen_recording"`, `"accessibility"`, `"microphone"`.
///
/// `Shell::open` is deprecated in tauri-plugin-shell ≥2.1 in favour of
/// `tauri-plugin-opener`, but we don't need the full opener plugin just for
/// macOS System-Settings deep links. Suppress the deprecation lint here; we
/// will migrate to the opener plugin in P5.
#[allow(deprecated)]
#[tauri::command]
pub fn open_system_settings(app: tauri::AppHandle, pane: String) -> AppResult<()> {
    use tauri_plugin_shell::ShellExt;
    let url = match pane.as_str() {
        "screen_recording" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
        }
        "accessibility" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        }
        "microphone" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        }
        other => {
            return Err(AppError::Platform(format!(
                "unknown settings pane: {other}"
            )));
        }
    };
    app.shell()
        .open(url, None)
        .map_err(|e| AppError::Platform(format!("open system settings: {e}")))?;
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Settings store
// ──────────────────────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_settings(app: tauri::AppHandle) -> AppResult<Settings> {
    use tauri_plugin_store::StoreExt;
    let store = app
        .store(STORE_FILE)
        .map_err(|e| AppError::Sidecar(format!("store open: {e}")))?;
    match store.get(SETTINGS_KEY) {
        Some(v) => serde_json::from_value(v).map_err(AppError::from),
        None => Ok(Settings::default()),
    }
}

#[tauri::command]
pub fn save_settings(app: tauri::AppHandle, settings: Settings) -> AppResult<()> {
    use tauri_plugin_store::StoreExt;
    let store = app
        .store(STORE_FILE)
        .map_err(|e| AppError::Sidecar(format!("store open: {e}")))?;
    store.set(
        SETTINGS_KEY,
        serde_json::to_value(&settings).map_err(AppError::from)?,
    );
    store
        .save()
        .map_err(|e| AppError::Sidecar(format!("store save: {e}")))?;
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Sidecar bridge
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SidecarHealth {
    pub ok: bool,
    pub version: Option<String>,
}

#[tauri::command]
pub async fn ping_sidecar(state: State<'_, AppState>) -> AppResult<SidecarHealth> {
    let guard = state.sidecar.lock().await;
    let Some(sidecar) = guard.as_ref().cloned() else {
        return Ok(SidecarHealth {
            ok: false,
            version: None,
        });
    };
    drop(guard);

    let value = sidecar.call("ping", json!({})).await?;
    let parsed: SidecarHealth = serde_json::from_value(value).map_err(AppError::from)?;
    Ok(parsed)
}

/// Capture the primary display and return it as a base-64-encoded PNG.
///
/// Returns `None` on non-macOS platforms or when screen recording permission
/// has not been granted.
#[tauri::command]
pub fn capture_screen() -> AppResult<Option<String>> {
    match platform::current().capture().capture_focused_window() {
        Ok(png) => Ok(Some(base64::engine::general_purpose::STANDARD.encode(&png))),
        Err(e) => {
            tracing::warn!("capture_screen: {e}");
            Ok(None)
        }
    }
}

/// Synthesise a left-click at normalised coordinates (0.0–1.0) on the primary
/// monitor.  Converts to physical pixels, then posts CGEvent mouse-down/up.
///
/// The overlay must be click-through for the synthesised events to reach the
/// target application — this is guaranteed by `make_click_through_topmost`.
#[tauri::command]
pub async fn click_at_normalized(app: tauri::AppHandle, x: f64, y: f64) -> AppResult<()> {
    // Get primary monitor size for coordinate conversion.
    // We use a temporary webview window handle to access monitor info.
    let (px, py) = if let Some(window) = app.get_webview_window("overlay") {
        if let Ok(Some(monitor)) = window.primary_monitor() {
            let size = monitor.size();
            (
                (x.clamp(0.0, 1.0) * size.width as f64).round(),
                (y.clamp(0.0, 1.0) * size.height as f64).round(),
            )
        } else {
            (x * 1440.0, y * 900.0) // fallback
        }
    } else {
        (x * 1440.0, y * 900.0) // fallback
    };

    tracing::info!("click_at_normalized ({x:.3}, {y:.3}) → pixels ({px}, {py})");

    // Run the blocking CGEvent calls off the async executor.
    tokio::task::spawn_blocking(move || platform::current().mouse().click(px, py))
        .await
        .map_err(|e| AppError::Platform(format!("spawn_blocking click: {e}")))?
}

/// Generic JSON-RPC pass-through to the Python sidecar.
///
/// Progress notifications emitted by long-running handlers (e.g.
/// `setup.download_vlm`) are forwarded to all windows as
/// `sidecar://progress` Tauri events by the relay task started in
/// `lib::run()`.
#[tauri::command]
pub async fn sidecar_call(
    state: State<'_, AppState>,
    method: String,
    params: Value,
) -> AppResult<Value> {
    let guard = state.sidecar.lock().await;
    let Some(sidecar) = guard.as_ref().cloned() else {
        return Err(AppError::Sidecar("sidecar is not running".into()));
    };
    drop(guard);
    sidecar.call(&method, params).await
}
