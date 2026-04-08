//! TCC permission probes. We deliberately do *not* trigger the permission
//! prompts here — that happens at the moment we actually need each capability
//! (mic on first record, screen on first capture). These probes only report
//! the current state for the settings UI.

use core_foundation::base::TCFType;
use core_foundation::string::{CFString, CFStringRef};
use core_graphics::access::ScreenCaptureAccess;

use crate::platform::{PermissionStatus, Permissions};

pub struct MacPermissions;

impl Permissions for MacPermissions {
    fn screen_recording(&self) -> PermissionStatus {
        // CGPreflightScreenCaptureAccess does not prompt; CGRequestScreenCaptureAccess would.
        if ScreenCaptureAccess.preflight() {
            PermissionStatus::Granted
        } else {
            PermissionStatus::Denied
        }
    }

    fn accessibility(&self) -> PermissionStatus {
        // AXIsProcessTrustedWithOptions with prompt=false.
        unsafe {
            extern "C" {
                fn AXIsProcessTrustedWithOptions(
                    options: *const core_foundation::dictionary::__CFDictionary,
                ) -> bool;
            }
            // Pass NULL options → no prompt, just probe.
            let trusted = AXIsProcessTrustedWithOptions(std::ptr::null());
            if trusted {
                PermissionStatus::Granted
            } else {
                PermissionStatus::Denied
            }
        }
    }

    fn microphone(&self) -> PermissionStatus {
        // AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio.
        // 0 = notDetermined, 1 = restricted, 2 = denied, 3 = authorized.
        unsafe {
            extern "C" {
                fn AVCaptureDeviceAuthorizationStatusForMediaType(media_type: CFStringRef) -> i64;
            }
            let media_type = CFString::new("soun");
            // The runtime-correct value is `AVMediaTypeAudio` ("soun" four-char-code wrapped
            // in an NSString). We resolve via dlsym at runtime in P3 when we actually link
            // AVFoundation; until then we conservatively return Unknown.
            let _ = media_type;
            let _ = AVCaptureDeviceAuthorizationStatusForMediaType;
            PermissionStatus::Unknown
        }
    }
}
