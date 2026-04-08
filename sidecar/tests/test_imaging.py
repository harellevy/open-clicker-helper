"""Tests for screenshot downscaling."""

from __future__ import annotations

import io

import pytest

from och_sidecar import imaging as _imaging


def _make_png(w: int, h: int) -> bytes:
    """Build a solid-color PNG of the requested size using Pillow."""
    from PIL import Image  # type: ignore[import]

    img = Image.new("RGB", (w, h), color=(120, 160, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestDownscalePng:
    def test_shrinks_large_image(self):
        png = _make_png(3000, 2000)
        small, orig, new = _imaging.downscale_png(png)
        assert orig == (3000, 2000)
        # area should be roughly 1/8 of the original
        orig_area = orig[0] * orig[1]
        new_area = new[0] * new[1]
        assert new_area < orig_area
        assert new_area <= orig_area / 6  # tolerate rounding; target is ~1/8
        # bytes should also be strictly smaller
        assert len(small) < len(png)

    def test_small_image_not_reduced_to_noise(self):
        """A tiny input should stay at or above the MIN_LONG_EDGE floor so the
        VLM has something meaningful to look at."""
        png = _make_png(400, 300)
        _small, _orig, new = _imaging.downscale_png(png)
        assert max(new) >= _imaging.MIN_LONG_EDGE or max(new) >= max(400, 300)

    def test_invalid_bytes_returned_unchanged(self):
        """Bad PNG bytes should pass through rather than crashing the pipeline."""
        garbage = b"not a real png at all"
        out, orig, new = _imaging.downscale_png(garbage)
        assert out == garbage
        assert orig == (0, 0)
        assert new == (0, 0)

    def test_output_is_valid_png(self):
        from PIL import Image  # type: ignore[import]

        png = _make_png(1600, 1000)
        small, _orig, new = _imaging.downscale_png(png)
        with Image.open(io.BytesIO(small)) as im:
            assert im.size == new
