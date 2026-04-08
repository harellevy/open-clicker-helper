"""Voice round-trip pipeline: audio bytes in → STT → [Grounding|LLM] → TTS → audio bytes out.

The public entry point is `run()`, a generator that yields ``(event, payload)``
progress tuples compatible with the RPC streaming layer in `rpc.py`.

Event sequence (text-only — no image_b64):
  ("stt_start",       {})
  ("stt_done",        {"transcript": str})
  ("llm_start",       {})
  ("llm_done",        {"answer": str})
  ("tts_start",       {})
  ("tts_done",        {})
  ("result",          {"transcript": str, "answer": str, "audio_b64": str, "steps": []})

Event sequence (grounding mode — image_b64 provided):
  ("stt_start",       {})
  ("stt_done",        {"transcript": str})
  ("grounding_start", {})
  ("grounding_done",  {"steps": [{"x": float, "y": float, "explanation": str}]})
  ("tts_start",       {})
  ("tts_done",        {})
  ("result",          {"transcript": str, "answer": str, "audio_b64": str, "steps": [...]})

The RPC layer stops iterating once it sees an event named ``"result"`` and
sends that payload as the JSON-RPC result.
"""

from __future__ import annotations

import base64
import json
import logging
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


def run(
    audio_b64: str,
    image_b64: str | None,
    settings: dict[str, Any] | None,
) -> Iterator[tuple[str, Any]]:
    """Generator pipeline for a single voice round-trip.

    Args:
        audio_b64:  Base-64-encoded WAV audio from the user.
        image_b64:  Optional base-64-encoded PNG screenshot for vision context.
        settings:   Provider settings dict (see ``_make_*`` helpers below).

    Yields:
        ``(event_name, payload)`` tuples consumed by the RPC streaming layer.
        The final yield is ``("result", {...})``.
    """
    settings = settings or {}
    audio_bytes = base64.b64decode(audio_b64)
    image_bytes = base64.b64decode(image_b64) if image_b64 else None

    # ── STT ──────────────────────────────────────────────────────────────────
    yield ("stt_start", {})
    stt = _make_stt(settings)
    transcript = stt.transcribe(audio_bytes)
    yield ("stt_done", {"transcript": transcript})
    logger.info("STT: %r", transcript)

    steps: list[dict[str, Any]] = []
    answer: str = ""

    if image_bytes is not None:
        # ── Grounding mode: VLM → click coordinates ───────────────────────
        yield ("grounding_start", {})
        from . import grounding as _grounding

        vlm = _make_vlm(settings)
        result = _grounding.locate(vlm, image_bytes, transcript)
        steps = result.get("steps", [])
        # Synthesise a spoken answer from the grounding steps.
        if steps:
            answer = steps[0]["explanation"]
            if len(steps) > 1:
                answer = f"I'll do this in {len(steps)} steps. " + answer
        else:
            answer = "I couldn't find where to click for that."
        yield ("grounding_done", {"steps": steps})
        logger.info("Grounding: %d steps", len(steps))
    else:
        # ── Text-only mode: LLM → answer ──────────────────────────────────
        yield ("llm_start", {})
        vlm = _make_vlm(settings)
        answer = vlm.complete(transcript, image_bytes=None)
        yield ("llm_done", {"answer": answer})
        logger.info("LLM answer: %r", answer)

    # ── TTS ──────────────────────────────────────────────────────────────────
    yield ("tts_start", {})
    tts = _make_tts(settings)
    audio_response = tts.synthesize(answer)
    audio_response_b64 = base64.b64encode(audio_response).decode()
    yield ("tts_done", {})

    # ── Final result (consumed by rpc.py as the JSON-RPC result payload) ─────
    yield (
        "result",
        {
            "transcript": transcript,
            "answer": answer,
            "audio_b64": audio_response_b64,
            "steps": steps,
        },
    )


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
