"""RPC method dispatcher.

Keep this file small: each method is a one-line wrapper around domain code in
`setup.py`, `pipeline.py`, `providers/`, etc.
"""

from __future__ import annotations

from typing import Any

from . import __version__
from .rpc import Dispatcher
from . import setup as _setup


# ──────────────────────────────────────────────────────────────────────────────
# Core
# ──────────────────────────────────────────────────────────────────────────────

def _ping(_params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "version": __version__}


# ──────────────────────────────────────────────────────────────────────────────
# Setup — check and download offline model dependencies
# ──────────────────────────────────────────────────────────────────────────────

def _setup_check(params: dict[str, Any]) -> dict[str, Any]:
    """Check all three dependency groups at once.

    Returns a dict with keys ``stt``, ``vlm``, ``tts`` each containing the
    same status dict as the individual check methods.
    """
    return {
        "stt": _setup.check_stt(
            model=params.get("stt_model", "mlx-community/whisper-base-mlx")
        ),
        "vlm": _setup.check_vlm(
            model=params.get("vlm_model", "qwen2.5vl:7b"),
            base_url=params.get("ollama_url", "http://localhost:11434"),
        ),
        "tts": _setup.check_tts(
            voice=params.get("tts_voice", "af_heart")
        ),
    }


def _setup_check_stt(params: dict[str, Any]) -> dict[str, Any]:
    return _setup.check_stt(
        model=params.get("model", "mlx-community/whisper-base-mlx")
    )


def _setup_check_vlm(params: dict[str, Any]) -> dict[str, Any]:
    return _setup.check_vlm(
        model=params.get("model", "qwen2.5vl:7b"),
        base_url=params.get("base_url", "http://localhost:11434"),
    )


def _setup_check_tts(params: dict[str, Any]) -> dict[str, Any]:
    return _setup.check_tts(voice=params.get("voice", "af_heart"))


def _setup_download_stt(params: dict[str, Any]):
    """Streaming — yields (event, payload) progress tuples."""
    return _setup.download_stt(
        model=params.get("model", "mlx-community/whisper-base-mlx")
    )


def _setup_download_vlm(params: dict[str, Any]):
    """Streaming — yields (event, payload) progress tuples."""
    return _setup.download_vlm(
        model=params.get("model", "qwen2.5vl:7b"),
        base_url=params.get("base_url", "http://localhost:11434"),
    )


def _setup_download_tts(params: dict[str, Any]):
    """Streaming — yields (event, payload) progress tuples."""
    return _setup.download_tts(voice=params.get("voice", "af_heart"))


# ──────────────────────────────────────────────────────────────────────────────
# Providers — connectivity tests used by the Settings > Providers page
# ──────────────────────────────────────────────────────────────────────────────

def _providers_list(_params: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "stt": ["mlx-whisper", "openai"],
        "llm": [],
        "vlm": ["ollama", "openai", "anthropic"],
        "tts": ["kokoro", "openai"],
    }


def _providers_test(params: dict[str, Any]) -> dict[str, Any]:
    """params: {type: "stt"|"vlm"|"tts", provider: str, config: {...}}"""
    return _setup.test_provider(
        provider_type=params.get("type", ""),
        provider_id=params.get("provider", ""),
        config=params.get("config", {}),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline — full voice round-trip (P3)
# ──────────────────────────────────────────────────────────────────────────────

def _pipeline_run(params: dict[str, Any]):
    """Streaming handler: yields (event, payload) progress tuples.

    params:
        audio_b64      – base-64-encoded WAV audio (required)
        image_b64      – base-64-encoded PNG screenshot, optional
        settings       – provider settings dict, optional
        ax_candidates  – list of macOS AX-tree candidates (see AxCandidate
                         in Rust) with normalised x/y/width/height in
                         [0, 1], optional. Ignored on non-macOS shells.
    """
    from . import pipeline as _pipeline

    return _pipeline.run(
        params["audio_b64"],
        params.get("image_b64"),
        params.get("settings", {}),
        ax_candidates=params.get("ax_candidates"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Grounding — re-ground a single question against a new screenshot (P4.1)
# ──────────────────────────────────────────────────────────────────────────────

def _grounding_locate(params: dict[str, Any]) -> dict[str, Any]:
    """Re-ground a question against a (new) screenshot without running STT/TTS.

    Used by the iterative multi-step loop: after each click Rust captures a
    fresh screenshot and calls this to re-ground the next step. The screenshot
    is downscaled the same way as in pipeline.run() to keep upload times low.

    AX candidates are honoured here too — when the caller provides a fresh
    list (Rust does this on every iterative step) and the user has AX mode
    enabled, we try the fast path first and only fall back to the VLM on a
    miss.

    params:
        image_b64      – base-64-encoded PNG screenshot (required)
        question       – the user's original transcribed question (required)
        settings       – provider settings dict, optional
        ax_candidates  – list of macOS AX-tree candidates, optional
    """
    import base64

    from . import grounding as _grounding
    from . import imaging as _imaging
    from .pipeline import _make_vlm, _normalise_grounding_mode

    question = params["question"]
    settings = params.get("settings") or {}
    ax_candidates = params.get("ax_candidates")

    grounding_cfg = settings.get("grounding") or {}
    mode = _normalise_grounding_mode(
        grounding_cfg.get("mode") if isinstance(grounding_cfg, dict) else None
    )

    # AX fast path — same semantics as pipeline.run().
    if mode in ("auto", "ax") and ax_candidates:
        ax_result = _grounding.locate_from_ax(ax_candidates, question)
        if ax_result is not None:
            return ax_result
        if mode == "ax":
            # AX-only mode + miss → return empty steps, never touch the VLM.
            return {"steps": [], "raw": "", "source": "ax"}

    image_bytes = base64.b64decode(params["image_b64"])
    small_png, _orig, _new = _imaging.downscale_png(image_bytes)
    system_prompt = (settings.get("system_prompts") or {}).get("grounding") or None

    vlm = _make_vlm(settings)
    result = _grounding.locate(vlm, small_png, question, system_prompt=system_prompt)
    result.setdefault("source", "vlm")
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────

def build_dispatcher() -> Dispatcher:
    return {
        "ping": _ping,
        # Setup checks (instant)
        "setup.check": _setup_check,
        "setup.check_stt": _setup_check_stt,
        "setup.check_vlm": _setup_check_vlm,
        "setup.check_tts": _setup_check_tts,
        # Setup downloads (streaming generators)
        "setup.download_stt": _setup_download_stt,
        "setup.download_vlm": _setup_download_vlm,
        "setup.download_tts": _setup_download_tts,
        # Provider management
        "providers.list": _providers_list,
        "providers.test": _providers_test,
        # Pipeline
        "pipeline.run": _pipeline_run,
        # Iterative grounding (P4.1)
        "grounding.locate": _grounding_locate,
    }
