"""First-run setup helpers: check and download open-source model dependencies.

Each public function is either:
- A plain check that returns a status dict immediately.
- A generator that yields (event, payload) progress tuples and finishes with
  ("result", {...}).  The RPC server turns those yields into JSON-RPC
  notifications so the React UI can render a live progress bar.

Dependency map
--------------
STT  → mlx-whisper  (Apple Silicon offline) or openai (cloud)
VLM  → Ollama + qwen2.5-vl (offline) or openai / anthropic (cloud)
TTS  → kokoro-onnx  (offline) or openai (cloud)
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

# ──────────────────────────────────────────────────────────────────────────────
# STT — mlx-whisper
# ──────────────────────────────────────────────────────────────────────────────

def check_stt(model: str = "mlx-community/whisper-base-mlx") -> dict[str, Any]:
    """Return install + download status for the mlx-whisper model."""
    installed = _is_importable("mlx_whisper")
    if not installed:
        return {
            "installed": False,
            "model_cached": False,
            "model": model,
            "message": "mlx-whisper is not installed. Run: uv pip install mlx-whisper",
        }
    cached = _mlx_whisper_model_cached(model)
    return {
        "installed": True,
        "model_cached": cached,
        "model": model,
        "message": "ready" if cached else f"model weights not cached ({model})",
    }


def download_stt(
    model: str = "mlx-community/whisper-base-mlx",
) -> Iterator[tuple[str, Any]]:
    """Download mlx-whisper model weights via huggingface_hub snapshot_download."""
    yield ("status", {"step": "stt", "message": f"Checking mlx-whisper install…"})

    if not _is_importable("mlx_whisper"):
        yield ("status", {"step": "stt", "message": "Installing mlx-whisper via uv…"})
        try:
            _uv_add("mlx-whisper")
        except RuntimeError as e:
            yield ("result", {"ok": False, "step": "stt", "error": str(e)})
            return

    yield ("status", {"step": "stt", "message": f"Downloading model {model}…"})
    try:
        _hf_download_with_progress("stt", model)
    except Exception as e:  # noqa: BLE001
        yield ("result", {"ok": False, "step": "stt", "error": str(e)})
        return

    yield ("result", {"ok": True, "step": "stt", "model": model})


# ──────────────────────────────────────────────────────────────────────────────
# VLM — Ollama
# ──────────────────────────────────────────────────────────────────────────────

def check_vlm(
    model: str = "qwen2.5-vl:7b",
    base_url: str = "http://localhost:11434",
) -> dict[str, Any]:
    """Return Ollama availability + whether the model is already pulled."""
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=3)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return {
            "ollama_running": False,
            "model_pulled": False,
            "model": model,
            "message": "Ollama is not running. Download from https://ollama.com/download",
        }

    tags = resp.json().get("models", [])
    names = [m.get("name", "") for m in tags]
    pulled = any(n == model or n.startswith(model.split(":")[0]) for n in names)
    return {
        "ollama_running": True,
        "model_pulled": pulled,
        "model": model,
        "available_models": names,
        "message": "ready" if pulled else f"model not pulled ({model})",
    }


def download_vlm(
    model: str = "qwen2.5-vl:7b",
    base_url: str = "http://localhost:11434",
) -> Iterator[tuple[str, Any]]:
    """Pull a model via the Ollama streaming API, yielding progress events."""
    yield ("status", {"step": "vlm", "message": "Checking Ollama…"})

    # Confirm Ollama is reachable first.
    try:
        httpx.get(f"{base_url}/api/tags", timeout=3).raise_for_status()
    except Exception:  # noqa: BLE001
        yield (
            "result",
            {
                "ok": False,
                "step": "vlm",
                "error": "Ollama is not running. Download from https://ollama.com/download",
            },
        )
        return

    yield ("status", {"step": "vlm", "message": f"Pulling {model}…"})

    try:
        with httpx.stream(
            "POST",
            f"{base_url}/api/pull",
            json={"name": model, "stream": True},
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    data: dict[str, Any] = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                status_msg = data.get("status", "")

                # Digest-level download progress.
                completed = data.get("completed")
                total = data.get("total")
                if completed is not None and total and total > 0:
                    pct = int(completed / total * 100)
                    yield (
                        "progress",
                        {"step": "vlm", "progress": pct, "message": status_msg},
                    )
                else:
                    yield ("status", {"step": "vlm", "message": status_msg})

                if status_msg == "success":
                    break

    except httpx.HTTPStatusError as e:
        yield ("result", {"ok": False, "step": "vlm", "error": str(e)})
        return
    except Exception as e:  # noqa: BLE001
        yield ("result", {"ok": False, "step": "vlm", "error": str(e)})
        return

    yield ("result", {"ok": True, "step": "vlm", "model": model})


# ──────────────────────────────────────────────────────────────────────────────
# TTS — Kokoro
# ──────────────────────────────────────────────────────────────────────────────

def check_tts(voice: str = "af_heart") -> dict[str, Any]:
    """Return install + voice-model status for kokoro-onnx."""
    installed = _is_importable("kokoro")
    if not installed:
        return {
            "installed": False,
            "voice_ready": False,
            "voice": voice,
            "message": "kokoro-onnx is not installed",
        }
    voice_ready = _kokoro_voice_cached(voice)
    return {
        "installed": True,
        "voice_ready": voice_ready,
        "voice": voice,
        "message": "ready" if voice_ready else f"voice model not cached ({voice})",
    }


def download_tts(voice: str = "af_heart") -> Iterator[tuple[str, Any]]:
    """Install kokoro-onnx and download the requested voice model."""
    yield ("status", {"step": "tts", "message": "Checking kokoro-onnx install…"})

    if not _is_importable("kokoro"):
        yield ("status", {"step": "tts", "message": "Installing kokoro-onnx via uv…"})
        try:
            _uv_add("kokoro-onnx soundfile")
        except RuntimeError as e:
            yield ("result", {"ok": False, "step": "tts", "error": str(e)})
            return

    yield ("status", {"step": "tts", "message": f"Downloading voice model {voice}…"})
    try:
        # kokoro-onnx fetches voices from HuggingFace on first use.
        # We trigger that by importing and calling a lightweight check.
        _hf_download_with_progress("tts", f"hexgrad/Kokoro-82M")
    except Exception as e:  # noqa: BLE001
        yield ("result", {"ok": False, "step": "tts", "error": str(e)})
        return

    yield ("result", {"ok": True, "step": "tts", "voice": voice})


# ──────────────────────────────────────────────────────────────────────────────
# Provider connectivity test (used by Providers settings page)
# ──────────────────────────────────────────────────────────────────────────────

def test_provider(provider_type: str, provider_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Quick connectivity / credential check for a named provider.

    Returns `{ok: bool, latency_ms?: int, error?: str}`.
    """
    import time

    t0 = time.monotonic()
    try:
        if provider_id == "ollama":
            base_url = config.get("ollama_url", "http://localhost:11434")
            httpx.get(f"{base_url}/api/tags", timeout=5).raise_for_status()

        elif provider_id == "openai":
            key = config.get("openai_key") or os.environ.get("OPENAI_API_KEY", "")
            if not key:
                return {"ok": False, "error": "No OpenAI API key configured"}
            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=8,
            )
            resp.raise_for_status()

        elif provider_id == "anthropic":
            key = config.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                return {"ok": False, "error": "No Anthropic API key configured"}
            resp = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=8,
            )
            resp.raise_for_status()

        elif provider_id == "mlx-whisper":
            if not _is_importable("mlx_whisper"):
                return {"ok": False, "error": "mlx-whisper not installed"}
            model = config.get("mlx_model", "mlx-community/whisper-base-mlx")
            if not _mlx_whisper_model_cached(model):
                return {"ok": False, "error": f"Model weights not cached: {model}"}

        elif provider_id == "kokoro":
            if not _is_importable("kokoro"):
                return {"ok": False, "error": "kokoro-onnx not installed"}

        else:
            return {"ok": False, "error": f"Unknown provider: {provider_id}"}

        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "latency_ms": ms}

    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_importable(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _mlx_whisper_model_cached(model_id: str) -> bool:
    """Check HuggingFace hub cache for the mlx-whisper model."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    slug = "models--" + model_id.replace("/", "--")
    return (cache_dir / slug).exists()


def _kokoro_voice_cached(voice: str) -> bool:
    """Heuristic: kokoro downloads voices into the HF hub cache."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    return any(cache_dir.glob("models--hexgrad*")) if cache_dir.exists() else False


def _uv_add(packages: str) -> None:
    """Install extra packages into the current uv environment."""
    cmd = [sys.executable, "-m", "uv", "pip", "install", *packages.split()]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def _hf_download_with_progress(step: str, repo_id: str) -> None:
    """Download a HuggingFace repo snapshot; progress is logged but not streamed
    (HF hub doesn't expose a simple progress callback without tqdm).  A future
    iteration can parse tqdm stderr output for finer-grained progress."""
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import]
    except ImportError:
        # huggingface_hub is a transitive dep of mlx-whisper; if it's missing
        # the user hasn't installed the optional extras yet.
        raise RuntimeError(
            "huggingface_hub not available — install mlx-whisper first"
        )
    snapshot_download(repo_id=repo_id, local_files_only=False)
