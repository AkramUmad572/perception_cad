"""Prepare a reference photo for image-to-3D.

Image-to-3D reconstructs whatever fills the frame. Hand it a product photo and
it builds the photo — a flat card — instead of the subject. So the subject has
to be cut out, isolated and centred before it is sent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Background is sampled from the border and flood-filled inward, so highlights
# inside the subject (white eyes, glare) are not punched out.
BG_TOLERANCE = 38
MIN_SUBJECT_RATIO = 0.02
PAD_RATIO = 0.12


def _background_mask(rgb: Any, tolerance: int) -> Any:
    """Flood fill inward from the border; True where background."""
    import numpy as np
    from scipy import ndimage

    h, w, _ = rgb.shape
    border = np.concatenate(
        [rgb[0, :, :], rgb[h - 1, :, :], rgb[:, 0, :], rgb[:, w - 1, :]]
    ).astype(np.float32)
    bg_color = np.median(border, axis=0)

    close = np.linalg.norm(rgb.astype(np.float32) - bg_color, axis=2) < tolerance

    # Only the region connected to the frame edge is background.
    labels, count = ndimage.label(close)
    if count == 0:
        return np.zeros((h, w), dtype=bool)
    edge_labels = set(labels[0, :]) | set(labels[h - 1, :])
    edge_labels |= set(labels[:, 0]) | set(labels[:, w - 1])
    edge_labels.discard(0)
    if not edge_labels:
        return np.zeros((h, w), dtype=bool)
    return np.isin(labels, list(edge_labels))


def _largest_blob(fg: Any) -> Any:
    """Keep the biggest object — drops the key ring, tags and stray hardware."""
    import numpy as np
    from scipy import ndimage

    labels, count = ndimage.label(fg)
    if count <= 1:
        return fg
    sizes = ndimage.sum(fg, labels, range(1, count + 1))
    return labels == (int(np.argmax(sizes)) + 1)


def isolate_subject(
    src: Path | str,
    dest: Path | str,
    tolerance: int = BG_TOLERANCE,
    keep_largest: bool = True,
) -> dict[str, Any]:
    """
    Cut the subject out of a photo onto a transparent square canvas.

    Returns stats so a caller can tell a good cut-out from a no-op.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    im = Image.open(str(src)).convert("RGB")
    rgb = np.asarray(im)
    h, w, _ = rgb.shape

    bg = _background_mask(rgb, tolerance)
    fg = ~bg
    # Close pin holes and shave single-pixel fringe from the matte.
    fg = ndimage.binary_closing(fg, np.ones((5, 5), bool))
    fg = ndimage.binary_opening(fg, np.ones((3, 3), bool))

    ratio = float(fg.mean())
    if ratio < MIN_SUBJECT_RATIO or ratio > 0.98:
        return {"ok": False, "reason": f"subject fills {ratio:.0%} of the frame"}

    if keep_largest:
        fg = _largest_blob(fg)

    ys, xs = np.nonzero(fg)
    if ys.size == 0:
        return {"ok": False, "reason": "nothing left after masking"}
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1

    rgba = np.dstack([rgb, (fg * 255).astype(np.uint8)])[y0:y1, x0:x1]
    cut = Image.fromarray(rgba, "RGBA")

    side = int(max(cut.size) * (1 + 2 * PAD_RATIO))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(cut, ((side - cut.width) // 2, (side - cut.height) // 2), cut)
    canvas = canvas.resize((1024, 1024), Image.LANCZOS)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(dest))

    return {
        "ok": True,
        "subject_ratio": round(ratio, 4),
        "source_size": (w, h),
        "crop_box": (x0, y0, x1, y1),
        "path": str(dest),
    }
