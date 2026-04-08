"""VLM provider: OpenAI GPT-4o vision."""

from __future__ import annotations

import base64
from typing import Any

from .base import ProviderConfig, ProviderError, VlmProvider


class OpenAIVlmConfig(ProviderConfig):
    openai_key: str = ""
    openai_model: str = "gpt-4o"


class OpenAIVlm(VlmProvider):
    kind = "vlm"
    id = "openai"
    config_schema = OpenAIVlmConfig

    def __init__(
        self,
        config: OpenAIVlmConfig | None = None,
        *,
        api_key: str | None = None,
        model: str = "gpt-4o",
    ) -> None:
        if config is None:
            config = OpenAIVlmConfig(openai_key=api_key or "", openai_model=model)
        super().__init__(config)

    @property
    def _key(self) -> str:
        return self.config.openai_key  # type: ignore[attr-defined]

    @property
    def _model(self) -> str:
        return self.config.openai_model  # type: ignore[attr-defined]

    def test(self) -> dict[str, Any]:
        try:
            import openai  # type: ignore[import]
        except ImportError:
            return {"ok": False, "error": "openai package not installed (pip install openai)"}
        try:
            openai.OpenAI(api_key=self._key).models.retrieve(self._model)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def complete(
        self,
        prompt: str,
        image_bytes: bytes | None = None,
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        try:
            import openai  # type: ignore[import]
        except ImportError as exc:
            raise ProviderError("openai package not installed (pip install openai)") from exc

        content: list[dict[str, Any]] = []
        if image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            })
        content.append({"type": "text", "text": prompt})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 1024,
        }
        if json_schema is not None:
            # OpenAI's structured-output wrapper. `strict: True` makes the
            # schema enforced exactly.
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "grounding",
                    "schema": json_schema,
                    "strict": True,
                },
            }

        client = openai.OpenAI(api_key=self._key)
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content.strip()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"OpenAI VLM failed: {exc}") from exc

    def locate(self, image_png: bytes, question: str) -> dict[str, Any]:
        answer = self.complete(question, image_bytes=image_png)
        return {"explanation": answer}
