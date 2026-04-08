"""VLM-based visual grounding.

Given a screenshot (PNG bytes) and a natural-language question, asks the active
VLM provider where to click and returns a list of action steps with normalised
coordinates (0.0–1.0 relative to the screenshot dimensions).

The VLM is prompted to respond with a JSON object:

    {
      "steps": [
        {"x": 0.52, "y": 0.33, "explanation": "Click the Save button"}
      ]
    }

If the first response is not parseable, one automatic retry is performed with
a stricter prompt suffix.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .providers.base import ProviderError, VlmProvider

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a UI grounding assistant. Given a screenshot and a task description, output the screen coordinates where the user should click to complete the task.

IMPORTANT: Respond ONLY with a valid JSON object — no prose, no markdown fences.

Schema:
{
  "steps": [
    {
      "x": <float 0.0–1.0>,
      "y": <float 0.0–1.0>,
      "explanation": "<one short sentence>"
    }
  ]
}

Coordinates are normalised: (0, 0) is the top-left corner, (1, 1) is the bottom-right. For multi-step tasks include one entry per click in order."""

_RETRY_SUFFIX = "\n\nRespond ONLY with the JSON object. No explanation, no code fences."


# ── Public API ────────────────────────────────────────────────────────────────

def locate(
    vlm: VlmProvider,
    image_png: bytes,
    question: str,
) -> dict[str, Any]:
    """Return ``{"steps": [{"x": float, "y": float, "explanation": str}]}``.

    Raises `ProviderError` if both attempts fail to parse.
    """
    prompt = f"{_SYSTEM_PROMPT}\n\nTask: {question}"

    # First attempt
    raw = vlm.complete(prompt, image_bytes=image_png)
    first_err_msg: str
    try:
        return _parse(raw)
    except ValueError as exc:
        first_err_msg = str(exc)
        logger.warning("grounding parse error (will retry): %s | raw=%r", exc, raw[:200])

    # Retry with a stricter suffix
    raw2 = vlm.complete(prompt + _RETRY_SUFFIX, image_bytes=image_png)
    try:
        return _parse(raw2)
    except ValueError as second_err:
        raise ProviderError(
            f"Grounding failed after retry. "
            f"First error: {first_err_msg}. "
            f"Second error: {second_err}. "
            f"Last raw response: {raw2[:300]!r}"
        ) from second_err


# ── Parsing / validation ──────────────────────────────────────────────────────

def _parse(text: str) -> dict[str, Any]:
    """Extract, validate, and clamp a grounding JSON response.

    Raises `ValueError` with a descriptive message on any validation failure.
    """
    # Strip optional markdown fences (```json … ```)
    text = re.sub(r"```(?:json)?\s*", "", text).strip()

    # Find the first {...} blob
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object found in: {text[:200]!r}")

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error: {exc}") from exc

    raw_steps = data.get("steps") or data.get("actions") or data.get("clicks") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(f"expected non-empty 'steps' list, got: {data!r}")

    validated: list[dict[str, Any]] = []
    for i, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            raise ValueError(f"step[{i}] is not a dict: {step!r}")
        try:
            x = float(step.get("x", step.get("x_norm", step.get("left", 0))))
            y = float(step.get("y", step.get("y_norm", step.get("top", 0))))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"step[{i}] has non-numeric x/y: {step!r}") from exc

        validated.append(
            {
                "x": max(0.0, min(1.0, x)),
                "y": max(0.0, min(1.0, y)),
                "explanation": str(step.get("explanation", step.get("label", ""))),
            }
        )

    return {"steps": validated}
