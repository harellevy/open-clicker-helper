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


def _get_grounding_opts(settings: dict[str, Any]) -> dict[str, Any]:
    """Read the `grounding` block from settings (knobs like `refine`, `mode`)."""
    g = settings.get("grounding") or {}
    if not isinstance(g, dict):
        return {}
    return g


def _normalise_grounding_mode(raw: Any) -> str:
    """Clamp the mode string to one of the three supported values."""
    mode = str(raw or "auto").strip().lower()
    if mode not in ("auto", "ax", "vlm"):
        return "auto"
    return mode


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run(
    audio_b64: str,
    image_b64: str | None,
    settings: dict[str, Any] | None,
    ax_candidates: list[dict[str, Any]] | None = None,
) -> Iterator[tuple[str, Any]]:
    """Generator pipeline for a single voice round-trip."""
    settings = settings or {}
    debug_cfg = _get_debug(settings)
    debug_enabled = bool(debug_cfg.get("enabled"))
    prompts = _get_system_prompts(settings)
    grounding_prompt = prompts.get("grounding") or None
    caption_prompt = prompts.get("caption") or None
    refine_prompt = prompts.get("refine") or None
    grounding_opts = _get_grounding_opts(settings)
    # Two-pass crop-and-refine defaults on — a single extra VLM call per
    # step massively tightens coordinate accuracy on full-res screenshots.
    refine_enabled = bool(grounding_opts.get("refine", True))
    grounding_mode = _normalise_grounding_mode(grounding_opts.get("mode"))

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

    # ── Early bail-out: nothing was said ─────────────────────────────────────
    # The user held the hotkey but STT returned empty/whitespace — usually an
    # accidental tap or pure-silence capture. Without a question, grounding
    # has nothing to aim at and an image alone would just burn the VLM on a
    # noop. Short-circuit with a ``cancelled`` result so the overlay can show
    # a brief message and the user can retry.
    if not transcript.strip():
        timings["total_ms"] = _elapsed_ms(pipeline_start)
        logger.info("empty transcript — cancelling pipeline")
        yield (
            "result",
            {
                "transcript": "",
                "answer": "",
                "audio_b64": "",
                "steps": [],
                "timings": timings,
                "cancelled": "empty_transcript",
            },
        )
        return

    steps: list[dict[str, Any]] = []
    answer: str = ""
    debug_payload: dict[str, Any] = {
        "transcript": transcript,
    }

    if image_bytes is not None:
        from . import grounding as _grounding
        from . import imaging as _imaging

        # ── AX-tree fast path (auto + ax modes) ──────────────────────────
        # When the focused app exposes a useful Accessibility tree, we can
        # locate the target without loading an image into the VLM at all.
        # The Rust shell already collected and normalised the candidates;
        # the sidecar's only job is to match question text against labels.
        ax_result: dict[str, Any] | None = None
        if grounding_mode in ("auto", "ax") and ax_candidates:
            ax_t0 = time.monotonic()
            ax_result = _grounding.locate_from_ax(ax_candidates, transcript)
            timings["ax_ms"] = _elapsed_ms(ax_t0)
            yield (
                "ax_match",
                {
                    "hit": ax_result is not None,
                    "candidates": len(ax_candidates),
                    "elapsed_ms": timings["ax_ms"],
                },
            )
            logger.info(
                "AX match: %s over %d candidates (%d ms)",
                "hit" if ax_result else "miss",
                len(ax_candidates),
                timings["ax_ms"],
            )

        orig_size: tuple[int, int] = (0, 0)
        new_size: tuple[int, int] = (0, 0)
        image_for_vlm: bytes = image_bytes
        downscale_event: dict[str, Any] = {}
        result: dict[str, Any] = {}
        rough_steps: list[dict[str, Any]] = []

        if ax_result is not None:
            # AX hit: skip VLM + refinement entirely. Coordinates are
            # already pixel-accurate because they came straight from the
            # accessibility API.
            steps = list(ax_result.get("steps", []))
            rough_steps = list(steps)
            result = ax_result
            grounding_event = {
                "steps": steps,
                "elapsed_ms": timings.get("ax_ms", 0),
                "source": "ax",
            }
            if debug_enabled:
                grounding_event["raw"] = ax_result.get("raw", "")
            yield ("grounding_done", grounding_event)
        elif grounding_mode == "ax":
            # AX-only mode, no match: don't call the VLM at all.
            # Emit an empty grounding_done so the UI can report "no target".
            steps = []
            yield (
                "grounding_done",
                {"steps": [], "elapsed_ms": timings.get("ax_ms", 0), "source": "ax"},
            )
            logger.info("AX-only mode: no candidate matched, skipping VLM")
        else:
            # ── VLM grounding path ────────────────────────────────────────
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

            downscale_event = {
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

            # ── Optional caption step (debug mode only) ───────────────────
            caption_text = ""
            if debug_enabled:
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

            # ── Grounding: VLM → click coordinates ────────────────────────
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

            grounding_event = {
                "steps": steps,
                "elapsed_ms": timings["grounding_ms"],
                "source": "vlm",
            }
            if debug_enabled:
                grounding_event["raw"] = result.get("raw", "")
            yield ("grounding_done", grounding_event)
            logger.info(
                "Grounding: %d steps (%d ms)", len(steps), timings["grounding_ms"]
            )

            rough_steps = list(steps)  # copy so debug can compare

        # ── Refinement pass (two-pass crop-and-refine) ───────────────────
        # The first pass ran on the downscaled image (~1/8 area) which is
        # fast but loses pixel precision. Now for each rough target, crop
        # a window from the FULL-resolution screenshot around that point
        # and ask the VLM to pinpoint the exact centre. Refined coords are
        # mapped back to full-image normalised space.
        #
        # AX-sourced steps skip refinement: the bounding box came directly
        # from the accessibility API so it's already pixel-perfect.
        if steps and refine_enabled and result.get("source") != "ax" and ax_result is None:
            vlm = _make_vlm(settings)
            refine_t0 = time.monotonic()
            yield ("refine_start", {"count": len(steps)})
            refined_steps: list[dict[str, Any]] = []
            for i, step in enumerate(steps):
                crop_result = _imaging.crop_around(
                    image_bytes, step["x"], step["y"]
                )
                if crop_result is None:
                    refined_steps.append(step)
                    continue
                crop_png, (cx0, cy0, cw, ch) = crop_result
                refined = _grounding.refine(
                    vlm,
                    crop_png,
                    transcript,
                    system_prompt=refine_prompt,
                )
                if refined is None:
                    refined_steps.append(step)
                    continue
                # Map normalised-within-crop → normalised-in-full-image.
                full_x = cx0 + refined["x"] * cw
                full_y = cy0 + refined["y"] * ch
                refined_steps.append(
                    {
                        "x": max(0.0, min(1.0, full_x)),
                        "y": max(0.0, min(1.0, full_y)),
                        # Keep the first-pass explanation — it describes the
                        # *element*, which is what the user sees; the refine
                        # call explains only the precise pixel.
                        "explanation": step.get("explanation", ""),
                    }
                )
                logger.info(
                    "refine step %d: (%.3f, %.3f) → (%.3f, %.3f)",
                    i,
                    step["x"],
                    step["y"],
                    refined_steps[-1]["x"],
                    refined_steps[-1]["y"],
                )
            timings["refine_ms"] = _elapsed_ms(refine_t0)
            steps = refined_steps
            yield (
                "refine_done",
                {"steps": steps, "elapsed_ms": timings["refine_ms"]},
            )

        if steps:
            answer = steps[0]["explanation"]
            if len(steps) > 1:
                answer = f"I'll do this in {len(steps)} steps. " + answer
        else:
            answer = "I couldn't find where to click for that."

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
                    "rough_steps": rough_steps,
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
