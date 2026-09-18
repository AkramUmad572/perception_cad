"""three.ws keyless draft text-to-3D (TRELLIS-backed). No API key."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from mesh.meshy import MeshError, _download_glb

logger = logging.getLogger(__name__)

THREE_WS_GENERATE = "https://three.ws/api/3d/generate"
THREE_WS_FORGE = "https://three.ws/api/forge"
THREE_WS_ORIGIN = "https://three.ws"
POLL_INTERVAL_S = 2.0
MAX_POLL_INTERVAL_S = 3.0
BUSY_RETRY_S = 15.0
IMAGE_QUALITIES = ("low", "draft", "medium", "standard", "high", "ultra")
# `draft` reconstructs a photo as a paper-thin billboard; `high` gives a solid.
DEFAULT_IMAGE_QUALITY = "high"

# TRELLIS renders a reference image first, so a scene-like prompt gets a scene —
# base, ground, shadow and all. Ask for an isolated subject instead.
PROMPT_SUFFIX = (
    ", single isolated object, full body, centered, standing upright, "
    "no base, no pedestal, no ground plane, plain white background"
)


def _shape_prompt(prompt: str) -> str:
    text = prompt.strip()
    return text if "no ground plane" in text.lower() else text + PROMPT_SUFFIX


def _glb_url_from(data: dict[str, Any], depth: int = 0) -> str | None:
    if not isinstance(data, dict) or depth > 4:
        return None
    for key in ("glb_url", "glbUrl", "model_url", "modelUrl"):
        val = data.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    url = data.get("url")
    if isinstance(url, str) and url.startswith("http") and ".glb" in url.lower():
        return url
    for key in ("output", "result", "data"):
        nested = data.get(key)
        if isinstance(nested, str) and nested.startswith("http"):
            return nested
        found = _glb_url_from(nested, depth + 1) if isinstance(nested, dict) else None
        if found:
            return found
    return None


def _job_id(data: dict[str, Any]) -> str | None:
    for key in ("job", "job_id", "jobId", "id"):
        val = data.get(key)
        if isinstance(val, str) and val and val not in ("done", "pending", "failed"):
            return val
    return None


def _poll_url(data: dict[str, Any], job: str | None, base: str) -> str | None:
    """Poll the endpoint that created the job; /forge jobs 404 on /3d/generate."""
    poll = data.get("poll") or data.get("poll_url") or data.get("pollUrl")
    if isinstance(poll, str) and poll:
        return urljoin(THREE_WS_ORIGIN + "/", poll.lstrip("/")) if poll.startswith("/") else poll
    if job:
        # Forge job ids are signed tokens with characters that need escaping.
        return f"{base}?job={quote(job, safe='')}"
    return None


def _is_done(data: dict[str, Any]) -> bool:
    status = str(data.get("status") or "").lower()
    return status in ("done", "succeeded", "success", "complete", "completed") or bool(
        _glb_url_from(data)
    )


def _is_failed(data: dict[str, Any]) -> bool:
    status = str(data.get("status") or "").lower()
    return status in ("failed", "error", "cancelled", "canceled")


async def _wait_for_glb(
    client: httpx.AsyncClient,
    data: dict[str, Any],
    deadline: float,
    base: str,
) -> str:
    if _is_failed(data):
        raise MeshError(str(data.get("error") or "three.ws generation failed."))
    glb = _glb_url_from(data)
    if glb:
        return glb
    job = _job_id(data)
    poll = _poll_url(data, job, base)
    if not poll:
        raise MeshError("three.ws did not return a GLB or job id.")
    logger.info("three.ws polling %s", poll.split("?")[0])

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MeshError("three.ws timed out while generating.")
        wait = data.get("retryAfter") or data.get("retry_after") or POLL_INTERVAL_S
        try:
            wait_s = float(wait)
        except (TypeError, ValueError):
            wait_s = POLL_INTERVAL_S
        wait_s = min(max(wait_s, 1.0), MAX_POLL_INTERVAL_S)
        await asyncio.sleep(min(wait_s, max(remaining, 0.5)))
        resp = await client.get(poll, follow_redirects=True)
        if resp.status_code == 429:
            continue
        resp.raise_for_status()
        data = resp.json()
        if _is_failed(data):
            raise MeshError(str(data.get("error") or "three.ws generation failed."))
        glb = _glb_url_from(data)
        if glb:
            return glb
        if _is_done(data) and not glb:
            raise MeshError("three.ws finished without a GLB URL.")


async def generate_three_ws_glb(
    prompt: str,
    dest: Path,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    shaped = _shape_prompt(prompt)
    body = {"prompt": shaped}
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
        base = THREE_WS_GENERATE
        resp = await client.post(THREE_WS_GENERATE, headers=headers, json=body)
        if resp.status_code >= 400:
            logger.warning("three.ws /generate %s: %s", resp.status_code, resp.text[:240])
            base = THREE_WS_FORGE
            resp = await client.post(
                THREE_WS_FORGE,
                headers=headers,
                json={"prompt": shaped, "tier": "draft"},
            )
        if resp.status_code == 429:
            raise MeshError("three.ws rate limit reached; wait a minute and try again.")
        if resp.status_code >= 400:
            raise MeshError(f"three.ws failed ({resp.status_code}): {resp.text[:240]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise MeshError(f"three.ws returned non-JSON object: {type(data)}")
        logger.info("three.ws status=%s keys=%s", data.get("status"), list(data)[:12])
        glb_url = await _wait_for_glb(client, data, deadline, base)
        await _download_glb(client, glb_url, dest)
    return {"ok": True, "textured": True, "provider": "three_ws"}


async def generate_three_ws_glb_from_image(
    image_url: str,
    dest: Path,
    prompt: str | None = None,
    timeout_s: float = 150.0,
    quality: str = DEFAULT_IMAGE_QUALITY,
) -> dict[str, Any]:
    """
    Image-to-3D. Only /api/forge takes `image_url`, and it must be a URL three.ws
    can fetch — data URLs are rejected and inline base64 exceeds the body limit.
    """
    if not (image_url or "").startswith(("http://", "https://")):
        raise MeshError("three.ws needs a public http(s) image URL.")
    if quality not in IMAGE_QUALITIES:
        quality = DEFAULT_IMAGE_QUALITY

    deadline = time.monotonic() + timeout_s
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    body: dict[str, Any] = {"image_url": image_url, "quality": quality}
    if (prompt or "").strip():
        body["prompt"] = prompt.strip()

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
        # The free image lane runs on shared Spaces and answers 502 provider_busy
        # while they warm up. That clears on its own within a minute or two.
        resp = await client.post(THREE_WS_FORGE, headers=headers, json=body)
        while resp.status_code in (502, 503) and time.monotonic() < deadline - 25:
            logger.info("three.ws image lane busy; retrying in %.0fs", BUSY_RETRY_S)
            await asyncio.sleep(BUSY_RETRY_S)
            resp = await client.post(THREE_WS_FORGE, headers=headers, json=body)

        if resp.status_code == 429:
            raise MeshError("three.ws rate limit reached; wait a minute and try again.")
        if resp.status_code in (502, 503):
            raise MeshError(
                "three.ws free image engines are all busy. Try again in a minute."
            )
        if resp.status_code >= 400:
            raise MeshError(
                f"three.ws image-to-3D failed ({resp.status_code}): {resp.text[:240]}"
            )
        data = resp.json()
        if not isinstance(data, dict):
            raise MeshError(f"three.ws returned non-JSON object: {type(data)}")
        logger.info("three.ws image job status=%s keys=%s", data.get("status"), list(data)[:12])
        glb_url = await _wait_for_glb(client, data, deadline, THREE_WS_FORGE)
        await _download_glb(client, glb_url, dest)
    return {"ok": True, "textured": True, "provider": "three_ws_image"}
