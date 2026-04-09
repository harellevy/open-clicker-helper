# open-clicker-helper

> An open-source, **offline-first** AI buddy that lives next to your cursor.
> Hold a hotkey, ask a question out loud, and it figures out where you need to click.

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/status-work%20in%20progress-orange.svg" alt="Status: work in progress">
  <img src="https://img.shields.io/badge/platform-macOS-lightgrey.svg" alt="Platform: macOS">
  <img src="https://img.shields.io/badge/offline--first-yes-success.svg" alt="Offline-first: yes">
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs welcome">
</p>

---

## Heads up — this is a work in progress, and I need collaborators

**open-clicker-helper is in early, active development.** It is not ready for daily
use, it is not hardened, and it is not guaranteed to work. I am building it in the
open and I am **actively looking for collaborators** to help push it to a shippable
state.

If you enjoy any of the following, please jump in — issues, draft PRs, design
ideas and bug reports are all very welcome:

- Rust, Tauri 2, and native desktop plumbing
- Python AI pipelines (Whisper, Kokoro, Ollama, llama.cpp, MLX)
- macOS Accessibility APIs and ScreenCaptureKit
- **OS coverage** — a Windows port (the platform layer is stubbed and waiting)
  and eventually Linux
- **Language support** — STT and TTS models for non-English locales, including
  swapping in better multilingual Whisper / Kokoro variants and validating
  grounding prompts across languages
- Frontend polish (React + Vite), UX for a transparent cursor overlay
- Docs, packaging, CI, release engineering

Open an issue or a draft PR and say hi. No contribution is too small.

### Disclaimer — read this before running anything

> **This software is experimental. Use it entirely at your own risk.**

- It is provided **"as is", without warranty of any kind**, express or implied.
  See [`LICENSE`](LICENSE) for the full legal text.
- It **moves your mouse, clicks on your behalf, records your microphone, and
  captures your screen.** A buggy or misinstructed run can click the wrong
  thing, send messages you did not intend, delete data, or leak private content
  to a local or cloud model.
- It has **not been audited** for security, privacy, or correctness. Do not run
  it on production machines, against sensitive applications, inside regulated
  environments, or anywhere a wrong click could cause real harm.
- AI models are **not deterministic.** The same question can produce different
  clicks on different runs — review every suggested action before trusting it.
- By running this project you accept full responsibility for anything it does
  on your system.

If any of the above is a problem for your use case: **do not run this yet.**

---

## What it does

Hold a hotkey, speak a question, and open-clicker-helper will:

1. Take a screenshot of the focused app.
2. Walk the macOS Accessibility tree to enumerate clickable elements.
3. Use a local vision LLM to figure out **where you need to click**.
4. Animate a cursor on a transparent overlay showing you the path.
5. Read the answer back to you over TTS.

Works fully offline with local models. Cloud providers are a one-setting toggle
if you would rather use them.

## Stack

- **Shell:** Tauri 2 (Rust) + React + Vite
- **AI sidecar:** Python, managed by [`uv`](https://docs.astral.sh/uv/), spoken to over stdio JSON-RPC
- **Defaults (offline):** mlx-whisper (STT), Kokoro (TTS), Qwen2.5-VL via Ollama (vision LLM)
- **Optional (cloud):** OpenAI, Anthropic, Groq, any OpenAI-compatible endpoint

## Repository layout

```
src-tauri/   Rust shell, windows, capture, sidecar lifecycle
sidecar/     Python AI providers + pipeline (uv-managed)
ui/          React + Vite frontend (settings + transparent overlay)
docs/        Architecture, providers, porting notes
```

## Development

Requirements: Rust ≥ 1.80, Node ≥ 20, pnpm ≥ 9, [uv](https://docs.astral.sh/uv/) ≥ 0.4, Python ≥ 3.11.
On macOS you also need Xcode command line tools.

```bash
pnpm install
pnpm dev          # runs Tauri in dev mode
```

See [`docs/architecture.md`](docs/architecture.md) for a deeper dive into the
shell/sidecar split, permissions, and the grounding pipeline.

## Roadmap (rough)

- [x] Tauri shell, transparent overlay, global hotkey
- [x] Python sidecar lifecycle over stdio JSON-RPC
- [x] Local STT / TTS / VLM provider plumbing
- [x] macOS Accessibility fast-path grounding
- [ ] Reliable end-to-end click execution on real apps
- [ ] Installer / signed builds
- [ ] Windows port
- [ ] Plugin system for custom providers

## Contributing

1. Fork the repo and create a feature branch.
2. Run `pnpm dev` and make sure the app still launches on your machine.
3. For Rust changes: `cargo fmt`, `cargo clippy --all-targets -- -D warnings`, `cargo test`.
4. Open a PR — draft PRs are fine, even encouraged, for early feedback.

If you are not sure where to start, open an issue describing what you would
like to work on and I will help you scope it.

## License

MIT. See [`LICENSE`](LICENSE).
