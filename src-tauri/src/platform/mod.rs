//! Platform abstraction layer.
//!
//! All OS-specific code lives behind these traits so the eventual Windows
//! port only needs to add a sibling `windows/` module without touching the
//! Rust shell, the React UI, or the Python sidecar.

use tauri::WebviewWindow;

use crate::error::AppResult;

/// Permission status for a single TCC-style permission.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PermissionStatus {
    Granted,
    Denied,
    Unknown,
}

impl PermissionStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Granted => "granted",
            Self::Denied => "denied",
            Self::Unknown => "unknown",
        }
    }
}

pub trait Permissions {
    fn screen_recording(&self) -> PermissionStatus;
    fn accessibility(&self) -> PermissionStatus;
    fn microphone(&self) -> PermissionStatus;
}

pub trait OverlayWindow {
    /// Make the given window transparent, click-through, always on top across
    /// Spaces and fullscreen apps. macOS impl uses objc2 to set
    /// `kCGScreenSaverWindowLevel` and the right collection behavior.
    fn make_click_through_topmost(&self, window: &WebviewWindow) -> AppResult<()>;
}

/// Stub today; ScreenCaptureKit wrapper lands in P4.
pub trait ScreenCapture {
    fn capture_focused_window(&self) -> AppResult<Vec<u8>>;
}

/// Stub today; cpal-based recorder lands in P3.
pub trait MouseTracker {
    fn current_position(&self) -> AppResult<(i32, i32)>;
}

/// Aggregator returned by `current()`. Each method returns a fresh handle so
/// callers don't have to think about Send/Sync of OS objects.
pub trait Platform: Send + Sync {
    fn permissions(&self) -> Box<dyn Permissions>;
    fn overlay(&self) -> Box<dyn OverlayWindow>;
    fn capture(&self) -> Box<dyn ScreenCapture>;
    fn mouse(&self) -> Box<dyn MouseTracker>;
}

#[cfg(target_os = "macos")]
mod macos;

#[cfg(not(target_os = "macos"))]
mod stub;

pub fn current() -> &'static dyn Platform {
    #[cfg(target_os = "macos")]
    {
        &macos::MacOsPlatform
    }
    #[cfg(not(target_os = "macos"))]
    {
        &stub::StubPlatform
    }
}
