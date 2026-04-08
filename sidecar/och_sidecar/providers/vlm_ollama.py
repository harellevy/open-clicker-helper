"""VLM provider: Ollama (local inference server)."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from .base import ProviderConfig, ProviderError, VlmProvider


class OllamaConfig(ProviderConfig):
    ollama_model: str = "qwen2.5vl:7b"
    ollama_url: str = "http://localhost:11434"


class OllamaVlm(VlmProvider):
    kind = "vlm"
    id = "ollama"
    config_schema = OllamaConfig

    def __init__(
        self,
        config: OllamaConfig | None = None,
        *,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if config is None:
            config = OllamaConfig(
                ollama_model=model or "qwen2.5vl:7b",
                ollama_url=(base_url or "http://localhost:11434").rstrip("/"),
            )
        super().__init__(config)

    @property
    def _model(self) -> str:
        return self.config.ollama_model  # type: ignore[attr-defined]

    @property
    def _base_url(self) -> str:
        return self.config.ollama_url.rstrip("/")  # type: ignore[attr-defined]

    def test(self) -> dict[str, Any]:
        try:
            httpx.get(f"{self._base_url}/api/tags", timeout=3).raise_for_status()
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
        """Send a chat completion to Ollama.

        When ``json_schema`` is provided, it is passed as the ``format`` field
        to enable Ollama's structured-outputs mode — the model is constrained
        to produce JSON matching the schema, which removes the need for the
        prompt-based retry fallback in ``grounding.locate``.
        See https://github.com/ollama/ollama/blob/main/docs/api.md
        """
        if image_bytes is not None:
            # Ollama's /api/chat takes images as a sibling field to `content`,
            # not as OpenAI-style structured content parts. The base64 string
            # must be raw — no `data:image/...;base64,` prefix.
            img_b64 = base64.b64encode(image_bytes).decode()
            messages = [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [img_b64],
                }
            ]
        else:
            messages = [{"role": "user", "content": prompt}]

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        if json_schema is not None:
            payload["format"] = json_schema

        # Vision inference on a 7B local model with a multi-MB screenshot
        # easily blows past 60s on cold start. Use a generous timeout for the
        # body read, but cap connect/write so a wedged Ollama still surfaces
        # quickly.
        timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
        try:
            resp = httpx.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Ollama request failed: {exc}") from exc

        return resp.json()["message"]["content"].strip()

    def locate(self, image_png: bytes, question: str) -> dict[str, Any]:
        """Use the VLM to answer a visual location question."""
        answer = self.complete(question, image_bytes=image_png)
        return {"explanation": answer}
