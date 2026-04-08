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
import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


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
    from .providers.stt_mlx_whisper import MlxWhisperConfig, MlxWhisperStt

    stt_cfg = settings.get("stt", {})
    model = stt_cfg.get("mlx_model", "mlx-community/whisper-base-mlx")
    return MlxWhisperStt(MlxWhisperConfig(mlx_model=model))


def _make_vlm(settings: dict[str, Any]):
    from .providers.vlm_ollama import OllamaConfig, OllamaVlm

    vlm_cfg = settings.get("vlm", {})
    model = vlm_cfg.get("ollama_model", "qwen2.5-vl:7b")
    url = vlm_cfg.get("ollama_url", "http://localhost:11434")
    return OllamaVlm(OllamaConfig(ollama_model=model, ollama_url=url))


def _make_tts(settings: dict[str, Any]):
    from .providers.tts_kokoro import KokoroConfig, KokoroTts

    tts_cfg = settings.get("tts", {})
    voice = tts_cfg.get("kokoro_voice", "af_heart")
    speed = float(tts_cfg.get("kokoro_speed", 1.0))
    return KokoroTts(KokoroConfig(kokoro_voice=voice, kokoro_speed=speed))
