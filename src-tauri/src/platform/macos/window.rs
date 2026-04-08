//! Native overlay window configuration via objc2.
//!
//! Tauri's `transparent: true` + `alwaysOnTop: true` flags only get us a
//! borderless transparent window that floats over the active Space. To make a
//! true overlay we need to set:
//!
//! - `level = kCGScreenSaverWindowLevel` so we're above fullscreen apps,
//! - `collectionBehavior = canJoinAllSpaces | fullScreenAuxiliary | stationary`
//!   so we follow the user across Spaces and into fullscreen apps,
//! - `ignoresMouseEvents = true` so clicks pass through.
//!
//! `setIgnoreCursorEvents` from Tauri only handles the third bullet — and on
//! some macOS versions it can revert when the WebView reflows. We re-apply
//! everything from Rust at startup.

use objc2::msg_send;
use objc2::runtime::AnyObject;
use tauri::WebviewWindow;

use crate::error::{AppError, AppResult};
use crate::platform::OverlayWindow;

// From <CoreGraphics/CGWindowLevel.h>. kCGScreenSaverWindowLevel = 1000.
const SCREEN_SAVER_WINDOW_LEVEL: i64 = 1000;

// NSWindowCollectionBehavior bitmask values from <AppKit/NSWindow.h>.
const NS_COLLECTION_BEHAVIOR_CAN_JOIN_ALL_SPACES: u64 = 1 << 0;
const NS_COLLECTION_BEHAVIOR_STATIONARY: u64 = 1 << 4;
const NS_COLLECTION_BEHAVIOR_FULLSCREEN_AUXILIARY: u64 = 1 << 8;
const NS_COLLECTION_BEHAVIOR_IGNORES_CYCLE: u64 = 1 << 6;

pub struct MacOverlay;

impl OverlayWindow for MacOverlay {
    fn make_click_through_topmost(&self, window: &WebviewWindow) -> AppResult<()> {
        // Tauri's setIgnoreCursorEvents handles the click-through bit on the
        // *content view*. We still need it for newer macOS releases.
        window
            .set_ignore_cursor_events(true)
            .map_err(|e| AppError::Platform(format!("set_ignore_cursor_events: {e}")))?;

        let ns_window: *mut AnyObject = window
            .ns_window()
            .map_err(|e| AppError::Platform(format!("ns_window: {e}")))?
            as *mut AnyObject;

        if ns_window.is_null() {
            return Err(AppError::Platform("ns_window returned null".into()));
        }

        unsafe {
            // setLevel:
            let _: () = msg_send![ns_window, setLevel: SCREEN_SAVER_WINDOW_LEVEL];

            // setCollectionBehavior:
            let behavior: u64 = NS_COLLECTION_BEHAVIOR_CAN_JOIN_ALL_SPACES
                | NS_COLLECTION_BEHAVIOR_STATIONARY
                | NS_COLLECTION_BEHAVIOR_FULLSCREEN_AUXILIARY
                | NS_COLLECTION_BEHAVIOR_IGNORES_CYCLE;
            let _: () = msg_send![ns_window, setCollectionBehavior: behavior];

            // Belt-and-braces: ignoresMouseEvents on the NSWindow itself.
            let _: () = msg_send![ns_window, setIgnoresMouseEvents: true];

            // Don't show in window list / Mission Control / cmd-tab.
            let _: () = msg_send![ns_window, setHidesOnDeactivate: false];

            // True transparency: WKWebView paints its own white background
            // independently of the NSWindow transparency setting.
            let _: () = msg_send![ns_window, setOpaque: false];

            // wry wraps the real WKWebView inside a WryWebViewParent container.
            // contentView returns that parent; we need to walk one level into its
            // subviews to reach the actual WKWebView, which has drawsBackground.
            // Guard with respondsToSelector: so a wry refactor won't crash us.
            let content_view: *mut AnyObject = msg_send![ns_window, contentView];
            if !content_view.is_null() {
                let subviews: *mut AnyObject = msg_send![content_view, subviews];
                let count: usize = msg_send![subviews, count];
                for i in 0..count {
                    let subview: *mut AnyObject = msg_send![subviews, objectAtIndex: i];
                    if subview.is_null() {
                        continue;
                    }
                    // Only send setDrawsBackground: if the view actually supports it.
                    let sel = objc2::runtime::Sel::register("setDrawsBackground:");
                    let responds: bool = msg_send![subview, respondsToSelector: sel];
                    if responds {
                        let _: () = msg_send![subview, setDrawsBackground: false];
                        break;
                    }
                }
            }
        }

        tracing::info!("overlay window configured for click-through topmost");
        Ok(())
    }
}
