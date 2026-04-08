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
            model=params.get("vlm_model", "qwen2.5-vl:7b"),
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
        model=params.get("model", "qwen2.5-vl:7b"),
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
        model=params.get("model", "qwen2.5-vl:7b"),
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
# Pipeline (wired in P3 / P4)
# ──────────────────────────────────────────────────────────────────────────────

def _pipeline_run(_params: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "pipeline.run is wired in P3 (voice) and P4 (vision)",
    }


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
    }
