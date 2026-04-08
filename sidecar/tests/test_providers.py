"""Unit tests for provider configs and graceful error handling.

These tests run without any ML dependencies (no mlx-whisper, kokoro, ollama),
so they pass in CI on Linux/macOS out of the box.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from och_sidecar.providers.base import ProviderConfig, ProviderError
from och_sidecar.providers.stt_mlx_whisper import MlxWhisperConfig, MlxWhisperStt
from och_sidecar.providers.tts_kokoro import KokoroConfig, KokoroTts
from och_sidecar.providers.vlm_ollama import OllamaConfig, OllamaVlm


# ── MlxWhisperConfig ──────────────────────────────────────────────────────────

class TestMlxWhisperConfig:
    def test_default_model(self):
        cfg = MlxWhisperConfig()
        assert cfg.mlx_model == "mlx-community/whisper-base-mlx"

    def test_custom_model(self):
        cfg = MlxWhisperConfig(mlx_model="mlx-community/whisper-large-mlx")
        assert cfg.mlx_model == "mlx-community/whisper-large-mlx"

    def test_is_provider_config(self):
        assert isinstance(MlxWhisperConfig(), ProviderConfig)


class TestMlxWhisperStt:
    def test_kind_and_id(self):
        assert MlxWhisperStt.kind == "stt"
        assert MlxWhisperStt.id == "mlx-whisper"

    def test_default_init(self):
        stt = MlxWhisperStt()
        assert stt._model == "mlx-community/whisper-base-mlx"

    def test_model_kwarg(self):
        stt = MlxWhisperStt(model="mlx-community/whisper-large-mlx")
        assert stt._model == "mlx-community/whisper-large-mlx"

    def test_config_arg(self):
        cfg = MlxWhisperConfig(mlx_model="mlx-community/whisper-tiny-mlx")
        stt = MlxWhisperStt(cfg)
        assert stt._model == "mlx-community/whisper-tiny-mlx"

    def test_test_returns_false_when_mlx_whisper_missing(self):
        with patch.dict(sys.modules, {"mlx_whisper": None}):
            # importlib.util.find_spec returns None when module is None in sys.modules
            stt = MlxWhisperStt()
            result = stt.test()
        assert result["ok"] is False
        assert "error" in result

    def test_transcribe_raises_provider_error_when_import_fails(self):
        stt = MlxWhisperStt()
        with patch.dict(sys.modules, {"mlx_whisper": None}):
            with pytest.raises((ProviderError, ImportError)):
                stt.transcribe(b"fake wav bytes")


# ── KokoroConfig ─────────────────────────────────────────────────────────────

class TestKokoroConfig:
    def test_defaults(self):
        cfg = KokoroConfig()
        assert cfg.kokoro_voice == "af_heart"
        assert cfg.kokoro_speed == pytest.approx(1.0)

    def test_custom_values(self):
        cfg = KokoroConfig(kokoro_voice="bm_lewis", kokoro_speed=1.3)
        assert cfg.kokoro_voice == "bm_lewis"
        assert cfg.kokoro_speed == pytest.approx(1.3)


class TestKokoroTts:
    def test_kind_and_id(self):
        assert KokoroTts.kind == "tts"
        assert KokoroTts.id == "kokoro"

    def test_default_init(self):
        tts = KokoroTts()
        assert tts._voice == "af_heart"
        assert tts._speed == pytest.approx(1.0)

    def test_kwarg_init(self):
        tts = KokoroTts(voice="bm_lewis", speed=0.9)
        assert tts._voice == "bm_lewis"
        assert tts._speed == pytest.approx(0.9)

    def test_config_arg(self):
        cfg = KokoroConfig(kokoro_voice="af_sky", kokoro_speed=1.2)
        tts = KokoroTts(cfg)
        assert tts._voice == "af_sky"

    def test_test_returns_false_when_kokoro_missing(self):
        with patch.dict(sys.modules, {"kokoro": None}):
            tts = KokoroTts()
            result = tts.test()
        assert result["ok"] is False

    def test_get_pipeline_raises_provider_error_when_import_fails(self):
        tts = KokoroTts()
        tts._pipeline = None
        with patch.dict(sys.modules, {"kokoro": None}):
            with pytest.raises((ProviderError, ImportError)):
                tts._get_pipeline()


# ── OllamaConfig ──────────────────────────────────────────────────────────────

class TestOllamaConfig:
    def test_defaults(self):
        cfg = OllamaConfig()
        assert cfg.ollama_model == "qwen2.5-vl:7b"
        assert cfg.ollama_url == "http://localhost:11434"

    def test_custom_values(self):
        cfg = OllamaConfig(ollama_model="llama3.2-vision:11b", ollama_url="http://myhost:11434")
        assert cfg.ollama_model == "llama3.2-vision:11b"
        assert "myhost" in cfg.ollama_url


class TestOllamaVlm:
    def test_kind_and_id(self):
        assert OllamaVlm.kind == "vlm"
        assert OllamaVlm.id == "ollama"

    def test_default_init(self):
        vlm = OllamaVlm()
        assert vlm._model == "qwen2.5-vl:7b"
        assert vlm._base_url == "http://localhost:11434"

    def test_kwarg_init(self):
        vlm = OllamaVlm(model="llama3.2-vision:11b", base_url="http://myhost:11434")
        assert vlm._model == "llama3.2-vision:11b"
        assert "myhost" in vlm._base_url

    def test_base_url_trailing_slash_stripped(self):
        vlm = OllamaVlm(base_url="http://localhost:11434/")
        assert not vlm._base_url.endswith("/")

    def test_test_returns_false_when_ollama_not_running(self):
        import httpx
        vlm = OllamaVlm()
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            result = vlm.test()
        assert result["ok"] is False
        assert "error" in result

    def test_complete_raises_provider_error_on_http_error(self):
        import httpx
        vlm = OllamaVlm()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        with patch("httpx.post", side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp)):
            with pytest.raises(ProviderError):
                vlm.complete("hello")

    def test_complete_raises_provider_error_on_connection_error(self):
        import httpx
        vlm = OllamaVlm()
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(ProviderError):
                vlm.complete("hello")

    def test_locate_delegates_to_complete(self):
        vlm = OllamaVlm()
        with patch.object(vlm, "complete", return_value="top-left corner") as mock_complete:
            result = vlm.locate(b"png bytes", "where is the button?")
        mock_complete.assert_called_once_with("where is the button?", image_bytes=b"png bytes")
        assert "explanation" in result
        assert result["explanation"] == "top-left corner"
