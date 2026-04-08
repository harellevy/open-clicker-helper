"""Screenshot downscaling for fast VLM uploads.

Grounding coordinates are normalized (0.0–1.0), so shrinking the image before
upload is fully transparent to the pipeline: the VLM returns the same
normalized coordinates regardless of pixel size.

`downscale_png` aims to reduce the byte size by roughly ~8x (area ≈ 1/8,
scale factor ≈ 1/√8 ≈ 0.354 per axis) so that a 3000×2000 retina screenshot
comes back around 1060×707 px. Huge wins for upload time with negligible loss
for UI grounding.
"""

from __future__ import annotations

import io
import logging
import math

logger = logging.getLogger(__name__)

# Each axis is scaled so the resulting area is ~1/TARGET_AREA_DIVIDER of the
# original. 8× area reduction matches the "at least 8× smaller" spec and keeps
# UI text legible on retina displays.
TARGET_AREA_DIVIDER = 8
_SCALE_PER_AXIS = 1.0 / math.sqrt(TARGET_AREA_DIVIDER)  # ≈ 0.3535

# Hard floor so a tiny screenshot isn't reduced to noise.
MIN_LONG_EDGE = 512


def downscale_png(png_bytes: bytes) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    """Shrink a PNG so its total byte size is roughly ~8× smaller.

    Returns ``(smaller_png, (orig_w, orig_h), (new_w, new_h))``.

    On any import/decode failure the original bytes are returned unchanged —
    downscaling is an optimisation, never a correctness requirement.
    """
    try:
        from PIL import Image  # type: ignore[import]
    except ImportError:
        logger.warning("Pillow not installed; skipping screenshot downscale")
        return png_bytes, (0, 0), (0, 0)

    try:
        with Image.open(io.BytesIO(png_bytes)) as im:
            im.load()
            orig_size = im.size  # (w, h)

            # Target each axis scaled by _SCALE_PER_AXIS, then raise the long
            # edge back up to MIN_LONG_EDGE so very small screenshots stay
            # readable.
            new_w = max(1, int(round(orig_size[0] * _SCALE_PER_AXIS)))
            new_h = max(1, int(round(orig_size[1] * _SCALE_PER_AXIS)))
            long_edge = max(new_w, new_h)
            if long_edge < MIN_LONG_EDGE and long_edge > 0:
                bump = MIN_LONG_EDGE / long_edge
                new_w = max(1, int(round(new_w * bump)))
                new_h = max(1, int(round(new_h * bump)))

            resized = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
            # RGB (drop alpha) — uploads a bit smaller and the VLM doesn't
            # benefit from transparency.
            if resized.mode not in ("RGB", "L"):
                resized = resized.convert("RGB")

            out = io.BytesIO()
            resized.save(out, format="PNG", optimize=True)
            return out.getvalue(), orig_size, (new_w, new_h)
    except Exception as exc:  # noqa: BLE001
        logger.warning("screenshot downscale failed, using original: %s", exc)
        return png_bytes, (0, 0), (0, 0)


# Crop sizing for the refinement pass. The long edge of the crop is a
# fraction of the original screenshot's long edge, clamped so (a) tiny
# screens still get a usable crop, (b) huge screens don't blow past the
# VLM's practical image budget.
REFINE_CROP_FRAC = 0.22
REFINE_CROP_MIN_PX = 384
REFINE_CROP_MAX_PX = 960


def crop_around(
    png_bytes: bytes,
    norm_x: float,
    norm_y: float,
    *,
    crop_frac: float = REFINE_CROP_FRAC,
    min_size: int = REFINE_CROP_MIN_PX,
    max_size: int = REFINE_CROP_MAX_PX,
) -> tuple[bytes, tuple[float, float, float, float]] | None:
    """Crop a square window around a normalized point on a full-res screenshot.

    Used by the grounding refinement pass. Given a rough normalized target
    from the first (downscaled) grounding call, we crop the corresponding
    region from the **original** full-resolution image and hand that to the
    VLM for precise pixel-level targeting. A 1-px error in a 500×500 crop
    maps back to 1 px in the original — dramatically better than a 1-px
    error in the 8×-downscaled first pass (~2.8 px in the original).

    Returns ``(crop_png_bytes, (x0, y0, w, h))`` where ``(x0, y0, w, h)`` is
    the crop rectangle in **normalized** original-image coordinates (0–1),
    so callers can map refined coordinates back to the full image via
    ``full_x = x0 + refined_x * w``.

    Returns ``None`` on any decode/encode failure — the caller should fall
    back to the rough coordinate.
    """
    try:
        from PIL import Image  # type: ignore[import]
    except ImportError:
        logger.warning("Pillow not installed; skipping crop_around")
        return None

    try:
        with Image.open(io.BytesIO(png_bytes)) as im:
            im.load()
            w, h = im.size
            if w <= 0 or h <= 0:
                return None

            # Crop side length: crop_frac of the long edge, clamped, and
            # capped at the image's shortest side (we can't crop larger
            # than the source).
            side = int(round(max(w, h) * crop_frac))
            side = max(min_size, min(side, max_size))
            side = min(side, w, h)
            if side <= 0:
                return None

            cx = int(round(max(0.0, min(1.0, norm_x)) * w))
            cy = int(round(max(0.0, min(1.0, norm_y)) * h))
            half = side // 2
            x0 = max(0, min(cx - half, w - side))
            y0 = max(0, min(cy - half, h - side))

            crop = im.crop((x0, y0, x0 + side, y0 + side))
            if crop.mode not in ("RGB", "L"):
                crop = crop.convert("RGB")

            out = io.BytesIO()
            crop.save(out, format="PNG", optimize=True)
            return (
                out.getvalue(),
                (x0 / w, y0 / h, side / w, side / h),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("crop_around failed, using rough coordinate: %s", exc)
        return None
