"""TTS provider: kokoro."""

from __future__ import annotations

import io
from typing import Any

from .base import ProviderConfig, ProviderError, TtsProvider


class KokoroConfig(ProviderConfig):
    kokoro_voice: str = "af_heart"
    kokoro_speed: float = 1.0


class KokoroTts(TtsProvider):
    kind = "tts"
    id = "kokoro"
    config_schema = KokoroConfig

    def __init__(
        self,
        config: KokoroConfig | None = None,
        *,
        voice: str | None = None,
        speed: float = 1.0,
    ) -> None:
        if config is None:
            config = KokoroConfig(
                kokoro_voice=voice or "af_heart",
                kokoro_speed=speed,
            )
        super().__init__(config)
        self._pipeline = None

    @property
    def _voice(self) -> str:
        return self.config.kokoro_voice  # type: ignore[attr-defined]

    @property
    def _speed(self) -> float:
        return self.config.kokoro_speed  # type: ignore[attr-defined]

    def _get_pipeline(self):
        if self._pipeline is None:
            try:
                from kokoro import KPipeline  # type: ignore[import]
            except ImportError as exc:
                raise ProviderError(
                    "kokoro is not installed. "
                    "Install with: uv pip install kokoro soundfile"
                ) from exc
            self._pipeline = KPipeline(lang_code="a")
        return self._pipeline

    def test(self) -> dict[str, Any]:
        try:
            import importlib.util
            if importlib.util.find_spec("kokoro") is None:
                return {"ok": False, "error": "kokoro is not installed"}
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def synthesize(self, text: str) -> bytes:
        try:
            import numpy as np  # type: ignore[import]
            import soundfile as sf  # type: ignore[import]
        except ImportError as exc:
            raise ProviderError(
                "numpy or soundfile is not installed. "
                "Install with: uv pip install numpy soundfile"
            ) from exc

        pipeline = self._get_pipeline()
        generator = pipeline(text, voice=self._voice, speed=self._speed)

        samples = []
        for _, _, audio in generator:
            samples.append(audio)

        if not samples:
            return b""

        combined = np.concatenate(samples) if len(samples) > 1 else samples[0]

        buf = io.BytesIO()
        sf.write(buf, combined, samplerate=24000, format="WAV")
        return buf.getvalue()
