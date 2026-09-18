"""Pick a mesh factory: three.ws (free) → NVIDIA TRELLIS → Meshy."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from mesh.cleanup import strip_base_plate
from mesh.meshy import generate_mesh_glb as generate_meshy_glb
from mesh.nvidia_trellis import generate_nvidia_glb
from mesh.three_ws import generate_three_ws_glb, generate_three_ws_glb_from_image

logger = logging.getLogger(__name__)

# NVIDIA's hosted TRELLIS burns ~90s before returning 500 when the function is
# degraded, so cap its slice and stop retrying it for a while after failures.
NVIDIA_TIMEOUT_S = 45.0
NVIDIA_TRIP_AFTER = 2
NVIDIA_COOLDOWN_S = 600.0

_nvidia_failures = 0
_nvidia_blocked_until = 0.0


def _nvidia_available() -> bool:
    return time.monotonic() >= _nvidia_blocked_until


def _note_nvidia_failure() -> None:
    global _nvidia_failures, _nvidia_blocked_until
    _nvidia_failures += 1
    if _nvidia_failures >= NVIDIA_TRIP_AFTER:
        _nvidia_blocked_until = time.monotonic() + NVIDIA_COOLDOWN_S
        logger.warning(
            "NVIDIA TRELLIS looks down; skipping it for %.0f min",
            NVIDIA_COOLDOWN_S / 60,
        )


def _note_nvidia_success() -> None:
    global _nvidia_failures, _nvidia_blocked_until
    _nvidia_failures = 0
    _nvidia_blocked_until = 0.0


def mesh_ready(settings: Any) -> bool:
    """True when at least one factory can run. three.ws is keyless."""
    if getattr(settings, "meshy_api_key", ""):
        return True
    if getattr(settings, "nvidia_api_key", ""):
        return True
    return bool(getattr(settings, "three_ws_enabled", True))


def mesh_providers(settings: Any) -> list[str]:
    names: list[str] = []
    if bool(getattr(settings, "three_ws_enabled", True)):
        names.append("three_ws")
    if getattr(settings, "nvidia_api_key", ""):
        names.append("nvidia")
    if getattr(settings, "meshy_api_key", ""):
        names.append("meshy")
    return names


async def generate_mesh_glb_from_image(
    image_url: str,
    output_dir: Path,
    *,
    prompt: str = "",
    timeout_s: float = 240.0,
    quality: str = "high",
) -> dict[str, Any]:
    """
    Image-to-3D. three.ws is the only lane here: NVIDIA's hosted TRELLIS accepts
    four fixed sample images, and Meshy image-to-3D needs a paid key.
    """
    t0 = time.perf_counter()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_id = uuid.uuid4().hex[:12]
    dest = output_dir / f"{model_id}.glb"

    try:
        meta = await generate_three_ws_glb_from_image(
            image_url, dest, prompt=prompt, timeout_s=timeout_s, quality=quality
        )
    except Exception as exc:
        logger.warning("three.ws image-to-3D failed: %s", exc)
        dest.unlink(missing_ok=True)
        return {
            "ok": False,
            "error": str(exc),
            "error_type": "mesh",
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }

    strip_base_plate(dest)
    return {
        "ok": True,
        "model_id": model_id,
        "glb_path": str(dest),
        "exec_ms": (time.perf_counter() - t0) * 1000,
        "textured": bool(meta.get("textured", True)),
        "provider": "three_ws_image",
    }


async def generate_mesh_glb(
    prompt: str,
    output_dir: Path,
    *,
    meshy_api_key: str = "",
    nvidia_api_key: str = "",
    three_ws: bool = True,
    timeout_s: float = 120.0,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Try free lanes first, then paid Meshy.

    `api_key` is accepted as a Meshy alias so older callers still work.
    """
    t0 = time.perf_counter()
    meshy_key = meshy_api_key or api_key
    if not (prompt or "").strip():
        return {"ok": False, "error": "Missing mesh prompt.", "error_type": "validation"}

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_id = uuid.uuid4().hex[:12]
    dest = output_dir / f"{model_id}.glb"
    errors: list[str] = []

    if three_ws:
        try:
            logger.info("Mesh factory: three.ws")
            meta = await generate_three_ws_glb(prompt, dest, timeout_s=timeout_s)
            strip_base_plate(dest)
            return {
                "ok": True,
                "model_id": model_id,
                "glb_path": str(dest),
                "exec_ms": (time.perf_counter() - t0) * 1000,
                "textured": bool(meta.get("textured", True)),
                "provider": "three_ws",
            }
        except Exception as exc:
            logger.warning("three.ws failed: %s", exc)
            errors.append(f"three.ws: {exc}")
            if dest.exists():
                dest.unlink(missing_ok=True)

    if nvidia_api_key and _nvidia_available():
        try:
            logger.info("Mesh factory: NVIDIA TRELLIS")
            meta = await generate_nvidia_glb(
                prompt, dest, nvidia_api_key, timeout_s=min(timeout_s, NVIDIA_TIMEOUT_S)
            )
            _note_nvidia_success()
            strip_base_plate(dest)
            return {
                "ok": True,
                "model_id": model_id,
                "glb_path": str(dest),
                "exec_ms": (time.perf_counter() - t0) * 1000,
                "textured": bool(meta.get("textured", True)),
                "provider": "nvidia",
            }
        except Exception as exc:
            logger.warning("NVIDIA TRELLIS failed: %s", exc)
            _note_nvidia_failure()
            errors.append(f"nvidia: {exc}")
            if dest.exists():
                dest.unlink(missing_ok=True)

    if meshy_key:
        result = await generate_meshy_glb(
            prompt, output_dir, meshy_key, timeout_s=timeout_s
        )
        if result.get("ok"):
            result["provider"] = "meshy"
            result["exec_ms"] = (time.perf_counter() - t0) * 1000
            if result.get("glb_path"):
                strip_base_plate(result["glb_path"])
            return result
        errors.append(f"meshy: {result.get('error') or 'failed'}")

    if not errors:
        return {
            "ok": False,
            "error": "No mesh factory available.",
            "error_type": "config",
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }
    return {
        "ok": False,
        "error": " | ".join(errors),
        "error_type": "mesh",
        "exec_ms": (time.perf_counter() - t0) * 1000,
    }
