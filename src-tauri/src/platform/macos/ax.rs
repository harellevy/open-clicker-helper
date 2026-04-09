//! macOS Accessibility (AX) tree walker.
//!
//! Queries the system-wide AX element for the focused application, then its
//! focused window, and walks the descendant tree collecting clickable
//! elements. Each candidate carries its AX role plus any human-readable
//! label, so the grounding pipeline can match the user's question against
//! on-screen text without invoking a VLM.
//!
//! This path requires the user to have granted the app Accessibility
//! permission (System Settings → Privacy & Security → Accessibility).
//! Without it all `AXUIElementCopy*` calls return
//! `kAXErrorCannotComplete`/`kAXErrorAPIDisabled` and we silently fall
//! through to the VLM grounding path.
//!
//! Implementation notes:
//! * We talk to the raw `accessibility-sys` FFI and build `CFString` /
//!   `AXValue` wrappers ourselves. The `accessibility` crate has higher-level
//!   sugar but pulls in a duplicate cocoa/objc stack alongside our objc2 —
//!   not worth it for the ~200 lines of walker code here.
//! * The walker is depth- and breadth-capped (`MAX_DEPTH`, `MAX_CANDIDATES`)
//!   so a pathological app with thousands of nested cells can't blow the
//!   call budget or the latency target.

use std::ffi::c_void;

use accessibility_sys::{
    kAXChildrenAttribute, kAXDescriptionAttribute, kAXFocusedApplicationAttribute,
    kAXFocusedWindowAttribute, kAXPositionAttribute, kAXRoleAttribute, kAXSizeAttribute,
    kAXTitleAttribute, kAXValueAttribute, kAXValueTypeCGPoint, kAXValueTypeCGSize,
    AXUIElementCopyAttributeValue, AXUIElementCreateSystemWide, AXUIElementGetTypeID,
    AXUIElementRef, AXValueGetTypeID, AXValueGetValue, AXValueRef,
};
use core_foundation::{
    array::CFArray,
    base::{CFType, TCFType},
    string::CFString,
};
use core_foundation_sys::{
    array::{CFArrayGetTypeID, CFArrayRef},
    base::{CFGetTypeID, CFRelease, CFTypeRef},
    string::{CFStringGetTypeID, CFStringRef},
};

use crate::error::AppResult;
use crate::platform::{AxCandidate, AxTree};

/// Maximum tree depth to walk. macOS apps rarely nest deeper than ~20 for
/// clickable controls; anything deeper is almost certainly a text container
/// we don't care about for click grounding.
const MAX_DEPTH: usize = 15;

/// Hard cap on collected candidates. Keeps the downstream grounding prompt
/// small and bounds worst-case latency on pages like a massive table view.
const MAX_CANDIDATES: usize = 256;

/// Roles the clicker cares about. Everything else (groups, static text, …)
/// is walked for its children but not returned as a candidate on its own.
///
/// This list is intentionally conservative — callers that want fuzzier
/// matches can bypass it by running the walker through a custom predicate
/// later.
const CLICKABLE_ROLES: &[&str] = &[
    "AXButton",
    "AXCheckBox",
    "AXRadioButton",
    "AXPopUpButton",
    "AXMenuItem",
    "AXMenuBarItem",
    "AXTextField",
    "AXTextArea",
    "AXSearchField",
    "AXComboBox",
    "AXLink",
    "AXCell",
    "AXRow",
    "AXTab",
    "AXDisclosureTriangle",
    "AXSlider",
    "AXStaticText", // some apps (web views) mark labels as StaticText but they're the click target
    "AXImage",      // toolbar icons
];

pub struct MacAx;

impl AxTree for MacAx {
    fn focused_window_candidates(&self) -> AppResult<Vec<AxCandidate>> {
        // SAFETY: all AX calls below are memory-safe provided we honour the
        // "get = borrow, copy = own" rule from the Apple docs — every
        // `copy_*` result is released exactly once before we return.
        unsafe { Ok(walk_focused_window()) }
    }
}

