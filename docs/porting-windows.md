# Porting to Windows

v1 ships macOS-only. The code is deliberately structured so a Windows
port only needs a new `platform::windows` sibling module — the sidecar,
the UI, and every non-platform Rust file should stay untouched.

## What stays the same

- **All of `sidecar/`.** The Python providers, grounding parser, RPC
  framing, and pipeline orchestration are OS-agnostic. The only moving
  parts are:
  - Replace `mlx-whisper` with `faster-whisper` under a Python
    environment marker in `sidecar/pyproject.toml`. `mlx-whisper` is
    Apple-silicon-only; `faster-whisper` runs on CPU or CUDA.
  - Kokoro already runs on Windows. No change.
- **All of `ui/`.** React + Vite has no Windows-specific concerns.
- **`src-tauri/src/` top-level files** (`lib.rs`, `ipc.rs`,
  `sidecar.rs`, `audio.rs`, `store.rs`, `history.rs`, `error.rs`).
  `cpal` already runs on both platforms via WASAPI, so audio capture
  needs no changes.

## What to add

A new module at `src-tauri/src/platform/windows/` that implements the
traits declared in `platform/mod.rs`:

```rust
pub trait Platform {
    fn overlay(&self) -> &dyn OverlayWindow;
    fn capture(&self) -> &dyn ScreenCapture;
    fn permissions(&self) -> &dyn Permissions;
    fn mouse(&self) -> &dyn MouseController;
    fn ax(&self) -> &dyn AxTree;
}
```

### Screen capture (`capture.rs`)

Use the [`windows-capture`](https://crates.io/crates/windows-capture)
crate or call the Windows.Graphics.Capture API directly via the
[`windows`](https://crates.io/crates/windows) crate. Return
`Vec<u8>` of PNG bytes, same as the macOS impl.

```toml
[target.'cfg(target_os = "windows")'.dependencies]
windows-capture = "1"
windows = { version = "0.58", features = [
  "Win32_Graphics_Gdi",
  "Win32_UI_WindowsAndMessaging",
  "Win32_Foundation",
] }
```

### Overlay window (`window.rs`)

Tauri already handles `alwaysOnTop`, `decorations: false`, and
`transparent: true` via `tauri.conf.json`. The extra bits the macOS
impl does via `objc2` map to Win32 as:

- **Click-through.** Set `WS_EX_TRANSPARENT | WS_EX_LAYERED` on the
  window handle via `SetWindowLongPtrW(GWL_EXSTYLE, …)`.
- **Always on top across desktops.** `SetWindowPos` with `HWND_TOPMOST`.
- **Skip taskbar.** Already handled by `skipTaskbar: true` in
  `tauri.conf.json`; no extra call needed.

The `WebviewWindow::hwnd()` method gives you the raw `HWND`.

### Permissions (`permissions.rs`)

Windows doesn't have a centralised TCC equivalent. Return
`PermissionStatus::Granted` for `screen_recording` and
`accessibility`, and for `microphone` query the Windows privacy
settings via the [`windows`](https://crates.io/crates/windows) crate
(`Windows::Media::Capture` or, more simply, attempt to open a
capture stream via `cpal` and map failure to `Denied`).

### Mouse (`mouse.rs`)

`SendInput` with `MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_LEFTUP` at the
target coordinates. Coordinates are in virtual-screen pixels; scale
by the monitor DPI (`GetDpiForWindow`) if you need logical points.

### AX tree (`ax.rs`)

Use UI Automation (`Windows.UI.Automation` or the
[`uiautomation`](https://crates.io/crates/uiautomation) crate) to
walk the focused window's element tree. Map `ControlType` →
`AxCandidate.role`, `Name` → `title`, `HelpText` → `description`, and
use the element's `BoundingRectangle` for coordinates.

## Wiring

In `src-tauri/src/platform/mod.rs`, expand the `current()` selector:

```rust
#[cfg(target_os = "macos")]
pub fn current() -> &'static dyn Platform { &macos::Macos }

#[cfg(target_os = "windows")]
pub fn current() -> &'static dyn Platform { &windows::Windows }

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
pub fn current() -> &'static dyn Platform { &stub::Stub }
```

Move the existing `#[cfg(target_os = "macos")]` gates in `lib.rs` to
`#[cfg(any(target_os = "macos", target_os = "windows"))]` wherever
the behaviour is actually desired on both.

## CI

Add a `windows-latest` job to the GitHub Actions matrix running the
same `cargo fmt`/`cargo clippy`/`cargo test` gate. The Python sidecar
tests should already pass — verify `faster-whisper` installs cleanly
inside the `uv` project on Windows.

## What's out of scope for v1

- **Windows-specific installer / code-signing.** The macOS build targets
  a notarised `.dmg`; the Windows build will want an MSI or MSIX via
  `tauri-bundler`. That's a separate slice.
- **ScreenCaptureKit parity.** Per-window capture on Windows is more
  awkward than on macOS — `windows-capture` does full-screen easily and
  single-window via `GraphicsCaptureItem`. Start with full-screen
  capture and revisit once the rest of the port lands.
