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

# ── Default prompts (user-overridable via settings) ──────────────────────────

DEFAULT_GROUNDING_SYSTEM_PROMPT = """You are a UI grounding assistant. Given a screenshot and a task description, output the screen coordinates where the user should click to complete the task.

IMPORTANT: Respond ONLY with a valid JSON object — no prose, no markdown fences.
IMPORTANT: The "explanation" field MUST be written in English. The downstream text-to-speech engine only supports English, so any other language will fail to play back.

Schema:
{
  "steps": [
    {
      "x": <float 0.0–1.0>,
      "y": <float 0.0–1.0>,
      "explanation": "<one short sentence, English only>"
    }
  ]
}

Coordinates are normalised: (0, 0) is the top-left corner, (1, 1) is the bottom-right. For multi-step tasks include one entry per click in order."""

DEFAULT_CAPTION_SYSTEM_PROMPT = """You are a UI observer. Describe what is visible on the user's screen in 1–3 short sentences so a downstream agent can decide what to click.

Focus on:
- the app and page/window in view
- the main interactive elements (buttons, inputs, menus) and their rough locations
- any dialog, modal, or notification currently on top

IMPORTANT: Respond in English only — the downstream text-to-speech engine does not support other languages.
Respond with plain prose — no JSON, no lists, no code fences."""

DEFAULT_REFINE_SYSTEM_PROMPT = """You are a UI grounding refinement assistant. The image you are looking at is a ZOOMED-IN CROP of a larger screenshot — a small region where the target element is known to live.

Your job: pinpoint the EXACT CENTER of the element the user wants to click, within THIS cropped image.

IMPORTANT: Respond ONLY with a valid JSON object — no prose, no markdown fences.
IMPORTANT: The "explanation" field MUST be written in English (downstream TTS is English-only).

Schema:
{
  "x": <float 0.0–1.0>,
  "y": <float 0.0–1.0>,
  "explanation": "<one short sentence, English only>"
}

Coordinates are normalised within THIS crop (not the full screen). (0, 0) is the top-left of the crop; (1, 1) is the bottom-right. Aim for the geometric centre of the clickable region, not its edge. If multiple candidates are visible, pick the one that best matches the task."""

_RETRY_SUFFIX = "\n\nRespond ONLY with the JSON object. No explanation, no code fences."


# JSON schema passed to providers that support structured outputs
# (Ollama via `format`, OpenAI via `response_format.json_schema`). Providers
# that don't support schema-constrained decoding accept the kwarg as a no-op
# and the prompt-based fallback still works.
GROUNDING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "minimum": 0, "maximum": 1},
                    "y": {"type": "number", "minimum": 0, "maximum": 1},
                    "explanation": {"type": "string"},
                },
                "required": ["x", "y", "explanation"],
            },
        },
    },
    "required": ["steps"],
}

# Schema for the single-point refinement response.
REFINE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "x": {"type": "number", "minimum": 0, "maximum": 1},
        "y": {"type": "number", "minimum": 0, "maximum": 1},
        "explanation": {"type": "string"},
    },
    "required": ["x", "y", "explanation"],
}


# ── Public API ────────────────────────────────────────────────────────────────

