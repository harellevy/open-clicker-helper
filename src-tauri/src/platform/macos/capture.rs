//! Screen capture via macOS `screencapture` CLI.
//!
//! `screencapture -x -o` (no sound, no shadow) captures the primary display to
//! a temp PNG file.  This avoids wiring ScreenCaptureKit / CGWindowListCreateImage
//! from Rust for v1; a future iteration can swap this for `scap` to get
//! true per-window capture and avoid the disk I/O round-trip.

use std::process::Command;

use crate::error::{AppError, AppResult};
use crate::platform::ScreenCapture;

pub struct MacCapture;

impl ScreenCapture for MacCapture {
    fn capture_focused_window(&self) -> AppResult<Vec<u8>> {
        let tmp = std::env::temp_dir().join("och_capture.png");

        // -x  : suppress shutter sound
        // -o  : no window drop-shadow
        let status = Command::new("screencapture")
            .arg("-x")
            .arg("-o")
            .arg(&tmp)
            .status()
            .map_err(|e| AppError::Platform(format!("screencapture spawn: {e}")))?;

        if !status.success() {
            return Err(AppError::Platform(format!(
                "screencapture exited with {:?}",
                status.code()
            )));
        }

        let bytes =
            std::fs::read(&tmp).map_err(|e| AppError::Platform(format!("read screenshot: {e}")))?;

        let _ = std::fs::remove_file(&tmp); // best-effort cleanup
        Ok(bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mac_capture_is_constructible() {
        let _c = MacCapture;
    }
}
