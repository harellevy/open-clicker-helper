"""VLM provider: Anthropic Claude (vision)."""

from __future__ import annotations

import base64
from typing import Any

from .base import ProviderConfig, ProviderError, VlmProvider

_DEFAULT_MODEL = "claude-opus-4-6-20251101"


class AnthropicVlmConfig(ProviderConfig):
    anthropic_key: str = ""
    model: str = _DEFAULT_MODEL


class AnthropicVlm(VlmProvider):
    kind = "vlm"
    id = "anthropic"
    config_schema = AnthropicVlmConfig

    def __init__(
        self,
        config: AnthropicVlmConfig | None = None,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        if config is None:
            config = AnthropicVlmConfig(anthropic_key=api_key or "", model=model)
        super().__init__(config)

    @property
    def _key(self) -> str:
        return self.config.anthropic_key  # type: ignore[attr-defined]

    @property
    def _model(self) -> str:
        return self.config.model  # type: ignore[attr-defined]

    def test(self) -> dict[str, Any]:
        try:
            import anthropic  # type: ignore[import]
        except ImportError:
            return {"ok": False, "error": "anthropic package not installed (pip install anthropic)"}
        try:
            anthropic.Anthropic(api_key=self._key).models.list(limit=1)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def complete(self, prompt: str, image_bytes: bytes | None = None) -> str:
        try:
            import anthropic  # type: ignore[import]
        except ImportError as exc:
            raise ProviderError(
                "anthropic package not installed (pip install anthropic)"
            ) from exc

        content: list[dict[str, Any]] = []
        if image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode()
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
            })
        content.append({"type": "text", "text": prompt})

        client = anthropic.Anthropic(api_key=self._key)
        try:
            msg = client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[{"role": "user", "content": content}],
            )
            return msg.content[0].text.strip()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Anthropic VLM failed: {exc}") from exc

    def locate(self, image_png: bytes, question: str) -> dict[str, Any]:
        answer = self.complete(question, image_bytes=image_png)
        return {"explanation": answer}