unsafe fn walk_focused_window() -> Vec<AxCandidate> {
    let system_wide = AXUIElementCreateSystemWide();
    if system_wide.is_null() {
        return Vec::new();
    }

    let mut out = Vec::new();

    if let Some(app_ref) = copy_attribute_of_type(
        system_wide,
        kAXFocusedApplicationAttribute,
        AXUIElementGetTypeID(),
    ) {
        if let Some(window_ref) = copy_attribute_of_type(
            app_ref as AXUIElementRef,
            kAXFocusedWindowAttribute,
            AXUIElementGetTypeID(),
        ) {
            walk(window_ref as AXUIElementRef, 0, &mut out);
            CFRelease(window_ref);
        }
        CFRelease(app_ref);
    }

    CFRelease(system_wide as CFTypeRef);
    out
}

/// Recursive DFS over the AX subtree rooted at `element`.
///
/// `element` is borrowed — the caller owns the release. Children we copy
/// here are released before we return.
///
/// SAFETY: requires `element` to be a valid, non-null AXUIElementRef.
unsafe fn walk(element: AXUIElementRef, depth: usize, out: &mut Vec<AxCandidate>) {
    if depth >= MAX_DEPTH || out.len() >= MAX_CANDIDATES {
        return;
    }

    let role = copy_string(element, kAXRoleAttribute).unwrap_or_default();

    // Only emit a candidate if this element is clickable *and* we can
    // resolve a frame. Non-clickable containers still get recursed into.
    if CLICKABLE_ROLES.contains(&role.as_str()) {
        if let Some((x, y, w, h)) = copy_frame(element) {
            // Some AX implementations hand us degenerate 0x0 frames for
            // offscreen or hidden elements — skip those, they'd click on
            // nothing useful.
            if w > 0.0 && h > 0.0 {
                let title = copy_string(element, kAXTitleAttribute).unwrap_or_default();
                // Prefer AXDescription; fall back to AXValue (some
                // menu items expose their label via AXValue only).
                let description = match copy_string(element, kAXDescriptionAttribute) {
                    Some(d) => d,
                    None => copy_string(element, kAXValueAttribute).unwrap_or_default(),
                };

                out.push(AxCandidate {
                    role,
                    title,
                    description,
                    x,
                    y,
                    width: w,
                    height: h,
                });
            }
        }
    }

    // Recurse into children (owned CFArray of AXUIElementRef). Ownership
    // transfers to the `CFArray` wrapper which releases on drop, so we must
    // *not* also CFRelease the raw ptr. The type check guards against
    // ill-behaved apps that hand back something other than a CFArray for
    // kAXChildrenAttribute — calling CFArrayGetCount on a non-array throws
    // an ObjC exception that would abort the whole Rust process.
    if let Some(children_ref) =
        copy_attribute_of_type(element, kAXChildrenAttribute, CFArrayGetTypeID())
    {
        let arr: CFArray<CFType> = CFArray::wrap_under_create_rule(children_ref as CFArrayRef);
        for item in arr.iter() {
            if out.len() >= MAX_CANDIDATES {
                break;
            }
            let child = item.as_CFTypeRef() as AXUIElementRef;
            if !child.is_null() && CFGetTypeID(child as CFTypeRef) == AXUIElementGetTypeID() {
                walk(child, depth + 1, out);
            }
        }
    }
}

/// Copy a `CFTypeRef`-valued attribute from an AX element. Returns `None`
/// when the attribute is missing, the call errors, or the value is null.
///
/// The returned pointer is **owned by the caller** — it must be released
/// (either directly via `CFRelease` or by wrapping in a TCFType with
/// `wrap_under_create_rule`).
///
/// SAFETY: `element` must be a valid AXUIElementRef.
unsafe fn copy_attribute(element: AXUIElementRef, attr: &str) -> Option<CFTypeRef> {
    let name = CFString::new(attr);
    let mut value: CFTypeRef = std::ptr::null();
    let err = AXUIElementCopyAttributeValue(element, name.as_concrete_TypeRef(), &mut value);
    if err != 0 || value.is_null() {
        None
    } else {
        Some(value)
    }
}

