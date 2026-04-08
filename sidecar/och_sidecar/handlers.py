"""RPC method dispatcher.

Keep this file small: each method is a one-line wrapper around domain code in
`pipeline.py`, `providers/`, etc. Adding a new method = registering one entry.
"""

from __future__ import annotations

from typing import Any

from . import __version__
from .rpc import Dispatcher

# P3/P4 will replace these stubs with real provider/pipeline calls.


def _ping(_params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "version": __version__}


def _providers_list(_params: dict[str, Any]) -> dict[str, list[str]]:
    # P2 populates this from the providers registry.
    return {"stt": [], "tts": [], "llm": [], "vlm": []}


def _providers_test(_params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "error": "not implemented yet"}


def _pipeline_run(_params: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "pipeline.run is wired in P3 (voice) and P4 (vision)",
    }


def build_dispatcher() -> Dispatcher:
    return {
        "ping": _ping,
        "providers.list": _providers_list,
        "providers.test": _providers_test,
        "pipeline.run": _pipeline_run,
    }
