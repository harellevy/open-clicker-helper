# open-clicker-helper — Plan

## Context

Greenfield repo (`/home/user/open-clicker-helper`). Build an open-source, offline-first AI clicker buddy: hold a hotkey, speak a question, the app screenshots the focused window, asks a local vision LLM where to click, animates a cursor path on a transparent overlay, and reads the answer back.

---

## Locked decisions (post-review)

- **Tauri 2 (Rust) + React/Vite frontend**, two windows: `settings` and `overlay`.
- **Python sidecar** for AI, spawned by Tauri over stdio JSON-RPC so it dies when the app dies.
- **`uv` for Python**, not PyInstaller. Ship a bundled `uv` binary in `src-tauri/binaries/`.
- **Skip OmniParser for v1.** Use **Qwen2.5-VL** via Ollama for direct pixel-coordinate grounding.
- **Two windows, not three.** One always-on-top overlay; settings in the main window.
- **Native macOS window config via `objc2`** — `kCGScreenSaverWindowLevel`, `canJoinAllSpaces`, `ignoresMouseEvents`.
- **STT default: `mlx-whisper`** (Apple Silicon). Cloud fallback: OpenAI Whisper.
- **Screen capture via `scap`** crate (ScreenCaptureKit wrapper) for per-window capture.
- **Audio capture via `cpal`** (cross-platform, no extra system deps on macOS).
- **Offline-first:** Ollama / mlx-whisper / Kokoro. All cloud providers require user-supplied API keys.

---

## Cross-platform readiness

All OS-specific code lives behind Rust traits:

```rust
trait ScreenCapture  { fn capture_focused_window(&self) -> AppResult<Vec<u8>>; }
trait OverlayWindow  { fn make_click_through_topmost(&self, w: &WebviewWindow) -> AppResult<()>; }
trait Permissions    { fn screen_recording(&self) -> PermissionStatus; ... }
trait MouseTracker   { fn poll(&self) -> AppResult<(i32, i32)>; }
trait GlobalHotkey   { ... }
```

v1 ships only `platform::macos` impls (`scap`, `objc2`, `cpal`). A `platform::stub` keeps the crate compiling on Linux CI. `platform::windows` arrives in P8.

---

## Repository layout

```
open-clicker-helper/
├── README.md
├── LICENSE
├── PLAN.md
├── package.json          # pnpm workspace root
├── pnpm-workspace.yaml
├── src-tauri/
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── capabilities/
│   ├── binaries/         # bundled uv binary (gitignored except .gitkeep)
│   ├── icons/
│   └── src/
│       ├── lib.rs
│       ├── main.rs
│       ├── error.rs
│       ├── ipc.rs
│       ├── sidecar.rs
│       ├── audio.rs      # cpal mic capture (P3)
│       └── platform/
│           ├── mod.rs
│           ├── stub.rs
│           └── macos/
│               ├── mod.rs
│               ├── window.rs
│               ├── permissions.rs
│               ├── capture.rs    # scap (P4)
│               └── mouse.rs
├── sidecar/
│   ├── pyproject.toml
│   └── och_sidecar/
│       ├── __main__.py
│       ├── rpc.py
│       ├── pipeline.py       # P3+
│       ├── grounding.py      # P4+
│       ├── providers/
│       │   ├── base.py
│       │   ├── stt_mlx_whisper.py
│       │   ├── stt_openai.py
│       │   ├── vlm_ollama.py
│       │   ├── vlm_openai.py
│       │   ├── vlm_anthropic.py
│       │   ├── tts_kokoro.py
│       │   └── tts_openai.py
│       └── handlers.py
├── ui/
│   ├── index.html        # settings entry
│   ├── overlay.html      # overlay entry
│   ├── vite.config.ts
│   └── src/
│       ├── settings/
│       │   ├── App.tsx
│       │   ├── pages/
│       │   │   ├── Providers.tsx
│       │   │   ├── Permissions.tsx
│       │   │   └── Hotkeys.tsx
│       │   └── main.tsx
│       └── overlay/
│           ├── Overlay.tsx
│           ├── Annotation.tsx  # SVG cursor animation (P4)
│           ├── animator.ts     # easing / spring physics
│           └── main.tsx
└── docs/
    ├── architecture.md
    ├── providers.md
    └── porting-windows.md
```

---

## End-to-end flow

