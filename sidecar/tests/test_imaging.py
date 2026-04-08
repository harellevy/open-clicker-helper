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


class TestCropAround:
    """`crop_around` supports the two-pass grounding refinement: given a
    rough normalised target from the first pass, crop a window from the
    full-resolution screenshot so the VLM can pinpoint the exact pixel."""

    def test_returns_square_crop_around_centre(self):
        from PIL import Image  # type: ignore[import]

        png = _make_png(2000, 1500)
        result = _imaging.crop_around(png, 0.5, 0.5)
        assert result is not None
        crop_bytes, (x0, y0, w, h) = result
        # The returned rect is in normalised full-image coords and its
        # centre should lie on (0.5, 0.5).
        assert 0.0 <= x0 <= 1.0 and 0.0 <= y0 <= 1.0
        assert abs((x0 + w / 2) - 0.5) < 0.01
        assert abs((y0 + h / 2) - 0.5) < 0.01
        # The crop itself should be decodable and roughly square.
        with Image.open(io.BytesIO(crop_bytes)) as im:
            cw, ch = im.size
            assert cw == ch

    def test_crop_clamped_at_top_left_corner(self):
        """A target at (0, 0) can't be centred in the crop — the crop
        should clamp to the upper-left corner of the image."""
        png = _make_png(2000, 1500)
        result = _imaging.crop_around(png, 0.0, 0.0)
        assert result is not None
        _crop, (x0, y0, _w, _h) = result
        # The rect starts at the image origin.
        assert x0 == 0.0
        assert y0 == 0.0

    def test_crop_clamped_at_bottom_right_corner(self):
        png = _make_png(2000, 1500)
        result = _imaging.crop_around(png, 1.0, 1.0)
        assert result is not None
        _crop, (x0, y0, w, h) = result
        # The crop rect can't extend past the image, so its right/bottom
        # edge coincides with the image's right/bottom edge.
        assert abs((x0 + w) - 1.0) < 1e-6
        assert abs((y0 + h) - 1.0) < 1e-6

    def test_invalid_bytes_returns_none(self):
        out = _imaging.crop_around(b"not a png", 0.5, 0.5)
        assert out is None

    def test_crop_size_respects_max(self):
        """On a huge screenshot the crop must still fit the VLM budget."""
        from PIL import Image  # type: ignore[import]

        png = _make_png(8000, 6000)
        result = _imaging.crop_around(png, 0.4, 0.6)
        assert result is not None
        crop_bytes, _rect = result
        with Image.open(io.BytesIO(crop_bytes)) as im:
            cw, ch = im.size
            assert max(cw, ch) <= _imaging.REFINE_CROP_MAX_PX

    def test_crop_size_respects_min_on_small_images(self):
        """On a small screenshot we'd rather crop the whole thing than
        hand the VLM a few-pixel thumbnail."""
        from PIL import Image  # type: ignore[import]

        png = _make_png(500, 400)
        result = _imaging.crop_around(png, 0.5, 0.5)
        assert result is not None
        crop_bytes, _rect = result
        with Image.open(io.BytesIO(crop_bytes)) as im:
            cw, ch = im.size
            # Crop can't be larger than the source's shortest side.
            assert min(cw, ch) <= 400
