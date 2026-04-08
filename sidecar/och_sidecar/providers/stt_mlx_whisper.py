"""STT provider: mlx-whisper (Apple Silicon only)."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from .base import ProviderConfig, ProviderError, SttProvider


class MlxWhisperConfig(ProviderConfig):
    mlx_model: str = "mlx-community/whisper-base-mlx"


class MlxWhisperStt(SttProvider):
    kind = "stt"
    id = "mlx-whisper"
    config_schema = MlxWhisperConfig

    def __init__(self, config: MlxWhisperConfig | None = None, *, model: str | None = None) -> None:
        if config is None:
            config = MlxWhisperConfig(mlx_model=model or "mlx-community/whisper-base-mlx")
        super().__init__(config)

    @property
    def _model(self) -> str:
        return self.config.mlx_model  # type: ignore[attr-defined]

    def test(self) -> dict[str, Any]:
        try:
            import importlib.util
            if importlib.util.find_spec("mlx_whisper") is None:
                return {"ok": False, "error": "mlx-whisper is not installed (Apple Silicon only)"}
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def transcribe(self, audio_wav: bytes) -> str:
        try:
            import mlx_whisper  # type: ignore[import]
        except ImportError as exc:
            raise ProviderError(
                "mlx-whisper is not installed. "
                "It is only available on Apple Silicon (macOS arm64). "
                "Install with: uv pip install mlx-whisper"
            ) from exc

        # mlx-whisper expects a file path, not bytes — write to a temp file.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_wav)
            tmp_path = f.name
        try:
            result = mlx_whisper.transcribe(tmp_path, path_or_hf_repo=self._model)
            return result.get("text", "").strip()
        finally:
            os.unlink(tmp_path)
