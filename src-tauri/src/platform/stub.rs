//! Stub platform impl used when building on non-macOS hosts (e.g. Linux CI
//! `cargo check`). The Windows impl will replace this file once we add the
//! `windows/` module.

#![allow(dead_code)]

use tauri::WebviewWindow;

use crate::error::{AppError, AppResult};
use crate::platform::{
    AxCandidate, AxTree, MouseTracker, OverlayWindow, PermissionStatus, Permissions, Platform,
    ScreenCapture,
};

pub struct StubPlatform;

impl Platform for StubPlatform {
    fn permissions(&self) -> Box<dyn Permissions> {
        Box::new(StubPermissions)
    }
    fn overlay(&self) -> Box<dyn OverlayWindow> {
        Box::new(StubOverlay)
    }
    fn capture(&self) -> Box<dyn ScreenCapture> {
        Box::new(StubCapture)
    }
    fn mouse(&self) -> Box<dyn MouseTracker> {
        Box::new(StubMouse)
    }
    fn ax(&self) -> Box<dyn AxTree> {
        Box::new(StubAx)
    }
}

struct StubPermissions;
impl Permissions for StubPermissions {
    fn screen_recording(&self) -> PermissionStatus {
        PermissionStatus::Unknown
    }
    fn accessibility(&self) -> PermissionStatus {
        PermissionStatus::Unknown
    }
    fn microphone(&self) -> PermissionStatus {
        PermissionStatus::Unknown
    }
}

struct StubOverlay;
impl OverlayWindow for StubOverlay {
    fn make_click_through_topmost(&self, _window: &WebviewWindow) -> AppResult<()> {
        Ok(())
    }
}

struct StubCapture;
impl ScreenCapture for StubCapture {
    fn capture_focused_window(&self) -> AppResult<Vec<u8>> {
        Err(AppError::Platform(
            "screen capture not implemented on this platform".into(),
        ))
    }
}

struct StubMouse;
impl MouseTracker for StubMouse {
    fn current_position(&self) -> AppResult<(i32, i32)> {
        Ok((0, 0))
    }

    fn click(&self, _x: f64, _y: f64) -> AppResult<()> {
        Ok(()) // no-op on non-macOS
    }
}

struct StubAx;
impl AxTree for StubAx {
    fn focused_window_candidates(&self) -> AppResult<Vec<AxCandidate>> {
        // No Accessibility API off macOS — return nothing and let the
        // grounding pipeline fall through to VLM.
        Ok(Vec::new())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stub_ax_returns_empty() {
        let ax = StubAx;
        let v = ax.focused_window_candidates().unwrap();
        assert!(v.is_empty());
    }
}
