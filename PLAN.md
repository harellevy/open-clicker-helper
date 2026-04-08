# open-clicker-helper — Implementation Plan

An open-source, offline-first AI assistant that watches your screen, listens to your voice, figures out where you need to click, animates a cursor path on a transparent overlay, and reads the answer back to you.

---

## Phases

### P0 — Tauri Shell + Platform Abstraction ✅

**Goal:** A buildable, CI-passing Tauri 2 + React/Vite project with clean OS boundaries.

- pnpm workspace with `ui/` (Vite + React + TS, two HTML entries: `settings.html` + `overlay.html`) and `src-tauri/` Rust shell.
- `tauri.conf.json`: two windows — `settings` (880×620, visible) and `overlay` (transparent, decorations-off, always-on-top, hidden at startup).
- Platform abstraction layer (`src-tauri/src/platform/`) with four traits:
  - `Permissions` — TCC-style permission status (screen recording, accessibility, microphone).
  - `OverlayWindow` — configure the transparent overlay with objc2 on macOS (`kCGScreenSaverWindowLevel`, `canJoinAllSpaces`, `fullScreenAuxiliary`, `ignoresMouseEvents`).
  - `ScreenCapture` — stub; real impl lands in P4.
  - `MouseTracker` — stub; real impl lands in P4.
- macOS impl (`platform/macos/`), stub impl for non-macOS hosts so the crate always compiles.
- `get_permissions` and `ping_sidecar` Tauri commands wired to a minimal React settings page rendering permission badges.
- CI: `cargo fmt/clippy/test` on macOS-14 + ubuntu-latest, `pnpm typecheck`, uv pytest for the sidecar.

---

### P1 — Python Sidecar (stdio JSON-RPC) ✅

**Goal:** A reliable, lifecycle-managed AI back-end that the Rust shell can call from any `tauri::command`.

- `sidecar/` — uv-managed Python project (`pyproject.toml`, PEP 735 dev dependency group).
- `och_sidecar` package with:
  - `rpc.py` — async line-delimited JSON-RPC 2.0 over stdin/stdout (request/response + server-side notifications with `*.progress` method names).
  - `handlers.py` — `ping` handler (returns `{ok: true, version: "0.1.0"}`); further methods added per phase.
  - `providers/` — abstract `Provider` base class; concrete implementations added in P3/P4/P6.
- Rust `Sidecar` struct (`src-tauri/src/sidecar.rs`): spawns `uv run --project <dir> och-sidecar`, owns `ChildStdin`, drives a tokio read-loop that routes responses to `oneshot` channels and broadcasts progress notifications.
- `AppState` holds `Mutex<Option<Arc<Sidecar>>>`; spawned in `.setup()`, available to all `tauri::command`s via `State<AppState>`.
- `ping_sidecar` command exposed to the frontend, health-checked in the settings UI.

---

### P2 — Global Hotkey + Permissions Flow

**Goal:** A single hotkey starts a recording session; the app surfaces actionable permission prompts so first-run just works.

- Register a configurable global shortcut (default `Cmd+Shift+Space`) via `tauri-plugin-global-shortcut`; emit a Tauri event `clicker://start` to both windows.
- Permissions flow:
  - On startup call `get_permissions`; if any are `denied`/`unknown`, show a step-by-step guide card in the settings window.
  - macOS: open `System Settings` panes via `open x-apple.systempreferences:` URLs using `tauri-plugin-shell`.
  - Accessibility permission checked with `AXIsProcessTrusted` (objc2); screen-recording with `CGRequestScreenCaptureAccess`.
- Store the chosen hotkey in `tauri-plugin-store` (`settings.json`); allow re-binding via the settings UI.
- Show a small always-on-top HUD near the menu bar while a session is active (status: "Listening…" → "Thinking…" → "Done").

---

### P3 — Voice Input (STT)

**Goal:** Record microphone audio while the hotkey is held; transcribe it with a local or cloud STT provider.

**Rust side**
- `platform/macos/mouse.rs` → real `MouseTracker` impl using `CGEventPost` / `NSEvent` for current cursor position.
- `tauri-plugin-microphone` (or raw `cpal`) for mic capture; stream PCM frames over a second sidecar channel or write a temp WAV file.
- `record_audio` Tauri command: starts capture on `clicker://start`, stops on hotkey release or `clicker://stop`; returns a temp file path.

**Python side**
- `providers/stt/` with `SttProvider` abstract base.
- `MlxWhisperProvider` — calls `mlx_whisper.transcribe(audio_path)` (offline, Apple Silicon).
- `WhisperOpenAIProvider` — calls `openai.audio.transcriptions.create` (cloud fallback).
- `transcribe` RPC method dispatches to the active provider; streams `transcribe.progress` notifications with partial text.

---

### P4 — Screen Capture + Vision LLM

**Goal:** Capture the focused window and ask a local vision LLM where to click.

**Rust side**
- `platform/macos/capture.rs` — real `ScreenCapture` impl using `ScreenCaptureKit` via `core-graphics` / `objc2`; capture only the frontmost `CGWindowID` to avoid capturing the overlay itself.
- `get_focused_window` Tauri command: returns a temp PNG path + window title.
- Unhide the overlay window during the "Thinking…" state so the animated cursor can be shown.

