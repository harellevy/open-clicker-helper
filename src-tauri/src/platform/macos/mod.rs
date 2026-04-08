//! macOS implementation of the platform traits.

use crate::platform::{MouseTracker, OverlayWindow, Permissions, Platform, ScreenCapture};

mod capture;
mod mouse;
mod permissions;
mod window;

pub struct MacOsPlatform;

impl Platform for MacOsPlatform {
    fn permissions(&self) -> Box<dyn Permissions> {
        Box::new(permissions::MacPermissions)
    }
    fn overlay(&self) -> Box<dyn OverlayWindow> {
        Box::new(window::MacOverlay)
    }
    fn capture(&self) -> Box<dyn ScreenCapture> {
        Box::new(capture::MacCapture)
    }
    fn mouse(&self) -> Box<dyn MouseTracker> {
        Box::new(mouse::MacMouse)
    }
}