/// Like [`copy_attribute`], but also verifies the returned `CFTypeRef` has the
/// expected Core Foundation type ID. If the attribute resolves to something of
/// the wrong type (e.g. `kAXValueAttribute` returning a number when the caller
/// expects a `CFString`), we `CFRelease` the value and return `None`.
///
/// This is the linchpin of the walker's crash safety: Core Foundation / ObjC
/// APIs throw exceptions on type mismatches, and Rust cannot catch foreign
/// exceptions — they immediately abort the process. Validating up front means
/// we never feed a wrong-typed pointer into `CFString::to_string`,
/// `CFArrayGetCount`, or `AXValueGetValue`.
///
/// SAFETY: `element` must be a valid AXUIElementRef.
unsafe fn copy_attribute_of_type(
    element: AXUIElementRef,
    attr: &str,
    expected: core_foundation_sys::base::CFTypeID,
) -> Option<CFTypeRef> {
    let raw = copy_attribute(element, attr)?;
    if CFGetTypeID(raw) == expected {
        Some(raw)
    } else {
        CFRelease(raw);
        None
    }
}

/// Copy a string-valued attribute as an owned Rust `String`. Returns `None`
/// on any failure (missing attribute, wrong type, …) so callers can chain
/// with `.or_else`.
///
/// SAFETY: `element` must be a valid AXUIElementRef.
unsafe fn copy_string(element: AXUIElementRef, attr: &str) -> Option<String> {
    // Guard against type mismatch: kAXValueAttribute in particular may return
    // numbers, bools, dicts, or AXValues depending on the element, and calling
    // CFString methods on a non-string CFType would throw an ObjC exception.
    let raw = copy_attribute_of_type(element, attr, CFStringGetTypeID())?;
    // wrap_under_create_rule transfers ownership so the string is released
    // when we drop it.
    let s = CFString::wrap_under_create_rule(raw as CFStringRef);
    let out = s.to_string();
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

/// Read `AXPosition` + `AXSize` (each is an `AXValue` wrapping a CGPoint /
/// CGSize) and return `(x, y, w, h)` in screen pixels.
///
/// SAFETY: `element` must be a valid AXUIElementRef.
unsafe fn copy_frame(element: AXUIElementRef) -> Option<(f64, f64, f64, f64)> {
    // CGPoint / CGSize on 64-bit macOS are two `f64` fields each.
    #[repr(C)]
    struct CGPoint {
        x: f64,
        y: f64,
    }
    #[repr(C)]
    struct CGSize {
        width: f64,
        height: f64,
    }

    // AXValueGetValue on a non-AXValue CFType throws — validate the type ID
    // first, otherwise the whole process aborts with
    // "Rust cannot catch foreign exceptions".
    let pos_ref = copy_attribute_of_type(element, kAXPositionAttribute, AXValueGetTypeID())?;
    let size_ref = match copy_attribute_of_type(element, kAXSizeAttribute, AXValueGetTypeID()) {
        Some(r) => r,
        None => {
            CFRelease(pos_ref);
            return None;
        }
    };

    let mut point = CGPoint { x: 0.0, y: 0.0 };
    let mut size = CGSize {
        width: 0.0,
        height: 0.0,
    };

    let ok_p = AXValueGetValue(
        pos_ref as AXValueRef,
        kAXValueTypeCGPoint,
        &mut point as *mut _ as *mut c_void,
    );
    let ok_s = AXValueGetValue(
        size_ref as AXValueRef,
        kAXValueTypeCGSize,
        &mut size as *mut _ as *mut c_void,
    );

    CFRelease(pos_ref);
    CFRelease(size_ref);

    if ok_p && ok_s {
        Some((point.x, point.y, size.width, size.height))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clickable_roles_nonempty_and_well_formed() {
        // Guard against accidental deletions of the role list.
        assert!(CLICKABLE_ROLES.len() >= 5);
        for r in CLICKABLE_ROLES {
            assert!(r.starts_with("AX"));
        }
    }

    #[test]
    fn mac_ax_constructible() {
        // We deliberately do *not* call focused_window_candidates() here —
        // macOS CI runners are headless and AX calls into a missing
        // WindowServer can hang rather than fail fast. Integration tests
        // against a real focused app live outside the unit-test surface.
        let _ax = MacAx;
    }
}
