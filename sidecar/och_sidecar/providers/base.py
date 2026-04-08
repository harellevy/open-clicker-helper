"""Provider ABCs.

Every provider declares:
- `kind`: stt | tts | llm | vlm
- `id`: stable string used in settings.json
- `config_schema`: a Pydantic model class for the settings UI to render
- `test()`: cheap connectivity check
- domain method (`transcribe` / `synthesize` / `complete` / `locate`)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel


class ProviderError(Exception):
    pass


class ProviderConfig(BaseModel):
    """Base config; concrete providers extend with their fields."""


class Provider(ABC):
    kind: ClassVar[str]
    id: ClassVar[str]
    config_schema: ClassVar[type[ProviderConfig]]

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    def test(self) -> dict[str, Any]:
        """Return `{ok: bool, error?: str}`."""


class SttProvider(Provider):
    kind = "stt"

    @abstractmethod
    def transcribe(self, audio_wav: bytes) -> str: ...


class TtsProvider(Provider):
    kind = "tts"

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Return WAV bytes."""


class LlmProvider(Provider):
    kind = "llm"

    @abstractmethod
    def complete(self, prompt: str, *, system: str | None = None) -> str: ...


class VlmProvider(Provider):
    kind = "vlm"

    @abstractmethod
    def locate(self, image_png: bytes, question: str) -> dict[str, Any]:
        """Return `{action, target_xy_norm, confidence, explanation, steps?}`."""
