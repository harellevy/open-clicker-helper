# Providers

Three capabilities — speech-to-text, vision LLM, text-to-speech — each with
an offline default and one or more cloud fallbacks. Provider choice is
persisted in `settings.json` under `stt.provider`, `vlm.provider`, and
`tts.provider`, and the sidecar's factory functions in `pipeline.py`
dispatch on those strings.

All cloud providers require a user-supplied API key. Keys live in the
local settings store only; they are never sent anywhere except the target
API.

## Speech-to-Text (STT)

| Provider       | ID             | Offline? | Notes                                                                                  |
| -------------- | -------------- | -------- | -------------------------------------------------------------------------------------- |
| mlx-whisper    | `mlx-whisper`  | yes      | Default on Apple Silicon. Uses `mlx-whisper` with HuggingFace-cached weights.          |
| OpenAI Whisper | `openai`       | no       | Cloud fallback. Needs `stt.openai_key`. Useful on Intel Macs or when MLX isn't set up. |

**Default model:** `mlx-community/whisper-base-mlx` (`settings.stt.mlx_model`).
Swap to `whisper-small-mlx` or `whisper-medium-mlx` if you need higher
accuracy at the cost of latency.

**Where to look in code:**
- `sidecar/och_sidecar/providers/stt_mlx_whisper.py`
- `sidecar/och_sidecar/providers/stt_openai.py`
- Factory: `_make_stt()` in `sidecar/och_sidecar/pipeline.py`

## Vision LLM (VLM)

| Provider  | ID          | Offline? | Notes                                                                          |
| --------- | ----------- | -------- | ------------------------------------------------------------------------------ |
| Ollama    | `ollama`    | yes      | Default. Expects an `ollama serve` instance at `settings.vlm.ollama_url`.      |
| OpenAI    | `openai`    | no       | GPT-4o class vision. Needs `vlm.openai_key`.                                   |
| Anthropic | `anthropic` | no       | Claude with vision. Needs `vlm.anthropic_key`.                                 |

**Default Ollama model:** `qwen2.5vl:7b`. Pull it once with
`ollama pull qwen2.5vl:7b`; the first-run wizard can stream the pull
progress into the UI.

**Grounding mode** (`settings.grounding.mode`):
- `auto` (default) — try the macOS AX-tree fast path first (see
  `platform::macos::ax::focused_window_candidates` and
  `grounding.locate_from_ax`); fall back to VLM grounding on miss.
- `ax` — AX-tree only. Native apps only; no VLM spend.
- `vlm` — VLM only. Games, web content, non-native UIs.

**Refinement** (`settings.grounding.refine`): when `true`, each rough
VLM target is re-grounded on a zoomed crop to tighten pixel accuracy.
Costs roughly one extra VLM call per step; disable for latency.

**Where to look in code:**
- `sidecar/och_sidecar/providers/vlm_ollama.py`
- `sidecar/och_sidecar/providers/vlm_openai.py`
- `sidecar/och_sidecar/providers/vlm_anthropic.py`
- Grounding prompt + parser: `sidecar/och_sidecar/grounding.py`

## Text-to-Speech (TTS)

| Provider   | ID       | Offline? | Notes                                                       |
| ---------- | -------- | -------- | ----------------------------------------------------------- |
| Kokoro     | `kokoro` | yes      | Default. Tiny (~82M) English TTS model shipped via `uv`.    |
| OpenAI TTS | `openai` | no       | Cloud fallback. Needs `tts.openai_key`.                     |

**Kokoro voices** stored in `settings.tts.kokoro_voice`:
`af_heart` (default), `am_adam`, `bf_emma`, `bm_lewis`.

**OpenAI voices** stored in `settings.tts.openai_voice`: `alloy`, `echo`,
`fable`, `nova` (default), `onyx`, `shimmer`.

**Where to look in code:**
- `sidecar/och_sidecar/providers/tts_kokoro.py`
- `sidecar/och_sidecar/providers/tts_openai.py`

## Adding a new provider

1. Drop a new module under `sidecar/och_sidecar/providers/` that
   implements the shape expected by `base.py` for its category
   (`transcribe(bytes) -> str` for STT, `complete(prompt, image_bytes=)`
   for VLM, `synthesize(text) -> bytes` for TTS).
2. Extend the matching factory in `pipeline.py` (`_make_stt`,
   `_make_vlm`, `_make_tts`) with a new branch on the provider string.
   Keep construction lazy and memoised via `_cached()` so switching
   providers in settings doesn't reload models until actually needed.
3. Add a row to `sidecar/tests/test_pipeline.py` covering the new
   provider string round-trips through the factory.
4. Expose the new provider ID to the frontend by widening the union in
   `ui/src/lib/api.ts` (`SttSettings`, `VlmSettings`, or `TtsSettings`)
   and the matching `store.rs` setter so settings round-trip cleanly.
5. Add a Providers-page option in `ui/src/settings/pages/Providers.tsx`.

Don't edit `pipeline.py`'s per-stage loop for vendor-specific behaviour;
keep all vendor quirks inside the provider module. The pipeline should
only know the three verbs.

## Connection testing

Every provider exposes a `providers.test` RPC path that returns
`{ok, latency_ms, error?}`. The Providers settings page surfaces it as
a "Test connection" button per section so users can verify an API key
or an Ollama URL without waiting for the next hotkey press.
