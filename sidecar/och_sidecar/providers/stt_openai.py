"""STT provider: OpenAI Whisper API."""

from __future__ import annotations

import io
from typing import Any

from .base import ProviderConfig, ProviderError, SttProvider


class OpenAISttConfig(ProviderConfig):
    openai_key: str = ""
    model: str = "whisper-1"


class OpenAIStt(SttProvider):
    kind = "stt"
    id = "openai"
    config_schema = OpenAISttConfig

    def __init__(
        self,
        config: OpenAISttConfig | None = None,
        *,
        api_key: str | None = None,
        model: str = "whisper-1",
    ) -> None:
        if config is None:
            config = OpenAISttConfig(openai_key=api_key or "", model=model)
        super().__init__(config)

    @property
    def _key(self) -> str:
        return self.config.openai_key  # type: ignore[attr-defined]

    @property
    def _model(self) -> str:
        return self.config.model  # type: ignore[attr-defined]

    def test(self) -> dict[str, Any]:
        try:
            import openai  # type: ignore[import]
        except ImportError:
            return {"ok": False, "error": "openai package not installed (pip install openai)"}
        try:
            openai.OpenAI(api_key=self._key).models.retrieve("whisper-1")
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def transcribe(self, audio_wav: bytes) -> str:
        try:
            import openai  # type: ignore[import]
        except ImportError as exc:
            raise ProviderError("openai package not installed (pip install openai)") from exc

        client = openai.OpenAI(api_key=self._key)
        try:
            result = client.audio.transcriptions.create(
                model=self._model,
                file=("audio.wav", io.BytesIO(audio_wav), "audio/wav"),
            )
            return result.text.strip()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"OpenAI STT failed: {exc}") from exc
