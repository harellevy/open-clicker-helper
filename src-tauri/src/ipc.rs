use serde::Serialize;

use crate::error::AppResult;
use crate::platform::{self, Permissions as PermTrait};

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

#[derive(Debug, Serialize, Clone)]
pub struct SidecarHealth {
    pub ok: bool,
    pub version: Option<String>,
}

#[tauri::command]
pub fn ping_sidecar() -> AppResult<SidecarHealth> {
    // P1 wires this to the real sidecar over stdio JSON-RPC. P0 returns a stub
    // so the settings page renders without errors.
    Ok(SidecarHealth {
        ok: false,
        version: None,
    })
}