def locate(
    vlm: VlmProvider,
    image_png: bytes,
    question: str,
    *,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Return ``{"steps": [{"x": float, "y": float, "explanation": str}], "raw": str}``.

    ``system_prompt`` overrides the built-in grounding prompt (used by the
    settings-page prompt editor). Pass ``None`` to keep the default.

    Providers that support structured outputs (Ollama, OpenAI) are called
    with ``json_schema=GROUNDING_JSON_SCHEMA`` so the model is constrained to
    emit valid JSON — this eliminates the prompt-based retry in the common
    case. Providers that don't support it transparently ignore the kwarg and
    the retry path still covers them.

    Raises `ProviderError` if both attempts fail to parse.
    """
    base = (system_prompt or DEFAULT_GROUNDING_SYSTEM_PROMPT).rstrip()
    prompt = f"{base}\n\nTask: {question}"

    # First attempt — ask the provider for schema-constrained output.
    raw = vlm.complete(
        prompt,
        image_bytes=image_png,
        json_schema=GROUNDING_JSON_SCHEMA,
    )
    first_err_msg: str
    try:
        parsed = _parse(raw)
        parsed["raw"] = raw
        return parsed
    except ValueError as exc:
        first_err_msg = str(exc)
        logger.warning("grounding parse error (will retry): %s | raw=%r", exc, raw[:200])

    # Retry with a stricter prompt suffix. Keep the schema on — it's a pure
    # win when the provider supports it, and a no-op otherwise.
    raw2 = vlm.complete(
        prompt + _RETRY_SUFFIX,
        image_bytes=image_png,
        json_schema=GROUNDING_JSON_SCHEMA,
    )
    try:
        parsed = _parse(raw2)
        parsed["raw"] = raw2
        return parsed
    except ValueError as second_err:
        raise ProviderError(
            f"Grounding failed after retry. "
            f"First error: {first_err_msg}. "
            f"Second error: {second_err}. "
            f"Last raw response: {raw2[:300]!r}"
        ) from second_err


def caption(
    vlm: VlmProvider,
    image_png: bytes,
    *,
    system_prompt: str | None = None,
) -> str:
    """Ask the VLM for a short natural-language description of what's on screen.

    Used by the debug overlay to show "what the VLM sees" alongside the
    grounding answer.
    """
    prompt = (system_prompt or DEFAULT_CAPTION_SYSTEM_PROMPT).rstrip()
    return vlm.complete(prompt, image_bytes=image_png).strip()


def refine(
    vlm: VlmProvider,
    crop_png: bytes,
    question: str,
    *,
    system_prompt: str | None = None,
) -> dict[str, Any] | None:
    """Refinement pass: ask the VLM for a precise point inside a cropped region.

    The caller is expected to have cropped a window from the **full-resolution**
    screenshot centred on a rough coordinate from the first grounding call.
    This function asks the VLM to pinpoint the exact target centre within
    that crop. The returned coordinates are normalised within the crop —
    callers are responsible for mapping them back to full-image space.

    Returns ``{"x": float, "y": float, "explanation": str}`` on success, or
    ``None`` on any failure (VLM error, parse error). When ``None`` is
    returned the caller should fall back to the rough first-pass coordinate
    rather than fail the whole request.
    """
    base = (system_prompt or DEFAULT_REFINE_SYSTEM_PROMPT).rstrip()
    prompt = f"{base}\n\nTask: {question}"

    try:
        raw = vlm.complete(
            prompt,
            image_bytes=crop_png,
            json_schema=REFINE_JSON_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 — refine is best-effort
        logger.warning("refine VLM call failed: %s", exc)
        return None

    try:
        return _parse_single_point(raw)
    except ValueError as exc:
        logger.warning("refine parse failed: %s | raw=%r", exc, raw[:200])
        return None


def locate_from_ax(
    ax_candidates: list[dict[str, Any]],
    question: str,
) -> dict[str, Any] | None:
    """Try to answer a grounding question directly from the macOS AX tree.

    Returns a parsed grounding result ``{"steps": [...], "raw": str, "source":
    "ax"}`` when at least one candidate's text matches the question, or
    ``None`` when no candidate matches. The match is a token-overlap score
    over the candidate's ``role``, ``title``, and ``description`` fields.

    ``ax_candidates`` items follow the Rust ``AxCandidate`` shape:

        {role, title, description, x, y, width, height}

    where ``x``/``y`` are already normalised to [0, 1] relative to the focused
    window's logical screen coordinates (Rust does that conversion before
    handing candidates to the sidecar — the VLM pipeline always deals in
    normalised coordinates, so this keeps the dispatch uniform).

    The returned ``steps`` list has exactly one entry pointing at the centre
    of the best-matching candidate's bounding box. We intentionally don't try
    to chain multiple AX candidates into a multi-step plan — the iterative
    loop re-grounds on a fresh screenshot between clicks, so each AX hit just
    needs to describe one good click.
    """
    if not ax_candidates:
        return None

    q_tokens = _tokenise(question)
    if not q_tokens:
        return None

    best: tuple[float, dict[str, Any]] | None = None
    for cand in ax_candidates:
        if not isinstance(cand, dict):
            continue
        haystack_parts = [
            str(cand.get("role", "")),
            str(cand.get("title", "")),
            str(cand.get("description", "")),
        ]
        haystack_tokens = _tokenise(" ".join(haystack_parts))
        if not haystack_tokens:
            continue
        score = _match_score(q_tokens, haystack_tokens)
        if score <= 0.0:
            continue
        if best is None or score > best[0]:
            best = (score, cand)

    if best is None:
        return None

    _score, cand = best
    try:
        x = float(cand.get("x", 0.0))
        y = float(cand.get("y", 0.0))
        w = float(cand.get("width", 0.0))
        h = float(cand.get("height", 0.0))
    except (TypeError, ValueError):
        return None

    if w <= 0.0 or h <= 0.0:
        return None

    cx = max(0.0, min(1.0, x + w / 2.0))
    cy = max(0.0, min(1.0, y + h / 2.0))

    label = (cand.get("title") or cand.get("description") or cand.get("role") or "").strip()
    explanation = f"Clicking {label}." if label else "Clicking the matched control."

    return {
        "steps": [
            {
                "x": cx,
                "y": cy,
                "explanation": explanation,
            }
        ],
        "raw": f"ax:{cand.get('role', '')}:{label}",
        "source": "ax",
    }


# Split on non-alphanumeric AND on camelCase boundaries so "AXSearchField"
# tokenises as {"ax", "search", "field"} — otherwise the AX role prefix
# swallows the whole word and we can't match substrings inside it.
_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+")

# Words too generic to count for matching — otherwise "click the button" picks
# the first AXButton on screen regardless of its label.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "click",
        "clicks",
        "clicking",
        "press",
        "presses",
        "pressing",
        "tap",
        "taps",
        "tapping",
        "please",
        "on",
        "to",
        "in",
        "at",
        "for",
        "and",
        "or",
        "of",
        "button",
        "link",
        "item",
        "field",
    }
)


def _tokenise(text: str) -> set[str]:
    # Split camelCase (`AXSearchField`) *before* lowercasing so the word
    # boundaries survive. AX-role tokens all start with the literal `AX`
    # prefix — strip that too so `AXButton` can match "button" if it ever
    # slips past the stopword filter.
    raw = _TOKEN_RE.findall(text)
    out: set[str] = set()
    for t in raw:
        low = t.lower()
        if low == "ax":
            continue
        if low in _STOPWORDS:
            continue
        out.add(low)
    return out


def _match_score(q: set[str], hay: set[str]) -> float:
    """Jaccard-like overlap, but biased toward the question's coverage: we
    care whether the candidate contains what the user asked for, not whether
    the candidate is a clean subset of the question."""
    if not q or not hay:
        return 0.0
    inter = q & hay
    if not inter:
        return 0.0
    return len(inter) / len(q)


def _parse_single_point(text: str) -> dict[str, Any]:
    """Parse a single ``{x, y, explanation}`` JSON object. Clamps to [0, 1].

    Raises ``ValueError`` on any schema violation.
    """
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object found in: {text[:200]!r}")
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error: {exc}") from exc

    # Some models return {"steps": [{...}]} even with the single-point prompt.
    # Accept that shape too by unwrapping the first step.
    if isinstance(data.get("steps"), list) and data["steps"]:
        first = data["steps"][0]
        if isinstance(first, dict):
            data = first

    try:
        x = float(data.get("x", data.get("x_norm", data.get("left", 0))))
        y = float(data.get("y", data.get("y_norm", data.get("top", 0))))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric x/y: {data!r}") from exc

    return {
        "x": max(0.0, min(1.0, x)),
        "y": max(0.0, min(1.0, y)),
        "explanation": str(data.get("explanation", data.get("label", ""))),
    }


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
