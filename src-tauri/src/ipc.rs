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
