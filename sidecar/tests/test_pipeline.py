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
        # "debug" is only present when debug_mode is enabled.
        assert {"transcript", "answer", "audio_b64", "steps", "timings"} <= set(final.keys())
        assert "debug" not in final

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
        """When image_b64 is present the pipeline downscales and delegates to grounding.locate."""
        from och_sidecar import grounding as _grounding
        from och_sidecar import imaging as _imaging

        audio_b64 = _b64(_fake_wav())
        raw_img = b"\x89PNG\r\n\x1a\n"
        small_img = b"\x89PNG-small"
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
            patch.object(
                _imaging, "downscale_png", return_value=(small_img, (100, 80), (35, 28))
            ) as mock_downscale,
            patch.object(_grounding, "locate", return_value={"steps": fake_steps, "raw": "raw"}) as mock_locate,
        ):
            events_list, final = _collect(_pipeline.run(audio_b64, image_b64, {}))

        # downscale must be called on the decoded original image
        mock_downscale.assert_called_once_with(raw_img)
        # grounding.locate must receive the *downscaled* image and the transcript
        mock_locate.assert_called_once_with(
            mock_vlm, small_img, "transcribed text", system_prompt=None
        )
        assert final["steps"] == fake_steps

    def test_pipeline_emits_image_downscaled_event(self):
        """Grounding mode should emit an image_downscaled event before grounding runs."""
        from och_sidecar import grounding as _grounding
        from och_sidecar import imaging as _imaging

        audio_b64 = _b64(_fake_wav())
        image_b64 = _b64(b"\x89PNG\r\n\x1a\nfake")
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "do it"
        mock_vlm = MagicMock()
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b""

        with (
            patch.object(_pipeline, "_make_stt", return_value=mock_stt),
            patch.object(_pipeline, "_make_vlm", return_value=mock_vlm),
            patch.object(_pipeline, "_make_tts", return_value=mock_tts),
            patch.object(_imaging, "downscale_png", return_value=(b"s", (200, 100), (71, 35))),
            patch.object(_grounding, "locate", return_value={"steps": [{"x": 0.5, "y": 0.5, "explanation": "ok"}], "raw": "r"}),
        ):
            events, final = _collect(_pipeline.run(audio_b64, image_b64, {}))

        names = [e for e, _ in events]
        assert "image_downscaled" in names
        payload = dict(events)["image_downscaled"]
        assert payload["orig_size"] == [200, 100]
        assert payload["new_size"] == [71, 35]
        # image_b64 stays out unless debug mode is on
        assert "image_b64" not in payload

    def test_debug_mode_emits_caption_and_attaches_debug(self):
        """With debug enabled the pipeline should call grounding.caption and
        attach a `debug` block to the final result."""
        from och_sidecar import grounding as _grounding
        from och_sidecar import imaging as _imaging

        audio_b64 = _b64(_fake_wav())
        image_b64 = _b64(b"\x89PNG")
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "what is this"
        mock_vlm = MagicMock()
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b""

        with (
            patch.object(_pipeline, "_make_stt", return_value=mock_stt),
            patch.object(_pipeline, "_make_vlm", return_value=mock_vlm),
            patch.object(_pipeline, "_make_tts", return_value=mock_tts),
            patch.object(_imaging, "downscale_png", return_value=(b"tiny", (100, 80), (35, 28))),
            patch.object(_grounding, "caption", return_value="Settings page open") as mock_caption,
            patch.object(
                _grounding,
                "locate",
                return_value={"steps": [{"x": 0.5, "y": 0.5, "explanation": "ok"}], "raw": "raw output"},
            ),
        ):
            events, final = _collect(
                _pipeline.run(audio_b64, image_b64, {"debug": {"enabled": True}})
            )

        names = [e for e, _ in events]
        assert "caption_start" in names
        assert "caption_done" in names
        caption_done = dict(events)["caption_done"]
        assert caption_done["caption"] == "Settings page open"
        # final.debug block is populated
        assert "debug" in final
        assert final["debug"]["caption"] == "Settings page open"
        assert final["debug"]["grounding_raw"] == "raw output"
        assert "screenshot_b64" in final["debug"]
        assert final["debug"]["screenshot_b64"]
        mock_caption.assert_called_once()

    def test_system_prompts_passed_to_grounding(self):
        """When `system_prompts.grounding` is set in settings, locate() gets it."""
        from och_sidecar import grounding as _grounding
        from och_sidecar import imaging as _imaging

        audio_b64 = _b64(_fake_wav())
        image_b64 = _b64(b"\x89PNG")
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "click"
        mock_vlm = MagicMock()
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b""

        with (
            patch.object(_pipeline, "_make_stt", return_value=mock_stt),
            patch.object(_pipeline, "_make_vlm", return_value=mock_vlm),
            patch.object(_pipeline, "_make_tts", return_value=mock_tts),
            patch.object(_imaging, "downscale_png", return_value=(b"s", (10, 10), (4, 4))),
            patch.object(_imaging, "crop_around", return_value=None),
            patch.object(
                _grounding,
                "locate",
                return_value={"steps": [{"x": 0.5, "y": 0.5, "explanation": "ok"}], "raw": ""},
            ) as mock_locate,
            patch.object(
                _grounding,
                "refine",
                return_value=None,
            ),
        ):
            _collect(
                _pipeline.run(
                    audio_b64,
                    image_b64,
                    {
                        "system_prompts": {"grounding": "CUSTOM"},
                        "grounding": {"refine": False},
                    },
                )
            )

        kwargs = mock_locate.call_args.kwargs
        assert kwargs.get("system_prompt") == "CUSTOM"

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


