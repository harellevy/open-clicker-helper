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