**Python side**
- `providers/vision/` with `VisionProvider` abstract base.
- `OllamaVisionProvider` — sends image + prompt to a local Ollama endpoint (`qwen2.5-vl:7b` default); parses JSON `{x, y, explanation}` from the response.
- `OpenAIVisionProvider` — calls `openai.chat.completions.create` with `gpt-4o` (cloud fallback).
- `AnthropicVisionProvider` — calls `anthropic.messages.create` with `claude-opus-4-6`.
- `locate_click` RPC method: accepts `{image_b64, question}`, dispatches to active provider, returns `{x: float, y: float, explanation: str}` in screen-fraction coordinates (0–1).

---

### P5 — Overlay Cursor Animation

**Goal:** Animate a ghost cursor from its current position to the target, then optionally perform the click.

- React `Overlay.tsx`: subscribes to Tauri events; renders an SVG cursor sprite and a bezier path.
- Animation: spring-physics tween from `(cursor_x, cursor_y)` → `(target_x, target_y)` over ~600 ms; easing matches macOS system animations.
- Accessibility option: highlight target with a pulsing ring in addition to the path.
- Optional auto-click: `perform_click` Tauri command uses `CGEventPost(kCGHIDEventTap, mouseDown + mouseUp)` at the target coordinates (requires accessibility permission).
- The overlay window hides itself once the animation completes (`overlay.hide()` after a 1 s grace period).

---

### P6 — Voice Output (TTS)

**Goal:** Read the LLM explanation back to the user in a natural voice.

**Python side**
- `providers/tts/` with `TtsProvider` abstract base.
- `KokoroProvider` — streams PCM audio from `kokoro-onnx`; plays via `sounddevice` directly in the sidecar process (offline, low latency).
- `OpenAITtsProvider` — calls `openai.audio.speech.create` with `tts-1`.
- `speak` RPC method: accepts `{text}`, dispatches to active provider; streams `speak.progress` notifications (`{chunk_index, total}`) so the UI can show a progress bar.

**Rust side**
- `speak_result` Tauri command: calls `sidecar.call("speak", …)` and forwards progress events to the frontend.

---

### P7 — Model Wizard + Settings UI

**Goal:** A polished first-run wizard that detects available local models and lets the user pick cloud providers.

- Settings window gains a multi-step wizard route shown on first launch (stored in `tauri-plugin-store`).
- **Step 1 — Permissions**: re-uses the P2 permission cards; "Fix" buttons open the relevant System Settings pane.
- **Step 2 — STT**: auto-detects `mlx_whisper` install; offers cloud (OpenAI key input) as fallback.
- **Step 3 — Vision LLM**: pings `http://localhost:11434` to check if Ollama is running; lists pulled models matching `*-vl`; offers OpenAI / Anthropic / Groq as cloud options.
- **Step 4 — TTS**: checks for `kokoro-onnx` + a voice model; offers OpenAI TTS as cloud fallback.
- **Step 5 — Hotkey**: live hotkey recorder; shows a preview of the activation flow.
- Provider config stored in `settings.json` via `tauri-plugin-store`; read by the sidecar on startup via a `get_config` RPC call.

---

### P8 — Packaging + Release

**Goal:** A signed, notarized `.dmg` that a user can download, open, and use in under 2 minutes.

- Bundle a platform-specific `uv` binary inside the app (`src-tauri/binaries/uv-aarch64-apple-darwin`) so users don't need `uv` on `PATH`.
- `resolve_sidecar_dir` updated to probe the bundled resources path when running from a packaged app.
- Icons: generate a full `icons/` set (1024×1024 PNG → icns, ico, various PNGs) from a source SVG.
- `tauri.conf.json` bundle section: add `signingIdentity`, `providerShortName`, entitlements for screen recording and accessibility.
- GitHub Actions release workflow: build on `macos-14`, sign + notarize with `notarytool`, upload `.dmg` as a GitHub Release asset.
- Auto-update: `tauri-plugin-updater` pointed at the GitHub Releases API; checks on startup, prompts with a non-blocking banner.

---

## Architecture summary

```
┌─────────────────────────────────┐
│         React UI (Vite)         │
│  settings window  overlay window│
└────────────┬────────────────────┘
             │ Tauri commands / events
┌────────────▼────────────────────┐
│       Tauri Shell (Rust)        │
│  platform/  ipc/  sidecar/      │
│  objc2 overlay, CGCapture, etc. │
└────────────┬────────────────────┘
             │ stdio JSON-RPC
┌────────────▼────────────────────┐
│     Python Sidecar (uv)         │
│  STT  |  Vision LLM  |  TTS     │
│  providers: local + cloud       │
└─────────────────────────────────┘
```

**Provider matrix**

| Capability | Offline (default) | Cloud option A | Cloud option B |
|---|---|---|---|
| STT | mlx-whisper (Apple Silicon) | OpenAI Whisper | — |
| Vision LLM | Ollama (Qwen2.5-VL) | OpenAI GPT-4o | Anthropic Claude |
| TTS | Kokoro-ONNX | OpenAI TTS | — |

All cloud providers require the user to supply their own API key; no key is bundled or telemetry collected.