class TestEmptyTranscriptCancels:
    """When STT returns nothing, the pipeline should short-circuit before
    calling grounding/VLM/TTS and emit a `cancelled` result so the overlay
    can show a brief notice."""

    def _run_with_transcript(self, transcript: str, image_b64: str | None = None):
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = transcript
        mock_vlm = MagicMock()
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b""

        with (
            patch.object(_pipeline, "_make_stt", return_value=mock_stt),
            patch.object(_pipeline, "_make_vlm", return_value=mock_vlm),
            patch.object(_pipeline, "_make_tts", return_value=mock_tts),
        ):
            events, final = _collect(
                _pipeline.run(_b64(_fake_wav()), image_b64, {})
            )
        return events, final, mock_stt, mock_vlm, mock_tts

    def test_empty_transcript_short_circuits(self):
        events, final, _stt, mock_vlm, mock_tts = self._run_with_transcript("")
        assert final.get("cancelled") == "empty_transcript"
        assert final.get("steps") == []
        assert final.get("audio_b64") == ""
        # Neither the VLM nor the TTS should have been called.
        mock_vlm.complete.assert_not_called()
        mock_tts.synthesize.assert_not_called()

    def test_whitespace_only_transcript_short_circuits(self):
        _events, final, _stt, mock_vlm, mock_tts = self._run_with_transcript("   \n  ")
        assert final.get("cancelled") == "empty_transcript"
        mock_vlm.complete.assert_not_called()
        mock_tts.synthesize.assert_not_called()

    def test_empty_transcript_in_grounding_mode_still_cancels(self):
        """Even with a screenshot present, no speech means no task — we
        must not burn the VLM on a meaningless image-only request."""
        _events, final, _stt, mock_vlm, mock_tts = self._run_with_transcript(
            "", image_b64=_b64(b"\x89PNG")
        )
        assert final.get("cancelled") == "empty_transcript"
        mock_vlm.complete.assert_not_called()
        mock_tts.synthesize.assert_not_called()

    def test_nonempty_transcript_still_runs_full_pipeline(self):
        """Sanity check — a normal transcript should still hit VLM and TTS."""
        _events, final, _stt, mock_vlm, mock_tts = self._run_with_transcript(
            "hello"
        )
        assert "cancelled" not in final
        mock_vlm.complete.assert_called()
        mock_tts.synthesize.assert_called()


