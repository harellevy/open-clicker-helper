use serde::{Deserialize, Serialize};
use serde_json::json;
use tauri::State;

use crate::error::{AppError, AppResult};
use crate::platform::{self, Permissions as PermTrait};
use crate::AppState;

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
