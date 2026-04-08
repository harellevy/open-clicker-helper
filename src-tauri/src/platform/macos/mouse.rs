//! Mouse position polling and click synthesis via CoreGraphics.

use core_graphics::event::{CGEvent, CGEventType, CGMouseButton};
use core_graphics::event_source::{CGEventSource, CGEventSourceStateID};
use core_graphics::geometry::CGPoint;

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

    fn click(&self, x: f64, y: f64) -> AppResult<()> {
        let source = CGEventSource::new(CGEventSourceStateID::HIDSystemState)
            .map_err(|_| AppError::Platform("CGEventSource for click failed".into()))?;
        let pos = CGPoint { x, y };

        let down = CGEvent::new_mouse_event(
            source.clone(),
            CGEventType::LeftMouseDown,
            pos,
            CGMouseButton::Left,
        )
        .map_err(|_| AppError::Platform("mouse down event failed".into()))?;

        let up =
            CGEvent::new_mouse_event(source, CGEventType::LeftMouseUp, pos, CGMouseButton::Left)
                .map_err(|_| AppError::Platform("mouse up event failed".into()))?;

        down.post(core_graphics::event::CGEventTapLocation::HID);
        // Brief pause between down and up for compatibility with some apps.
        std::thread::sleep(std::time::Duration::from_millis(30));
        up.post(core_graphics::event::CGEventTapLocation::HID);

        Ok(())
    }
}
