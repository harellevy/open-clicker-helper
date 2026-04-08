"""Unit tests for the VLM grounding parser and locate() function."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from och_sidecar import grounding as _grounding
from och_sidecar.providers.base import ProviderError


# ── _parse ────────────────────────────────────────────────────────────────────

class TestParse:
    def test_simple_json(self):
        raw = '{"steps": [{"x": 0.5, "y": 0.3, "explanation": "Click Save"}]}'
        result = _grounding._parse(raw)
        assert result["steps"] == [{"x": 0.5, "y": 0.3, "explanation": "Click Save"}]

    def test_coordinates_clamped_to_unit_range(self):
        raw = '{"steps": [{"x": 1.5, "y": -0.2, "explanation": "out of bounds"}]}'
        result = _grounding._parse(raw)
        step = result["steps"][0]
        assert step["x"] == pytest.approx(1.0)
        assert step["y"] == pytest.approx(0.0)

    def test_markdown_fences_stripped(self):
        raw = '```json\n{"steps": [{"x": 0.1, "y": 0.2, "explanation": "ok"}]}\n```'
        result = _grounding._parse(raw)
        assert len(result["steps"]) == 1

    def test_json_embedded_in_prose(self):
        raw = 'Here is the answer: {"steps": [{"x": 0.4, "y": 0.6, "explanation": "click"}]} done.'
        result = _grounding._parse(raw)
        assert len(result["steps"]) == 1

    def test_alternative_key_actions(self):
        raw = '{"actions": [{"x": 0.3, "y": 0.7, "explanation": "tap"}]}'
        result = _grounding._parse(raw)
        assert len(result["steps"]) == 1

    def test_alternative_key_clicks(self):
        raw = '{"clicks": [{"x": 0.2, "y": 0.8, "explanation": "press"}]}'
        result = _grounding._parse(raw)
        assert len(result["steps"]) == 1

    def test_normalised_key_aliases(self):
        raw = '{"steps": [{"x_norm": 0.5, "y_norm": 0.5, "explanation": "center"}]}'
        result = _grounding._parse(raw)
        assert result["steps"][0]["x"] == pytest.approx(0.5)

    def test_multi_step(self):
        raw = '{"steps": [{"x": 0.1, "y": 0.1, "explanation": "A"}, {"x": 0.9, "y": 0.9, "explanation": "B"}]}'
        result = _grounding._parse(raw)
        assert len(result["steps"]) == 2

    def test_raises_on_no_json(self):
        with pytest.raises(ValueError, match="no JSON object found"):
            _grounding._parse("no json here")

    def test_raises_on_empty_steps(self):
        with pytest.raises(ValueError, match="non-empty 'steps'"):
            _grounding._parse('{"steps": []}')

    def test_raises_on_non_dict_step(self):
        with pytest.raises(ValueError, match="not a dict"):
            _grounding._parse('{"steps": ["not a dict"]}')

    def test_raises_on_invalid_json(self):
        with pytest.raises(ValueError, match="JSON parse error"):
            _grounding._parse("{bad json}")

    def test_missing_explanation_defaults_to_empty_string(self):
        raw = '{"steps": [{"x": 0.5, "y": 0.5}]}'
        result = _grounding._parse(raw)
        assert result["steps"][0]["explanation"] == ""


# ── locate() ─────────────────────────────────────────────────────────────────

class TestLocate:
    def _make_vlm(self, response: str):
        mock = MagicMock()
        mock.complete.return_value = response
        return mock

    def test_returns_parsed_steps_on_first_attempt(self):
        vlm = self._make_vlm('{"steps": [{"x": 0.5, "y": 0.3, "explanation": "Click Save"}]}')
        result = _grounding.locate(vlm, b"fakepng", "click Save")
        assert len(result["steps"]) == 1
        assert result["steps"][0]["explanation"] == "Click Save"
        assert vlm.complete.call_count == 1

    def test_retries_on_first_parse_failure(self):
        good_json = '{"steps": [{"x": 0.2, "y": 0.8, "explanation": "retry ok"}]}'
        vlm = self._make_vlm("garbage")
        # Second call returns valid JSON
        vlm.complete.side_effect = ["garbage", good_json]
        result = _grounding.locate(vlm, b"png", "do something")
        assert vlm.complete.call_count == 2
        assert result["steps"][0]["explanation"] == "retry ok"

    def test_raises_provider_error_after_two_failures(self):
        vlm = self._make_vlm("still garbage")
        with pytest.raises(ProviderError, match="Grounding failed after retry"):
            _grounding.locate(vlm, b"png", "impossible")

    def test_vlm_called_with_image_bytes(self):
        png = b"\x89PNG\r\n"
        vlm = self._make_vlm('{"steps": [{"x": 0.1, "y": 0.1, "explanation": "ok"}]}')
        _grounding.locate(vlm, png, "find button")
        call_kwargs = vlm.complete.call_args
        assert call_kwargs.kwargs.get("image_bytes") == png or call_kwargs.args[1] == png

    def test_retry_prompt_appended(self):
        """Second call must include the stricter suffix."""
        vlm = self._make_vlm("bad")
        vlm.complete.side_effect = [
            "bad",
            '{"steps": [{"x": 0.5, "y": 0.5, "explanation": "retry"}]}',
        ]
        _grounding.locate(vlm, b"png", "task")
        first_prompt = vlm.complete.call_args_list[0].args[0]
        second_prompt = vlm.complete.call_args_list[1].args[0]
        assert len(second_prompt) > len(first_prompt)
