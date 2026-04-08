"""Unit tests for the voice round-trip pipeline.

Providers are fully mocked so the tests run without any ML dependencies.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch

import pytest

from och_sidecar import pipeline as _pipeline


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    """The provider cache is module-level state; reset it between tests so
    one test patching a provider class doesn't see a stale instance built by
    a previous test."""
    _pipeline.reset_provider_cache()
    yield
    _pipeline.reset_provider_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _fake_wav() -> bytes:
    """Minimal 44-byte valid-ish WAV blob (content doesn't matter for mocks)."""
    return b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"


def _collect(gen) -> tuple[list[tuple[str, dict]], dict]:
    """Drain a pipeline generator.

    Returns (events, final_result) where events is everything *except* the
    terminal ``("result", ...)`` tuple and final_result is that payload.
    """
    events = []
    final = {}
    for event, payload in gen:
        if event == "result":
            final = payload
            break
        events.append((event, payload))
    return events, final


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPipelineEventOrder:
    """Verify the correct sequence of (event, payload) tuples."""

    def _run(self, audio_b64: str, image_b64: str | None = None, settings: dict | None = None):
        fake_wav_out = _fake_wav()

        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "hello world"

        mock_vlm = MagicMock()
        mock_vlm.complete.return_value = "the answer is 42"

        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = fake_wav_out

        with (
            patch.object(_pipeline, "_make_stt", return_value=mock_stt),
            patch.object(_pipeline, "_make_vlm", return_value=mock_vlm),
            patch.object(_pipeline, "_make_tts", return_value=mock_tts),
        ):
            gen = _pipeline.run(audio_b64, image_b64, settings or {})
            events, final = _collect(gen)

        return events, final, mock_stt, mock_vlm, mock_tts

    def test_event_names_in_order(self):
        audio_b64 = _b64(_fake_wav())
        events, final, *_ = self._run(audio_b64)
        names = [e for e, _ in events]
        assert names == ["stt_start", "stt_done", "llm_start", "llm_done", "tts_start", "tts_done"]

    def test_stt_done_payload_contains_transcript(self):
        audio_b64 = _b64(_fake_wav())
        events, final, *_ = self._run(audio_b64)
        stt_done = dict(events)["stt_done"]
        assert stt_done["transcript"] == "hello world"

    def test_llm_done_payload_contains_answer(self):
        audio_b64 = _b64(_fake_wav())
        events, final, *_ = self._run(audio_b64)
        llm_done = dict(events)["llm_done"]
        assert llm_done["answer"] == "the answer is 42"

    def test_final_result_has_expected_keys(self):
        audio_b64 = _b64(_fake_wav())
        events, final, *_ = self._run(audio_b64)
        assert set(final.keys()) == {"transcript", "answer", "audio_b64", "steps"}

    def test_final_result_values(self):
        audio_b64 = _b64(_fake_wav())
        events, final, *_ = self._run(audio_b64)
        assert final["transcript"] == "hello world"
        assert final["answer"] == "the answer is 42"
        # audio_b64 should be valid base64 that round-trips back to the fake WAV
        assert base64.b64decode(final["audio_b64"]) == _fake_wav()


class TestPipelineProviderCalls:
    """Verify providers are called with correct arguments."""

    def _run_with_mocks(self, audio_b64, image_b64=None, settings=None):
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "transcribed text"
        mock_vlm = MagicMock()
        mock_vlm.complete.return_value = "vlm response"
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b""

        with (
            patch.object(_pipeline, "_make_stt", return_value=mock_stt),
            patch.object(_pipeline, "_make_vlm", return_value=mock_vlm),
            patch.object(_pipeline, "_make_tts", return_value=mock_tts),
        ):
            _collect(_pipeline.run(audio_b64, image_b64, settings or {}))

        return mock_stt, mock_vlm, mock_tts

    def test_stt_receives_decoded_audio_bytes(self):
        raw = _fake_wav()
        audio_b64 = _b64(raw)
        mock_stt, *_ = self._run_with_mocks(audio_b64)
        mock_stt.transcribe.assert_called_once_with(raw)

    def test_vlm_receives_transcript_and_no_image_when_image_b64_is_none(self):
        audio_b64 = _b64(_fake_wav())
        _, mock_vlm, _ = self._run_with_mocks(audio_b64, image_b64=None)
        mock_vlm.complete.assert_called_once_with("transcribed text", image_bytes=None)

    def test_grounding_called_with_decoded_image_when_image_b64_provided(self):
        """When image_b64 is present the pipeline delegates to grounding.locate."""
        from och_sidecar import grounding as _grounding

        audio_b64 = _b64(_fake_wav())
        raw_img = b"\x89PNG\r\n\x1a\n"
        image_b64 = _b64(raw_img)
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "transcribed text"
        mock_vlm = MagicMock()
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b""
        fake_steps = [{"x": 0.5, "y": 0.3, "explanation": "Click the button"}]

        with (
            patch.object(_pipeline, "_make_stt", return_value=mock_stt),
            patch.object(_pipeline, "_make_vlm", return_value=mock_vlm),
            patch.object(_pipeline, "_make_tts", return_value=mock_tts),
            patch.object(_grounding, "locate", return_value={"steps": fake_steps}) as mock_locate,
        ):
            events_list, final = _collect(_pipeline.run(audio_b64, image_b64, {}))

        # grounding.locate must receive the decoded PNG bytes and the transcript
        mock_locate.assert_called_once_with(mock_vlm, raw_img, "transcribed text")
        assert final["steps"] == fake_steps

    def test_tts_receives_vlm_answer(self):
        audio_b64 = _b64(_fake_wav())
        _, _, mock_tts = self._run_with_mocks(audio_b64)
        mock_tts.synthesize.assert_called_once_with("vlm response")


class TestPipelineMakeHelpers:
    """Verify _make_* factory helpers pass settings through correctly."""

    def test_make_stt_uses_settings_model(self):
        settings = {"stt": {"mlx_model": "mlx-community/whisper-large-mlx"}}
        with patch(
            "och_sidecar.providers.stt_mlx_whisper.MlxWhisperStt"
        ) as MockStt:
            MockStt.return_value = MagicMock()
            _pipeline._make_stt(settings)
            call_kwargs = MockStt.call_args
            # The config passed in should have our model
            config_arg = call_kwargs[0][0]
            assert config_arg.mlx_model == "mlx-community/whisper-large-mlx"

    def test_make_stt_uses_default_model(self):
        with patch(
            "och_sidecar.providers.stt_mlx_whisper.MlxWhisperStt"
        ) as MockStt:
            MockStt.return_value = MagicMock()
            _pipeline._make_stt({})
            config_arg = MockStt.call_args[0][0]
            assert config_arg.mlx_model == "mlx-community/whisper-base-mlx"

    def test_make_vlm_uses_settings(self):
        settings = {"vlm": {"ollama_model": "llama3.2-vision:11b", "ollama_url": "http://myhost:11434"}}
        with patch(
            "och_sidecar.providers.vlm_ollama.OllamaVlm"
        ) as MockVlm:
            MockVlm.return_value = MagicMock()
            _pipeline._make_vlm(settings)
            config_arg = MockVlm.call_args[0][0]
            assert config_arg.ollama_model == "llama3.2-vision:11b"
            assert "myhost" in config_arg.ollama_url

    def test_make_tts_uses_settings_voice(self):
        settings = {"tts": {"kokoro_voice": "bm_lewis", "kokoro_speed": 1.2}}
        with patch(
            "och_sidecar.providers.tts_kokoro.KokoroTts"
        ) as MockTts:
            MockTts.return_value = MagicMock()
            _pipeline._make_tts(settings)
            config_arg = MockTts.call_args[0][0]
            assert config_arg.kokoro_voice == "bm_lewis"
            assert config_arg.kokoro_speed == pytest.approx(1.2)


class TestProviderCache:
    """Cached providers across pipeline.run() calls — fixes the apparent
    memory leak from reloading Whisper / Kokoro models on every recording."""

    def test_same_settings_returns_same_instance(self):
        settings = {"tts": {"kokoro_voice": "af_heart", "kokoro_speed": 1.0}}
        with patch("och_sidecar.providers.tts_kokoro.KokoroTts") as MockTts:
            MockTts.side_effect = lambda *_a, **_k: MagicMock(name="kokoro")
            a = _pipeline._make_tts(settings)
            b = _pipeline._make_tts(settings)
        assert a is b
        assert MockTts.call_count == 1

    def test_different_voice_replaces_cached_instance(self):
        with patch("och_sidecar.providers.tts_kokoro.KokoroTts") as MockTts:
            MockTts.side_effect = lambda *_a, **_k: MagicMock(name="kokoro")
            a = _pipeline._make_tts({"tts": {"kokoro_voice": "af_heart"}})
            b = _pipeline._make_tts({"tts": {"kokoro_voice": "bm_lewis"}})
        assert a is not b
        assert MockTts.call_count == 2

    def test_stt_cached_across_calls(self):
        with patch("och_sidecar.providers.stt_mlx_whisper.MlxWhisperStt") as MockStt:
            MockStt.side_effect = lambda *_a, **_k: MagicMock(name="stt")
            a = _pipeline._make_stt({})
            b = _pipeline._make_stt({})
        assert a is b
        assert MockStt.call_count == 1

    def test_vlm_cached_across_calls(self):
        with patch("och_sidecar.providers.vlm_ollama.OllamaVlm") as MockVlm:
            MockVlm.side_effect = lambda *_a, **_k: MagicMock(name="vlm")
            a = _pipeline._make_vlm({})
            b = _pipeline._make_vlm({})
        assert a is b
        assert MockVlm.call_count == 1


class TestOllamaTimeout:
    """Vision inference on a 7B local model with a multi-MB screenshot
    routinely takes >60s on cold start. Pin the new generous timeout."""

    def test_complete_with_image_uses_long_read_timeout(self):
        import httpx
        from och_sidecar.providers.vlm_ollama import OllamaVlm

        vlm = OllamaVlm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "ok"}}
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            vlm.complete("describe", image_bytes=b"fake png")

        timeout = mock_post.call_args.kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read >= 300.0
