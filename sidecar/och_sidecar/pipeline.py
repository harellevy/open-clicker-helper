"""Voice round-trip pipeline: audio bytes in → STT → [Grounding|LLM] → TTS → audio bytes out.

The public entry point is `run()`, a generator that yields ``(event, payload)``
progress tuples compatible with the RPC streaming layer in `rpc.py`.

Event sequence (text-only — no image_b64):
  ("stt_start",       {})
  ("stt_done",        {"transcript": str, "elapsed_ms": int})
  ("llm_start",       {})
  ("llm_done",        {"answer": str, "elapsed_ms": int})
  ("tts_start",       {})
  ("tts_done",        {"elapsed_ms": int})
  ("result",          {...})

Event sequence (grounding mode — image_b64 provided):
  ("stt_start",       {})
  ("stt_done",        {"transcript": str, "elapsed_ms": int})
  ("image_downscaled",{"orig_size": [w, h], "new_size": [w, h], "bytes": int,
                       "image_b64": str?})      # image_b64 only in debug mode
  ("caption_start",   {})                        # debug mode only
  ("caption_done",    {"caption": str, "elapsed_ms": int})   # debug mode only
  ("grounding_start", {})
  ("grounding_done",  {"steps": [...], "raw": str?, "elapsed_ms": int})
  ("tts_start",       {})
  ("tts_done",        {"elapsed_ms": int})
  ("result",          {"transcript": str, "answer": str, "audio_b64": str,
                       "steps": [...], "debug": {...}?})

The RPC layer stops iterating once it sees an event named ``"result"`` and
sends that payload as the JSON-RPC result.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Iterator
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Provider cache ────────────────────────────────────────────────────────────
#
# Each pipeline.run() call would otherwise instantiate fresh STT/VLM/TTS
# providers, which for KokoroTts means loading a Torch KPipeline (model +
# voices) on every recording. Python/PyTorch don't always free those promptly,
# so RSS climbs request after request and looks like a leak.
#
# Cache one instance per (kind, settings-fingerprint). When the user changes
# settings the fingerprint changes and we drop the old instance — fine because
# only the *latest* instance is reachable, so the previous model can actually
# be GC'd.
_provider_cache: dict[str, tuple[str, Any]] = {}


def _cached(kind: str, fingerprint_obj: Any, build: Callable[[], Any]) -> Any:
    fingerprint = json.dumps(fingerprint_obj, sort_keys=True, default=str)
    cur = _provider_cache.get(kind)
    if cur is not None and cur[0] == fingerprint:
        return cur[1]
    instance = build()
    _provider_cache[kind] = (fingerprint, instance)
    return instance


def reset_provider_cache() -> None:
    """Drop every cached provider. Used by tests."""
    _provider_cache.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _elapsed_ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _get_debug(settings: dict[str, Any]) -> dict[str, Any]:
    """Read the `debug` block from settings, tolerating missing/legacy shapes."""
    dbg = settings.get("debug") or {}
    if not isinstance(dbg, dict):
        return {}
    return dbg


def _get_system_prompts(settings: dict[str, Any]) -> dict[str, Any]:
    sp = settings.get("system_prompts") or {}
    if not isinstance(sp, dict):
        return {}
    return sp


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run(
    audio_b64: str,
    image_b64: str | None,
    settings: dict[str, Any] | None,
) -> Iterator[tuple[str, Any]]:
    """Generator pipeline for a single voice round-trip."""
    settings = settings or {}
    debug_cfg = _get_debug(settings)
    debug_enabled = bool(debug_cfg.get("enabled"))
    prompts = _get_system_prompts(settings)
    grounding_prompt = prompts.get("grounding") or None
    caption_prompt = prompts.get("caption") or None

    audio_bytes = base64.b64decode(audio_b64)
    image_bytes = base64.b64decode(image_b64) if image_b64 else None

    pipeline_start = time.monotonic()
    timings: dict[str, int] = {}

    # ── STT ──────────────────────────────────────────────────────────────────
    yield ("stt_start", {})
    stt_t0 = time.monotonic()
    stt = _make_stt(settings)
    transcript = stt.transcribe(audio_bytes)
    timings["stt_ms"] = _elapsed_ms(stt_t0)
    yield ("stt_done", {"transcript": transcript, "elapsed_ms": timings["stt_ms"]})
    logger.info("STT: %r (%d ms)", transcript, timings["stt_ms"])

    steps: list[dict[str, Any]] = []
    answer: str = ""
    debug_payload: dict[str, Any] = {
        "transcript": transcript,
    }

    if image_bytes is not None:
        # ── Downscale the screenshot before anything else sees it ─────────
        from . import imaging as _imaging

        downscale_t0 = time.monotonic()
        small_png, orig_size, new_size = _imaging.downscale_png(image_bytes)
        timings["downscale_ms"] = _elapsed_ms(downscale_t0)

        if new_size != (0, 0):
            logger.info(
                "screenshot downscaled: %s → %s (%d → %d bytes, %d ms)",
                orig_size,
                new_size,
                len(image_bytes),
                len(small_png),
                timings["downscale_ms"],
            )
            image_for_vlm = small_png
        else:
            image_for_vlm = image_bytes  # downscale failed, fall back

        downscale_event: dict[str, Any] = {
            "orig_size": list(orig_size),
            "new_size": list(new_size),
            "orig_bytes": len(image_bytes),
            "new_bytes": len(image_for_vlm),
            "elapsed_ms": timings["downscale_ms"],
        }
        if debug_enabled:
            # Ship the tiny image down to the overlay so it can draw the
            # "what the VLM sees" preview.
            downscale_event["image_b64"] = base64.b64encode(image_for_vlm).decode()
        yield ("image_downscaled", downscale_event)

        # ── Optional caption step (debug mode only) ───────────────────────
        caption_text = ""
        if debug_enabled:
            from . import grounding as _grounding

            vlm = _make_vlm(settings)
            yield ("caption_start", {})
            caption_t0 = time.monotonic()
            try:
                caption_text = _grounding.caption(
                    vlm, image_for_vlm, system_prompt=caption_prompt
                )
            except Exception as exc:  # noqa: BLE001 — caption is best-effort
                caption_text = f"(caption failed: {exc})"
                logger.warning("caption failed: %s", exc)
            timings["caption_ms"] = _elapsed_ms(caption_t0)
            yield (
                "caption_done",
                {"caption": caption_text, "elapsed_ms": timings["caption_ms"]},
            )
            debug_payload["caption"] = caption_text

        # ── Grounding: VLM → click coordinates ────────────────────────────
        from . import grounding as _grounding

        yield ("grounding_start", {})
        grounding_t0 = time.monotonic()
        vlm = _make_vlm(settings)
        result = _grounding.locate(
            vlm,
            image_for_vlm,
            transcript,
            system_prompt=grounding_prompt,
        )
        timings["grounding_ms"] = _elapsed_ms(grounding_t0)

        steps = result.get("steps", [])
        if steps:
            answer = steps[0]["explanation"]
            if len(steps) > 1:
                answer = f"I'll do this in {len(steps)} steps. " + answer
        else:
            answer = "I couldn't find where to click for that."

        grounding_event: dict[str, Any] = {
            "steps": steps,
            "elapsed_ms": timings["grounding_ms"],
        }
        if debug_enabled:
            grounding_event["raw"] = result.get("raw", "")
        yield ("grounding_done", grounding_event)
        logger.info("Grounding: %d steps (%d ms)", len(steps), timings["grounding_ms"])

        if debug_enabled:
            debug_payload.update(
                {
                    "screenshot_b64": downscale_event.get("image_b64", ""),
                    "orig_size": list(orig_size),
                    "new_size": list(new_size),
                    "orig_bytes": len(image_bytes),
                    "new_bytes": len(image_for_vlm),
                    "grounding_raw": result.get("raw", ""),
                    "steps": steps,
                }
            )
    else:
        # ── Text-only mode: LLM → answer ──────────────────────────────────
        yield ("llm_start", {})
        llm_t0 = time.monotonic()
        vlm = _make_vlm(settings)
        answer = vlm.complete(transcript, image_bytes=None)
        timings["llm_ms"] = _elapsed_ms(llm_t0)
        yield ("llm_done", {"answer": answer, "elapsed_ms": timings["llm_ms"]})
        logger.info("LLM answer: %r (%d ms)", answer, timings["llm_ms"])

    # ── TTS ──────────────────────────────────────────────────────────────────
    yield ("tts_start", {})
    tts_t0 = time.monotonic()
    tts = _make_tts(settings)
    audio_response = tts.synthesize(answer)
    timings["tts_ms"] = _elapsed_ms(tts_t0)
    audio_response_b64 = base64.b64encode(audio_response).decode()
    yield ("tts_done", {"elapsed_ms": timings["tts_ms"]})

    timings["total_ms"] = _elapsed_ms(pipeline_start)
    if debug_enabled:
        debug_payload["timings"] = timings
        debug_payload["answer"] = answer

    # ── Final result ─────────────────────────────────────────────────────────
    result_payload: dict[str, Any] = {
        "transcript": transcript,
        "answer": answer,
        "audio_b64": audio_response_b64,
        "steps": steps,
        "timings": timings,
    }
    if debug_enabled:
        result_payload["debug"] = debug_payload
    yield ("result", result_payload)


# ── Provider factories ────────────────────────────────────────────────────────

def _make_stt(settings: dict[str, Any]):
    stt_cfg = settings.get("stt", {})
    provider = stt_cfg.get("provider", "mlx-whisper")

    if provider == "openai":
        def build():
            from .providers.stt_openai import OpenAIStt, OpenAISttConfig
            return OpenAIStt(OpenAISttConfig(openai_key=stt_cfg.get("openai_key") or ""))
        return _cached("stt", ["openai", stt_cfg.get("openai_key") or ""], build)

    # default: mlx-whisper
    model = stt_cfg.get("mlx_model", "mlx-community/whisper-base-mlx")

    def build():
        from .providers.stt_mlx_whisper import MlxWhisperConfig, MlxWhisperStt
        return MlxWhisperStt(MlxWhisperConfig(mlx_model=model))

    return _cached("stt", ["mlx-whisper", model], build)


def _make_vlm(settings: dict[str, Any]):
    vlm_cfg = settings.get("vlm", {})
    provider = vlm_cfg.get("provider", "ollama")

    if provider == "openai":
        key = vlm_cfg.get("openai_key") or ""
        model = vlm_cfg.get("openai_model", "gpt-4o")

        def build():
            from .providers.vlm_openai import OpenAIVlm, OpenAIVlmConfig
            return OpenAIVlm(OpenAIVlmConfig(openai_key=key, openai_model=model))

        return _cached("vlm", ["openai", key, model], build)

    if provider == "anthropic":
        key = vlm_cfg.get("anthropic_key") or ""

        def build():
            from .providers.vlm_anthropic import AnthropicVlm, AnthropicVlmConfig
            return AnthropicVlm(AnthropicVlmConfig(anthropic_key=key))

        return _cached("vlm", ["anthropic", key], build)

    # default: ollama
    model = vlm_cfg.get("ollama_model", "qwen2.5vl:7b")
    url = vlm_cfg.get("ollama_url", "http://localhost:11434")

    def build():
        from .providers.vlm_ollama import OllamaConfig, OllamaVlm
        return OllamaVlm(OllamaConfig(ollama_model=model, ollama_url=url))

    return _cached("vlm", ["ollama", model, url], build)


def _make_tts(settings: dict[str, Any]):
    tts_cfg = settings.get("tts", {})
    provider = tts_cfg.get("provider", "kokoro")

    if provider == "openai":
        key = tts_cfg.get("openai_key") or ""
        voice = tts_cfg.get("openai_voice", "nova")

        def build():
            from .providers.tts_openai import OpenAITts, OpenAITtsConfig
            return OpenAITts(OpenAITtsConfig(openai_key=key, voice=voice))

        return _cached("tts", ["openai", key, voice], build)

    # default: kokoro
    voice = tts_cfg.get("kokoro_voice", "af_heart")
    speed = float(tts_cfg.get("kokoro_speed", 1.0))

    def build():
        from .providers.tts_kokoro import KokoroConfig, KokoroTts
        return KokoroTts(KokoroConfig(kokoro_voice=voice, kokoro_speed=speed))

    return _cached("tts", ["kokoro", voice, speed], build)
