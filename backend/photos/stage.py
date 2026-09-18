"""Save a photo locally, cut the subject out, return the URL three.ws can fetch."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from app.config import Settings
from mesh.refimage import isolate_subject

logger = logging.getLogger(__name__)


def suffix_for(mime: str, name: str = "") -> str:
    mime = (mime or "").lower()
    if "png" in mime:
        return ".png"
    if "webp" in mime:
        return ".webp"
    lower = name.lower()
    if lower.endswith(".png"):
        return ".png"
    if lower.endswith(".webp"):
        return ".webp"
    return ".jpg"


def public_ref_url(settings: Settings, filename: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/media/ref/{filename}"


def stage_photo(
    raw: bytes,
    settings: Settings,
    *,
    mime: str = "image/jpeg",
    name: str = "",
    file_id: str | None = None,
) -> dict[str, str]:
    """
    Write the original for the AR picker, plus a cut-out for image-to-3D.

    Returns paths/URLs. Falls back to the original if isolation fails.
    """
    settings.ref_dir.mkdir(parents=True, exist_ok=True)
    suffix = suffix_for(mime, name)
    stem = file_id or uuid.uuid4().hex[:12]
    raw_path = settings.ref_dir / f"drive_{stem}_raw{suffix}"
    raw_path.write_bytes(raw)

    served = raw_path.name
    cut_path = settings.ref_dir / f"drive_{stem}.png"
    cut = isolate_subject(raw_path, cut_path)
    if cut.get("ok"):
        served = cut_path.name
    else:
        logger.info("Subject isolation skipped for %s: %s", stem, cut.get("reason"))

    return {
        "file_id": stem,
        "preview_url": f"/media/ref/{raw_path.name}",
        "build_filename": served,
        "build_url": public_ref_url(settings, served),
        "raw_path": str(raw_path),
    }
