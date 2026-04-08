"""STT provider: mlx-whisper (Apple Silicon only)."""

from __future__ import annotations

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

        import io
        import numpy as np
        import soundfile as sf

        # Decode WAV in Python — avoids requiring ffmpeg in PATH (Tauri apps
        # don't inherit the shell PATH on macOS).
        with io.BytesIO(audio_wav) as buf:
            audio_array, sample_rate = sf.read(buf, dtype="float32", always_2d=False)

        # Convert stereo → mono
        if audio_array.ndim > 1:
            audio_array = audio_array.mean(axis=1)

        # Resample to 16 kHz (Whisper's expected sample rate)
        target_sr = 16_000
        if sample_rate != target_sr:
            n_out = int(round(len(audio_array) * target_sr / sample_rate))
            audio_array = np.interp(
                np.linspace(0.0, len(audio_array) - 1, n_out),
                np.arange(len(audio_array)),
                audio_array,
            ).astype(np.float32)

        # Pass ndarray directly — mlx_whisper skips the ffmpeg load_audio call
        # when given an array instead of a file path.
        result = mlx_whisper.transcribe(audio_array, path_or_hf_repo=self._model)
        return result.get("text", "").strip()
