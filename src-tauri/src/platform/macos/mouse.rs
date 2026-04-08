//! Mouse position polling. P3 swaps the polling loop in for an event tap.

use core_graphics::event::CGEvent;
use core_graphics::event_source::{CGEventSource, CGEventSourceStateID};

use crate::error::{AppError, AppResult};
use crate::platform::MouseTracker;

pub struct MacMouse;

impl MouseTracker for MacMouse {
    fn current_position(&self) -> AppResult<(i32, i32)> {
        let source = CGEventSource::new(CGEventSourceStateID::HIDSystemState)
            .map_err(|_| AppError::Platform("CGEventSource::new failed".into()))?;
        let event =
            CGEvent::new(source).map_err(|_| AppError::Platform("CGEvent::new failed".into()))?;
        let p = event.location();
        Ok((p.x as i32, p.y as i32))
    }
}
