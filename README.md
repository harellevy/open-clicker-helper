# open-clicker-helper

An open-source, **offline-first** AI buddy that lives next to your cursor.
Hold a hotkey, ask a question out loud, and it will:

1. Take a screenshot of the focused app.
2. Use a local vision LLM to figure out **where you need to click**.
3. Animate a cursor on a transparent overlay showing you the path.
4. Read the answer back to you.

Inspired by [clicky.so](https://www.clicky.so/), but open source and works fully offline with local models (Ollama, mlx-whisper, Kokoro).

## Status

Early development. macOS first; Windows port planned. See [`docs/architecture.md`](docs/architecture.md).

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

## License

MIT. See [`LICENSE`](LICENSE).