class TestRefinementPass:
    """Two-pass crop-and-refine grounding — the first call runs on the
    downscaled image for speed, the second uses a full-res crop for
    pixel-accurate coordinates."""

    def _run_refine(
        self,
        refine_result: dict | None,
        crop_rect: tuple[float, float, float, float] = (0.2, 0.3, 0.4, 0.4),
        settings: dict | None = None,
    ):
        from och_sidecar import grounding as _grounding
        from och_sidecar import imaging as _imaging

        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "click save"
        mock_vlm = MagicMock()
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b""

        with (
            patch.object(_pipeline, "_make_stt", return_value=mock_stt),
            patch.object(_pipeline, "_make_vlm", return_value=mock_vlm),
            patch.object(_pipeline, "_make_tts", return_value=mock_tts),
            patch.object(
                _imaging,
                "downscale_png",
                return_value=(b"small", (2000, 1500), (707, 530)),
            ),
            patch.object(
                _imaging,
                "crop_around",
                return_value=(b"crop-png", crop_rect),
            ) as mock_crop,
            patch.object(
                _grounding,
                "locate",
                return_value={
                    "steps": [{"x": 0.5, "y": 0.5, "explanation": "save button"}],
                    "raw": "first pass",
                },
            ) as mock_locate,
            patch.object(
                _grounding, "refine", return_value=refine_result
            ) as mock_refine,
        ):
            events, final = _collect(
                _pipeline.run(
                    _b64(_fake_wav()),
                    _b64(b"\x89PNG-full-res"),
                    settings or {},
                )
            )

        return events, final, mock_crop, mock_locate, mock_refine

    def test_refine_remaps_coords_to_full_image(self):
        """When refine returns (0.5, 0.5) within a crop that occupies
        (x0=0.2, y0=0.3, w=0.4, h=0.4) of the full image, the final
        coordinate should be (0.2 + 0.5*0.4, 0.3 + 0.5*0.4) = (0.4, 0.5)."""
        events, final, _crop, _locate, mock_refine = self._run_refine(
            refine_result={"x": 0.5, "y": 0.5, "explanation": "exact centre"},
            crop_rect=(0.2, 0.3, 0.4, 0.4),
        )
        mock_refine.assert_called_once()
        assert final["steps"][0]["x"] == pytest.approx(0.4)
        assert final["steps"][0]["y"] == pytest.approx(0.5)
        # The explanation from the first pass is preserved — it describes
        # the element; the refine call explains only the pixel.
        assert final["steps"][0]["explanation"] == "save button"

    def test_refine_failure_falls_back_to_rough(self):
        """If refine returns None (parse failure, provider error), the
        rough coordinate from the first pass must be used unchanged."""
        events, final, _crop, _locate, _refine = self._run_refine(
            refine_result=None
        )
        assert final["steps"][0]["x"] == pytest.approx(0.5)
        assert final["steps"][0]["y"] == pytest.approx(0.5)

    def test_refine_passes_full_res_image_to_crop(self):
        """`crop_around` must be called with the ORIGINAL image bytes, not
        the downscaled version — the whole point is that the second pass
        works at full pixel resolution."""
        events, final, mock_crop, *_ = self._run_refine(
            refine_result={"x": 0.5, "y": 0.5, "explanation": "ok"}
        )
        # crop_around(png_bytes, norm_x, norm_y, ...) — first arg is bytes
        args = mock_crop.call_args.args
        assert args[0] == b"\x89PNG-full-res"
        # The rough normalised coord from the first pass
        assert args[1] == pytest.approx(0.5)
        assert args[2] == pytest.approx(0.5)

    def test_refine_pass_emits_events(self):
        events, _final, *_ = self._run_refine(
            refine_result={"x": 0.5, "y": 0.5, "explanation": "ok"}
        )
        names = [e for e, _ in events]
        assert "refine_start" in names
        assert "refine_done" in names

    def test_refine_disabled_skips_second_pass(self):
        """When `grounding.refine` is false the refine call must not happen."""
        _events, final, _crop, _locate, mock_refine = self._run_refine(
            refine_result={"x": 0.9, "y": 0.9, "explanation": "ok"},
            settings={"grounding": {"refine": False}},
        )
        mock_refine.assert_not_called()
        # Rough coords pass through.
        assert final["steps"][0]["x"] == pytest.approx(0.5)
        assert final["steps"][0]["y"] == pytest.approx(0.5)


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
