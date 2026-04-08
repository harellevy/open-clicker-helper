//! TCC permission probes. We deliberately do *not* trigger the permission
//! prompts here — that happens at the moment we actually need each capability
//! (mic on first record, screen on first capture). These probes only report
//! the current state for the settings UI.

use core_graphics::access::ScreenCaptureAccess;

use crate::platform::{PermissionStatus, Permissions};

#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    /// AXIsProcessTrusted returns whether the current process is in the
    /// Accessibility allow-list. It does NOT prompt the user; that variant is
    /// `AXIsProcessTrustedWithOptions(kAXTrustedCheckOptionPrompt: true)`.
    fn AXIsProcessTrusted() -> bool;
}

pub struct MacPermissions;

impl Permissions for MacPermissions {
    fn screen_recording(&self) -> PermissionStatus {
        // `preflight` does not prompt; `request` would.
        if ScreenCaptureAccess.preflight() {
            PermissionStatus::Granted
        } else {
            PermissionStatus::Denied
        }
    }

    fn accessibility(&self) -> PermissionStatus {
        // SAFETY: AXIsProcessTrusted has no preconditions and no side effects.
        if unsafe { AXIsProcessTrusted() } {
            PermissionStatus::Granted
        } else {
            PermissionStatus::Denied
        }
    }

    fn microphone(&self) -> PermissionStatus {
        // P3 will probe the real status by lazy-loading AVFoundation through
        // the cpal stack we use to record. Until we actually need the mic we
        // refuse to add an AVFoundation link dependency just for a status badge.
        PermissionStatus::Unknown
    }
}
