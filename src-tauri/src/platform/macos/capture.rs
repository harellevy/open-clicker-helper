//! Screen capture stub. P4 wires this to `scap` (ScreenCaptureKit).

#![allow(dead_code)]

use crate::error::{AppError, AppResult};
use crate::platform::ScreenCapture;

pub struct MacCapture;

impl ScreenCapture for MacCapture {
    fn capture_focused_window(&self) -> AppResult<Vec<u8>> {
        Err(AppError::Platform(
            "capture not yet implemented (lands in P4 with scap)".into(),
        ))
    }
}
