//! Platform abstraction layer.
//!
//! All OS-specific code lives behind these traits so the eventual Windows
//! port only needs to add a sibling `windows/` module without touching the
//! Rust shell, the React UI, or the Python sidecar.
//!
//! Most of the surface area below is scaffolding for capabilities that land
//! in later phases. Today only `permissions()` (via ipc) and `overlay()` (on
//! macOS) have callers — `capture()` and `mouse()` arrive in P3/P4. We keep
//! the trait surface stable and silence dead-code so CI stays green until
//! the callers land.

#![allow(dead_code)]

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

pub trait MouseTracker {
    fn current_position(&self) -> AppResult<(i32, i32)>;
    /// Synthesise a left-button click at the given screen coordinates (pixels).
    fn click(&self, x: f64, y: f64) -> AppResult<()>;
}

/// One candidate element from the focused app's Accessibility (AX) tree.
///
/// Coordinates are in **screen pixels** (matching `MouseTracker::click`), not
/// normalised. The grounding pipeline matches on `role`/`title`/`description`
/// text against the user's question and then clicks the centre of the chosen
/// candidate.
#[derive(Debug, Clone, serde::Serialize)]
pub struct AxCandidate {
    /// AX role (`AXButton`, `AXTextField`, `AXMenuItem`, …). Empty if unknown.
    pub role: String,
    /// Primary human-readable label (`AXTitle`). May be empty.
    pub title: String,
    /// Fallback label (`AXDescription` / `AXValue`). May be empty.
    pub description: String,
    /// Screen-space frame: `{x, y, width, height}` in pixels.
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl AxCandidate {
    /// Centre point of the element in screen pixels.
    pub fn centre(&self) -> (f64, f64) {
        (self.x + self.width / 2.0, self.y + self.height / 2.0)
    }
}

/// Walks the macOS Accessibility tree to enumerate clickable elements of the
/// focused app's focused window. Returns an empty Vec on platforms where no
/// AX API is available (Linux CI) or when the app doesn't expose one.
pub trait AxTree {
    /// Enumerate candidate elements in the focused window of the frontmost
    /// application. Implementations should cap tree depth to keep latency
    /// sub-millisecond on reasonable UIs.
    fn focused_window_candidates(&self) -> AppResult<Vec<AxCandidate>>;
}

/// Aggregator returned by `current()`. Each method returns a fresh handle so
/// callers don't have to think about Send/Sync of OS objects.
pub trait Platform: Send + Sync {
    fn permissions(&self) -> Box<dyn Permissions>;
    fn overlay(&self) -> Box<dyn OverlayWindow>;
    fn capture(&self) -> Box<dyn ScreenCapture>;
    fn mouse(&self) -> Box<dyn MouseTracker>;
    fn ax(&self) -> Box<dyn AxTree>;
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
