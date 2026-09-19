"""
Meshy text-to-3D client.

Pipeline calls generate_mesh_glb(); swap this module for Tripo later.
Preview (shape) then refine (PBR textures). If refine times out, return the
preview GLB rather than failing the whole request.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MESHY_TEXT_TO_3D = "https://api.meshy.ai/openapi/v2/text-to-3d"
POLL_INTERVAL_S = 3.0
DEFAULT_TIMEOUT_S = 90.0


class MeshError(Exception):
    """Meshy task failed or was misconfigured."""


class MeshBusyError(MeshError):
    """Provider has no free capacity. Nothing is wrong with the request."""


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _task_id(payload: dict[str, Any]) -> str | None:
    result = payload.get("result")
    if isinstance(result, str) and result:
        return result
    ident = payload.get("id")
    if isinstance(ident, str) and ident:
        return ident
    return None


def _error_message(payload: dict[str, Any], fallback: str) -> str:
    err = payload.get("task_error") or payload.get("error") or {}
    if isinstance(err, dict):
        msg = err.get("message") or err.get("error")
        if msg:
            return str(msg)
    if isinstance(err, str) and err:
        return err
    return fallback


async def _create_task(
    client: httpx.AsyncClient,
    api_key: str,
    body: dict[str, Any],
) -> str:
    resp = await client.post(MESHY_TEXT_TO_3D, headers=_headers(api_key), json=body)
    if resp.status_code in (401, 403):
        raise MeshError("Meshy rejected the API key.")
    if resp.status_code == 402:
        raise MeshError("Meshy is out of credits.")
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MeshError(f"Meshy create failed ({resp.status_code}): {resp.text[:300]}") from exc
    data = resp.json()
    task_id = _task_id(data)
    if not task_id:
        raise MeshError("Meshy did not return a task id.")
    return task_id


async def _poll_task(
    client: httpx.AsyncClient,
    api_key: str,
    task_id: str,
    deadline: float,
) -> dict[str, Any]:
    url = f"{MESHY_TEXT_TO_3D}/{task_id}"
    headers = _headers(api_key)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MeshError("Meshy timed out while generating.")
        resp = await client.get(url, headers=headers)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MeshError(f"Meshy poll failed ({resp.status_code})") from exc
        data = resp.json()
        status = (data.get("status") or "").upper()
        if status == "SUCCEEDED":
            return data
        if status in ("FAILED", "CANCELED", "CANCELLED"):
            raise MeshError(_error_message(data, f"Meshy task {status.lower()}."))
        await asyncio.sleep(min(POLL_INTERVAL_S, max(remaining, 0.5)))


async def _download_glb(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    if not resp.content:
        raise MeshError("Empty GLB download.")
    dest.write_bytes(resp.content)


async def generate_mesh_glb(
    prompt: str,
    output_dir: Path,
    api_key: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """
    Preview + refine text-to-3D, then save GLB next to CadQuery output.

    Returns {ok, model_id, glb_path, exec_ms, textured} or {ok: False, error}.
    """
    t0 = time.perf_counter()
    if not api_key:
        return {"ok": False, "error": "MESHY_API_KEY is not set.", "error_type": "config"}
    if not (prompt or "").strip():
        return {"ok": False, "error": "Missing mesh prompt.", "error_type": "validation"}

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    model_id = uuid.uuid4().hex[:12]
    dest = output_dir / f"{model_id}.glb"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            preview_id = await _create_task(
                client,
                api_key,
                {
                    "mode": "preview",
                    "prompt": prompt.strip(),
                    "art_style": "realistic",
                    "should_remesh": True,
                    "target_formats": ["glb"],
                },
            )
            logger.info("Meshy preview task %s", preview_id)
            preview = await _poll_task(client, api_key, preview_id, deadline)
            glb_url = (preview.get("model_urls") or {}).get("glb")
            textured = False

            remaining = deadline - time.monotonic()
            if remaining > 12:
                try:
                    refine_id = await _create_task(
                        client,
                        api_key,
                        {
                            "mode": "refine",
                            "preview_task_id": preview_id,
                            "enable_pbr": True,
                            "target_formats": ["glb"],
                        },
                    )
                    logger.info("Meshy refine task %s", refine_id)
                    refined = await _poll_task(client, api_key, refine_id, deadline)
                    refined_url = (refined.get("model_urls") or {}).get("glb")
                    if refined_url:
                        glb_url = refined_url
                        textured = True
                except MeshError as exc:
                    logger.warning("Meshy refine skipped (%s); using preview GLB", exc)

            if not glb_url:
                raise MeshError("Meshy succeeded without a GLB URL.")
            await _download_glb(client, glb_url, dest)
    except MeshError as exc:
        logger.warning("Meshy failed: %s", exc)
        return {
            "ok": False,
            "error": str(exc),
            "error_type": "mesh",
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }
    except Exception as exc:
        logger.exception("Meshy unexpected error")
        return {
            "ok": False,
            "error": str(exc),
            "error_type": "mesh",
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }

    return {
        "ok": True,
        "model_id": model_id,
        "glb_path": str(dest),
        "exec_ms": (time.perf_counter() - t0) * 1000,
        "textured": textured,
    }
