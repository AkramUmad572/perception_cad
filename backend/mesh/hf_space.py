"""
Image-to-3D via Hugging Face ZeroGPU Spaces.

three.ws is a shared TRELLIS proxy. When that fleet is full we call the same
(or newer) models on Spaces that still have a free GPU.

No paid key. A free HF_TOKEN raises the daily ZeroGPU budget from ~2 min
(anonymous) to ~5 min. Without a token we still try.

Spaces speak Gradio sse_v3 (heartbeat + queue/data). Use the official client
so ZeroGPU sessions stick.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

from mesh.meshy import MeshBusyError, MeshError

logger = logging.getLogger(__name__)

TRELLIS_V1_SPACE = "trellis-community/TRELLIS"
TRELLIS_V2_SPACE = "microsoft/TRELLIS.2"

BUSY_MARKERS = (
    "gpu quota",
    "exceeded your gpu",
    "zero-gpu",
    "no gpu",
    "cuda out of memory",
    "queue is full",
    "too many requests",
    "provider_busy",
    "overloaded",
    "no capacity",
    "space is disabled",
    "currently down",
    "429",
)


def _is_busy(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in BUSY_MARKERS)


def _raise_space_error(text: str) -> None:
    msg = (text or "Hugging Face Space failed.").strip()
    if _is_busy(msg):
        raise MeshBusyError(msg)
    raise MeshError(msg)


def _looks_like_glb(text: str) -> bool:
    return ".glb" in (text or "").lower()


def _as_glb_url(space: str, value: Any) -> str | None:
    if isinstance(value, str):
        if _looks_like_glb(value) and value.startswith("http"):
            return value
        if value.endswith(".glb"):
            if value.startswith("http"):
                return value
            if space:
                return f"{space}/gradio_api/file={quote(value, safe='/')}"
            return value
        return None
    if isinstance(value, dict):
        url = value.get("url")
        path = value.get("path") or value.get("name")
        orig = str(value.get("orig_name") or "")
        if isinstance(url, str) and _looks_like_glb(url + orig + str(path or "")):
            if url.startswith("http"):
                return url
            if space and url.startswith("/"):
                return f"{space}{url}"
        if isinstance(path, str) and _looks_like_glb(path + orig):
            if space:
                return f"{space}/gradio_api/file={quote(path, safe='/')}"
            return path
    return None


def first_glb_url(space: str, payload: Any) -> str | None:
    """Walk a Gradio complete payload for a downloadable GLB."""
    found = _as_glb_url(space, payload)
    if found:
        return found
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = first_glb_url(space, item)
            if found:
                return found
    elif isinstance(payload, dict):
        for key in ("url", "path", "value", "data", "output"):
            if key in payload:
                found = first_glb_url(space, payload[key])
                if found:
                    return found
        for item in payload.values():
            found = first_glb_url(space, item)
            if found:
                return found
    return None


def _parse_sse_block(event: str, data: str, space: str) -> str | None:
    if event == "error":
        if data in ("", "null"):
            _raise_space_error("Hugging Face Space returned an empty error.")
        try:
            parsed = json.loads(data)
            if isinstance(parsed, str):
                _raise_space_error(parsed)
            _raise_space_error(json.dumps(parsed)[:400])
        except MeshError:
            raise
        except Exception:
            _raise_space_error(data)
    if event != "complete":
        return None
    if not data or data == "null":
        raise MeshError("Hugging Face Space finished without a result.")
    payload = json.loads(data)
    glb = first_glb_url(space, payload)
    if not glb:
        raise MeshError("Hugging Face Space finished without a GLB.")
    return glb


def _local_glb(result: Any) -> Path:
    """Prefer a file the Gradio client already downloaded."""
    items = result if isinstance(result, (list, tuple)) else [result]
    for item in items:
        candidate = None
        if isinstance(item, Path):
            candidate = item
        elif isinstance(item, str) and item.lower().endswith(".glb"):
            candidate = Path(item)
        elif hasattr(item, "path") and str(getattr(item, "path", "")).endswith(".glb"):
            candidate = Path(item.path)
        elif isinstance(item, dict):
            path = item.get("path") or item.get("name")
            if isinstance(path, str) and path.lower().endswith(".glb"):
                candidate = Path(path)
        if candidate and candidate.exists() and candidate.stat().st_size >= 12:
            return candidate
    remote = first_glb_url("", result)
    if remote and not remote.startswith("http"):
        path = Path(remote)
        if path.exists() and path.stat().st_size >= 12:
            return path
    raise MeshError("Hugging Face Space finished without a local GLB.")


def _map_client_error(exc: BaseException) -> None:
    from gradio_client.exceptions import AppError
    from gradio_client.utils import TooManyRequestsError

    if isinstance(exc, TooManyRequestsError):
        raise MeshBusyError(str(exc)) from exc
    if isinstance(exc, AppError) or _is_busy(str(exc)):
        _raise_space_error(str(exc))
    raise MeshError(str(exc) or exc.__class__.__name__) from exc


def _client(space: str, token: str):
    from gradio_client import Client

    return Client(
        space,
        token=token or None,
        verbose=False,
        httpx_kwargs={"timeout": 240.0},
    )


def _predict_trellis_v1(image_path: Path, dest: Path, token: str) -> dict[str, Any]:
    from gradio_client import handle_file

    client = _client(TRELLIS_V1_SPACE, token)
    try:
        client.predict(api_name="/start_session")
    except Exception as exc:
        logger.info("TRELLIS start_session skipped: %s", exc)
    result = client.predict(
        handle_file(str(image_path)),
        [],
        0,
        7.5,
        12,
        3.0,
        12,
        "stochastic",
        0.95,
        1024,
        api_name="/generate_and_extract_glb",
    )
    shutil.copyfile(_local_glb(result), dest)
    return {"ok": True, "textured": True, "provider": "hf_space"}


def _predict_trellis_v2(image_path: Path, dest: Path, token: str) -> dict[str, Any]:
    from gradio_client import handle_file

    client = _client(TRELLIS_V2_SPACE, token)
    try:
        client.predict(api_name="/start_session")
    except Exception as exc:
        logger.info("TRELLIS.2 start_session skipped: %s", exc)
    image = handle_file(str(image_path))
    try:
        image = client.predict(image, api_name="/preprocess_image")
    except Exception as exc:
        logger.info("TRELLIS.2 preprocess skipped: %s", exc)
        image = handle_file(str(image_path))
    client.predict(
        image,
        0,
        "1024",
        7.5,
        0.7,
        12,
        5.0,
        7.5,
        0.5,
        12,
        3.0,
        1.0,
        0.0,
        12,
        3.0,
        api_name="/image_to_3d",
    )
    result = client.predict(100000, 1024, api_name="/extract_glb")
    shutil.copyfile(_local_glb(result), dest)
    return {"ok": True, "textured": True, "provider": "hf_space"}


def _run_lane(name: str, predict, image_path: Path, dest: Path, token: str) -> dict[str, Any]:
    logger.info("HF Space image-to-3D: %s", name)
    try:
        return predict(image_path, dest, token)
    except MeshError:
        raise
    except Exception as exc:
        _map_client_error(exc)
        raise


async def generate_hf_space_glb(
    image_path: Path,
    dest: Path,
    *,
    token: str = "",
    timeout_s: float = 240.0,
) -> dict[str, Any]:
    """
    Sculpt a local photo on a free HF Space. Tries community TRELLIS first
    (yesterday's three.ws look), then Microsoft TRELLIS.2.
    """
    if not image_path or not Path(image_path).exists():
        raise MeshError("No local photo to upload to Hugging Face.")
    image_path = Path(image_path)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    lanes = (
        ("trellis", _predict_trellis_v1),
        ("trellis2", _predict_trellis_v2),
    )
    for name, predict in lanes:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_lane, name, predict, image_path, dest, token),
                timeout=timeout_s,
            )
        except MeshBusyError:
            raise
        except Exception as exc:
            logger.warning("HF Space %s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
            dest.unlink(missing_ok=True)
            if _is_busy(str(exc)):
                raise MeshBusyError(str(exc)) from exc
    raise MeshError(" | ".join(errors) or "Hugging Face Spaces failed.")