1. User holds global hotkey (default `` ⌘⇧` ``).
2. `audio.rs` records mic via `cpal`; `tracker` polls cursor position every 50 ms.
3. On release: `platform::macos::capture` grabs a PNG of the frontmost window.
4. `sidecar.rs` sends one JSON-RPC `pipeline.run` request with `{audio_b64, image_b64, question_hint}`.
5. Sidecar `pipeline.run`:
   a. STT (mlx-whisper) → text.
   b. `grounding.locate(image, question)` calls the active vision LLM; returns `{steps: [{x, y, explanation}]}`.
   c. Validate JSON; on parse failure or low-confidence score, retry once with a stricter prompt.
   d. TTS (Kokoro) → wav bytes for the explanation text.
   e. Stream progress events back over stdio (`pipeline.progress` notifications).
6. Tauri receives events, denormalises `xy_norm` (0–1) → screen pixels using window bounds.
7. `Annotation.tsx` animates an SVG cursor from current position → target with a bezier path.
8. Audio response plays via `cpal` output (or system audio for cloud TTS).
9. Multi-step (`steps[]`) animates sequentially with a 300 ms pause between steps.

---

## Implementation phases (ship fast, iterate)

**P0 — bootstrap (smallest demo loop)**
- `cargo create-tauri-app` with React+TS+Vite, two windows (`settings`, `overlay`).
- `platform/macos/window.rs` uses `objc2` to set level + collection behavior + ignoresMouseEvents.
- `platform/macos/permissions.rs` reads TCC status via `core-foundation`.
- `get_permissions` and `ping_sidecar` Tauri commands; minimal settings UI with permission badges.
- CI: `cargo fmt`, `cargo clippy`, `cargo test` on macOS-14 + ubuntu-latest; `pnpm typecheck`; uv pytest.

**P1 — sidecar transport**
- `sidecar/pyproject.toml` declares minimal deps; `uv` lock file committed.
- `och_sidecar/rpc.py`: read newline-delimited JSON-RPC 2.0 from stdin, write to stdout; broadcast channel for progress notifications.
- `sidecar.rs`: spawn `binaries/uv run och-sidecar`; own stdin/stdout; route responses via `oneshot` channels.
- One stub handler per category returning canary values (`ping → {ok: true}`).

**P2 — first-run environment setup + settings UI** ✅

The first thing a new user encounters after install is a 5-step wizard that
checks and downloads every offline model dependency. This runs once (gated by
`setup_complete` in the store); afterwards the settings window shows a tabbed
UI for re-configuring everything.

*Wizard steps (shown on first launch):*
1. **Permissions** — Screen Recording, Accessibility, Microphone badges with
   "Fix →" deep-links into System Settings privacy panes.
2. **STT** — Detect mlx-whisper install + model-weights cache; "Download
   weights" streams progress via `setup.download_stt`. OpenAI Whisper as cloud
   fallback.
3. **Vision LLM** — Ping Ollama; check + pull chosen model; `setup.download_vlm`
   streams NDJSON progress from `ollama pull`. OpenAI GPT-4o and Anthropic
   Claude as cloud fallbacks.
4. **TTS** — Detect kokoro-onnx + voice model; "Download Kokoro" runs
   `setup.download_tts`. OpenAI TTS as cloud fallback.
5. **Hotkey** — Live key-recorder; writes accelerator string to the store.

*Settings pages (post-wizard):*
- `Providers.tsx` — provider toggles + API-key fields + "Test connection" button
  (`providers.test` RPC).
- `Permissions.tsx` — permission badges + "Fix →" + Refresh.
- `Hotkeys.tsx` — rebind activation shortcut at any time.

*Rust:* `store.rs` (`Settings` structs); new IPC commands: `get_settings`,
`save_settings`, `open_system_settings`, `sidecar_call`; progress relay in
`lib.rs` emits `sidecar://progress` Tauri events.

*Sidecar:* `setup.py` with check/download helpers (generators for streaming);
`handlers.py` registers all `setup.*` and `providers.test` methods.

**P3 — voice round-trip (no vision yet)**
- `audio.rs` records mic to in-memory WAV via `cpal`; `record_audio` Tauri command.
- Implement `stt_mlx_whisper.py` and `tts_kokoro.py` providers.
- `pipeline.run` (text-only mode): audio → STT → LLM text answer → TTS → play.
- HUD shows transcript bubble. Confirms full hotkey → audio → response loop works.

**P4 — vision grounding (the core feature)** ✅
- `platform/macos/capture.rs`: screenshot via `screencapture -x` CLI (no extra deps); future P4.1 replaces with `scap` for per-window capture.
- `grounding.py`: prompt VLM to return structured `{steps: [{x,y,explanation}]}` JSON; validate + clamp to [0,1]; retry once with stricter prompt on parse failure.
- `pipeline.py`: when `image_b64` present, enters grounding mode (STT → grounding → TTS); text-only mode otherwise.
- `Annotation.tsx`: full-screen SVG layer — cursor dot follows cubic-bezier path to each step with spring-physics easing; sequential multi-step animation with 300 ms pause.
- `animator.ts`: `spring()`, `easeInOut()`, `lerp()`, `bezierPath()`, `animate()` helpers.
- `capture_screen` IPC command exposed to frontend.

**P4.1 — iterative multi-step grounding** *(planned)*

Real UI flows require re-grounding after each user action, not a single up-front batch:

1. Ground step 1 against initial screenshot → animate cursor → **click** (via `cliclick` or Accessibility API).
2. Wait for UI to settle (configurable delay, default 800 ms).
3. Capture a **fresh screenshot** of the updated UI.
4. Re-ground step 2 against the new screenshot → animate → click.
5. Repeat until all steps complete or an error occurs.

This requires:
- `platform/macos/mouse.rs`: synthesise a click at normalised coordinates using `CGEvent`.
- New `pipeline.step` RPC method (streaming) that yields `(screenshot_b64, step)` one at a time.
- Rust coordinates the loop: receives each step, clicks, captures, sends next screenshot.
- `Annotation.tsx` stays per-step (already correct architecture).
- Settings: "step delay" slider (0–2 s); "max steps" cap (default 8).

**P5 — first-run model wizard**
- `Models.tsx` checks for `~/Library/Application Support/` Ollama models + mlx-whisper cache.
- Downloads Whisper weights, Kokoro voice, pulls `qwen2.5-vl:7b` via `ollama pull`.
- Progress UI driven by sidecar download events (`download.progress` notifications).

**P6 — polish + tray**
- macOS tray icon; conversation history (last N sessions) stored in `tauri-plugin-store`.
- Notarisation + DMG via `tauri build`; documentation pass for README + `docs/`.

**P7 — OmniParser SoM (v1.1, optional)**
- Add OmniParser v2 to sidecar behind a feature flag (`OCH_GROUNDING_MODE=som`).
- `grounding.py` adds `mode: "direct" | "som"` dispatch.
- Settings exposes a "Use OmniParser for hard UIs" toggle.

**P8 — Windows port (later)**
- Add `src-tauri/src/platform/windows/` impl using `windows-capture` + `windows` crate.
- Add `windows-capture` to `Cargo.toml` under `[target.'cfg(target_os = "windows")'.dependencies]`.
- Sidecar/UI unchanged. STT switches to `faster-whisper` as `mlx-whisper` is Apple-only.

---

## Critical files (create-first order)

1. `src-tauri/src/platform/macos/window.rs` — objc2 overlay config (blocks everything else on macOS).
2. `src-tauri/src/platform/macos/capture.rs` — `scap` per-window PNG (blocks P4).
3. `src-tauri/src/sidecar.rs` — stdio JSON-RPC client (blocks all AI features).
4. `sidecar/pyproject.toml` + `sidecar/och_sidecar/rpc.py` — sidecar entry point.
5. `sidecar/och_sidecar/pipeline.py` + `grounding.py` — core AI pipeline (blocks P3/P4).
6. `ui/src/overlay/Annotation.tsx` + `animator.ts` — cursor animation (blocks P4 UX).
7. `ui/src/settings/pages/Providers.tsx` — provider config UI (blocks P2).

---

## Key dependencies

- **Rust:** `tauri` 2.x, `tauri-plugin-store`, `tauri-plugin-global-shortcut`, `tauri-plugin-shell`, `scap`, `cpal`, `objc2`, `objc2-app-kit`, `tokio`, `tracing`
- **Python:** `pydantic`, `numpy`, `pillow`, `mlx-whisper`, `kokoro-onnx`, `sounddevice`, `openai`, `anthropic`
- **Frontend:** `react`, `react-router-dom`, `@tauri-apps/api`, `framer-motion` (or custom spring), `vite`

---

## Verification

- `cargo test --manifest-path src-tauri/Cargo.toml` — unit tests for platform stubs + sidecar client.
- `pytest sidecar/` — provider unit tests (mock HTTP, mock audio).
- `pnpm test` (vitest) — animator interpolation + coordinate normalisation.
- **Manual smoke matrix** (documented in `docs/architecture.md`):
  1. Overlay stays click-through across Spaces and fullscreen apps.
  2. Screen Recording permission flow on a fresh machine (never-granted state).
  3. Voice loop with mlx-whisper + Kokoro, no network.
  4. Vision loop with `ollama pull qwen2.5-vl:7b` running locally.
  5. Cloud loop with OpenAI key configured.
  6. App quit kills sidecar process (no orphans).
- `tauri build` produces a notarisable `.dmg` with bundled `uv` binary.
