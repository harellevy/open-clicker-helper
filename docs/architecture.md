# Architecture

open-clicker-helper is a Tauri 2 desktop app that pairs a Rust shell with a
Python AI sidecar. The Rust side owns every OS concern (windows, hotkeys,
capture, clicks, persistent settings) and the Python side owns every AI
concern (speech-to-text, vision LLMs, text-to-speech). The two talk over a
single stdio JSON-RPC channel.

```
┌──────────────────────────────┐         ┌───────────────────────────────┐
│ Tauri shell (Rust)           │         │ Python sidecar                │
│                              │  stdio  │                               │
│  platform/macos/*            │ <─────> │  rpc.py   (JSON-RPC framing)  │
│  audio.rs   (cpal)           │  JSON   │  handlers.py                  │
│  sidecar.rs (child process)  │   RPC   │  pipeline.py (STT→VLM→TTS)    │
│  ipc.rs     (Tauri commands) │         │  grounding.py                 │
│  store.rs   (settings)       │         │  setup.py    (download model) │
│                              │         │  providers/  (per-vendor)     │
│   ┌───── React frontend ─────┴─────┐   │                               │
│   │ settings window · overlay HUD  │   │                               │
│   └────────────────────────────────┘   │                               │
└──────────────────────────────┘         └───────────────────────────────┘
```

## Why this split

- **Rust owns latency-critical and permission-gated work.** Hotkey capture,
  audio recording, screenshotting, cursor synthesis, and tray icon all live
  in `src-tauri/src/`. These have tight latency budgets and need direct
  access to macOS APIs.
- **Python owns the model zoo.** mlx-whisper, Kokoro, the OpenAI SDK,
  Anthropic SDK, and the Ollama HTTP client are all pinned to Python; the
  sidecar ships as a `uv`-managed project. This keeps the Rust build fast
  and lets us swap providers without touching compiled code.
- **Stdio JSON-RPC, not HTTP.** The child process dies automatically when
  Tauri drops the handle. No ports, no auth, no firewall prompts, one
  fewer failure mode on first launch. Framing is one JSON object per line
  (see `sidecar/och_sidecar/rpc.py`).

## End-to-end flow

1. **User presses the global hotkey.** `register_hotkey` in `lib.rs` owns
   the `tauri-plugin-global-shortcut` binding. First press starts a
   `cpal` recording; a second press (or VAD silence) stops it.
2. **Audio stop.** `audio::AudioRecorder` returns a WAV byte buffer plus a
   `StopReason` — if the user never spoke, the pipeline is cancelled and
   the HUD returns to idle without spending STT/VLM/TTS budget.
3. **Screenshot.** `platform::macos::capture::capture_focused_window`
   shells out to `screencapture -x` (P4 default) and returns PNG bytes.
4. **RPC call.** `sidecar::Sidecar::call("pipeline.run", …)` sends one
   request containing base64 audio, base64 image, and the current
   settings blob.
5. **Pipeline runs server-side.** `pipeline.py` does
   STT → grounding → TTS, yielding `(event, payload)` tuples for each
   stage. The Rust client streams them as `sidecar://progress`
   notifications which the overlay listens to.
6. **Overlay animates.** `ui/src/overlay/Annotation.tsx` consumes the
   final `{steps, audio_b64}` result and drives a cursor-path animation
   over the transparent overlay window. Each step is followed by a
   synthesized left-click (`click_at_normalized` →
   `platform::macos::mouse::click`) and a re-grounding pass against a
   fresh screenshot.

## Windows (two of them)

Tauri declares two windows in `tauri.conf.json`:

- **`settings`** — the normal app window with the React settings UI
  (`ui/src/settings/*`). Shown by default.
- **`overlay`** — a full-screen transparent, always-on-top,
  click-through window at `kCGScreenSaverWindowLevel`
  (`platform::macos::window::make_click_through_topmost`). Hosts the
  cursor animation and the debug-mode HUD.

The overlay is re-sized to the primary monitor at setup and hidden
whenever there's nothing to draw.

## Settings, store, and history

- `src-tauri/src/store.rs` defines the top-level `Settings` struct and
  nested `SttSettings` / `VlmSettings` / `TtsSettings` / `DebugSettings` /
  `SystemPrompts` / `GroundingSettings` blocks. Persisted via
  `tauri-plugin-store` as `settings.json` in the app's data directory.
- Every field is wrapped in `#[serde(default)]` so older saved settings
  keep loading after new fields are added.
- Conversation history lives in a separate `history.json` store file
  (see `src-tauri/src/history.rs`) so append-only log data doesn't mix
  with user configuration.

## Cross-platform scaffolding

All OS-specific code sits behind a trait object:

```rust
pub trait ScreenCapture  { fn capture_focused_window(&self) -> AppResult<Vec<u8>>; }
pub trait OverlayWindow  { fn make_click_through_topmost(&self, w: &WebviewWindow) -> AppResult<()>; }
pub trait Permissions    { /* screen_recording / accessibility / microphone */ }
pub trait MouseController{ fn click(&self, x: f64, y: f64) -> AppResult<()>; }
pub trait AxTree         { fn focused_window_candidates(&self) -> AppResult<Vec<AxCandidate>>; }
```

v1 ships only a `platform::macos` impl plus a `platform::stub` that keeps
Linux CI compiling. A future `platform::windows` module is the only
addition required to port — see [porting-windows.md](porting-windows.md).

## Test topology

- `cargo test --manifest-path src-tauri/Cargo.toml` — Rust unit tests
  cover store/history serde, audio VAD state, sidecar RPC framing, and
  platform stubs.
- `pytest sidecar/` — Python unit tests for grounding parser, pipeline
  dispatch, and provider factories (providers themselves are
  dependency-injected so tests never need real model weights).
- `pnpm typecheck` and `pnpm build` — frontend compile + bundle gate.

See [providers.md](providers.md) for the provider catalogue and
[porting-windows.md](porting-windows.md) for the Windows roadmap.
