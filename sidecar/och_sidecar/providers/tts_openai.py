"""TTS provider: OpenAI Text-to-Speech API."""

from __future__ import annotations

import io
import struct
import wave
from typing import Any

from .base import ProviderConfig, ProviderError, TtsProvider

_VOICES = ("alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")
_SAMPLE_RATE = 24_000
_CHANNELS = 1
_SAMPLE_WIDTH = 2  # 16-bit PCM


class OpenAITtsConfig(ProviderConfig):
    openai_key: str = ""
    voice: str = "nova"
    model: str = "tts-1"


class OpenAITts(TtsProvider):
    kind = "tts"
    id = "openai"
    config_schema = OpenAITtsConfig

    def __init__(
        self,
        config: OpenAITtsConfig | None = None,
        *,
        api_key: str | None = None,
        voice: str = "nova",
        model: str = "tts-1",
    ) -> None:
        if config is None:
            config = OpenAITtsConfig(openai_key=api_key or "", voice=voice, model=model)
        super().__init__(config)

    @property
    def _key(self) -> str:
        return self.config.openai_key  # type: ignore[attr-defined]

    @property
    def _voice(self) -> str:
        return self.config.voice  # type: ignore[attr-defined]

    @property
    def _model(self) -> str:
        return self.config.model  # type: ignore[attr-defined]

    def test(self) -> dict[str, Any]:
        try:
            import openai  # type: ignore[import]
        except ImportError:
            return {"ok": False, "error": "openai package not installed (pip install openai)"}
        try:
            openai.OpenAI(api_key=self._key).models.retrieve("tts-1")
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def synthesize(self, text: str) -> bytes:
        try:
            import openai  # type: ignore[import]
        except ImportError as exc:
            raise ProviderError("openai package not installed (pip install openai)") from exc

        client = openai.OpenAI(api_key=self._key)
        try:
            response = client.audio.speech.create(
                model=self._model,
                voice=self._voice,  # type: ignore[arg-type]
                input=text,
                response_format="pcm",  # raw 16-bit PCM at 24 kHz
            )
            pcm_bytes = response.content
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"OpenAI TTS failed: {exc}") from exc

        return _pcm_to_wav(pcm_bytes)


def _pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw 16-bit PCM (24 kHz mono) in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(_CHANNELS)
        wf.setsampwidth(_SAMPLE_WIDTH)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()
