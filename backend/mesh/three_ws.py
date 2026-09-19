"""three.ws keyless draft text-to-3D (TRELLIS-backed). No API key."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from mesh.meshy import MeshBusyError, MeshError, _download_glb

logger = logging.getLogger(__name__)

THREE_WS_GENERATE = "https://three.ws/api/3d/generate"
THREE_WS_FORGE = "https://three.ws/api/forge"
THREE_WS_ORIGIN = "https://three.ws"
POLL_INTERVAL_S = 2.0
MAX_POLL_INTERVAL_S = 3.0
BUSY_RETRY_S = 15.0
# Waiting for a free Space is not generation time, so it gets its own clock.
# Sharing one deadline let a busy spell eat the sculpt: by the time a slot
# opened there was no budget left to use it. Kept short because a caller with
# a CAD fallback is better served by failing over than by queueing.
ACCEPT_TIMEOUT_S = 20.0
# A job that is taken but never leaves the queue is the provider's failure
# mode when its Spaces are sick; waiting out the full budget only delays the
# fallback, so treat a motionless queue as a busy signal.
QUEUE_STALL_S = 75.0
# Self-host TRELLIS often sits in `queued` the whole time its weights load.
# Fail that lane and try the next free engine instead of blocking the headset.
SELFHOST_STALL_S = 45.0
HF_STALL_S = 180.0
QUEUED_STATES = ("queued", "pending", "waiting", "")
IMAGE_QUALITIES = ("low", "draft", "medium", "standard", "high", "ultra")
# `high` routes to Hunyuan on three.ws, which is the lane that goes down first.
# Draft/standard stay on TRELLIS (self-host or HF Spaces).
DEFAULT_IMAGE_QUALITY = "draft"
TIER_BY_QUALITY = {
    "low": "draft",
    "draft": "draft",
    "medium": "standard",
    "standard": "standard",
    "high": "standard",
    "ultra": "standard",
}
# Free photo engines, preferred first. Paid/BYOK lanes are never chosen here.
FREE_IMAGE_BACKENDS = ("huggingface", "trellis_selfhost", "hunyuan3d")

# TRELLIS renders a reference image first, so a scene-like prompt gets a scene —
# base, ground, shadow and all. Ask for an isolated subject instead.
PROMPT_SUFFIX = (
    ", single isolated object, full body, centered, standing upright, "
    "no base, no pedestal, no ground plane, plain white background"
)


def _shape_prompt(prompt: str) -> str:
    text = prompt.strip()
    return text if "no ground plane" in text.lower() else text + PROMPT_SUFFIX


def _tier_for_quality(quality: str) -> str:
    return TIER_BY_QUALITY.get((quality or "").strip().lower(), "draft")


def _backend_status(health: dict[str, Any], name: str) -> str:
    backends = health.get("backends") if isinstance(health, dict) else None
    info = backends.get(name) if isinstance(backends, dict) else None
    if not isinstance(info, dict):
        return ""
    return str(info.get("status") or "").lower()


def pick_image_backends(health: dict[str, Any] | None, tier: str = "draft") -> list[str]:
    """
    Free photo engines that are not confirmed down.

    `high` used to pin Hunyuan; when that worker is down we still try HF / TRELLIS.
    """
    health = health if isinstance(health, dict) else {}
    preferred = list(FREE_IMAGE_BACKENDS)
    if (tier or "").lower() == "high":
        preferred = ("hunyuan3d", "huggingface", "trellis_selfhost")

    ok: list[str] = []
    degraded: list[str] = []
    unknown: list[str] = []
    skipped_down: list[str] = []
    for name in preferred:
        status = _backend_status(health, name)
        if status == "down":
            skipped_down.append(name)
            continue
        if status == "ok":
            ok.append(name)
        elif status == "degraded":
            degraded.append(name)
        else:
            unknown.append(name)
    chosen = ok + degraded + unknown
    # Health flips self-host between "degraded" (loading) and "down" while
    # weights load. A queued job is how that worker becomes ready, so always
    # give it a turn after the healthy lanes.
    if "trellis_selfhost" not in chosen:
        chosen.append("trellis_selfhost")
    return chosen or list(FREE_IMAGE_BACKENDS)


def _image_forge_body(
    image_url: str,
    *,
    tier: str,
    backend: str,
    prompt: str | None = None,
) -> dict[str, Any]:
    # Official Forge contract is image_urls[] + tier + backend. Keep image_url
    # too so an older router still sees the photo.
    body: dict[str, Any] = {
        "image_urls": [image_url],
        "image_url": image_url,
        "tier": tier,
        "path": "image",
        "backend": backend,
    }
    if (prompt or "").strip():
        body["prompt"] = prompt.strip()
    return body


def _queue_stall_s(backend: str) -> float:
    if backend == "huggingface":
        return HF_STALL_S
    if backend in ("trellis_selfhost", "hunyuan3d"):
        return SELFHOST_STALL_S
    return QUEUE_STALL_S


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
    stall_s: float | None = None,
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
    status = str(data.get("status") or "")
    started = time.monotonic()

    while True:
        if (
            stall_s is not None
            and status.lower() in QUEUED_STATES
            and time.monotonic() - started > stall_s
        ):
            raise MeshBusyError(
                f"three.ws took the job but left it {status or 'queued'} "
                f"for {stall_s:.0f}s."
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Which stage it died in decides whether this is worth retrying:
            # stuck in `queued` means the provider never picked the job up.
            raise MeshError(
                f"three.ws timed out while generating (last status: {status or 'unknown'})."
            )
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
        if str(data.get("status") or "") != status:
            status = str(data.get("status") or "")
            logger.info("three.ws job → %s", status or "unknown")
        if _is_failed(data):
            raise MeshError(str(data.get("error") or "three.ws generation failed."))
        glb = _glb_url_from(data)
        if glb:
            return glb
        if _is_done(data) and not glb:
            raise MeshError("three.ws finished without a GLB URL.")


def _retry_after_s(resp: httpx.Response, default: float = 10.0) -> float:
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("retry_after") is not None:
            return max(float(data["retry_after"]), 1.0)
    except Exception:
        pass
    return default


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
        if resp.status_code == 429:
            wait = _retry_after_s(resp)
            if deadline - time.monotonic() > wait + 2:
                logger.info("three.ws /generate 429; retrying in %.0fs", wait)
                await asyncio.sleep(wait)
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
            raise MeshBusyError(
                "three.ws rate limit reached; wait a minute and try again."
            )
        if resp.status_code >= 400:
            raise MeshError(f"three.ws failed ({resp.status_code}): {resp.text[:240]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise MeshError(f"three.ws returned non-JSON object: {type(data)}")
        logger.info("three.ws status=%s keys=%s", data.get("status"), list(data)[:12])
        try:
            glb_url = await _wait_for_glb(client, data, deadline, base)
        except MeshError as exc:
            if base == THREE_WS_GENERATE and deadline - time.monotonic() > 25:
                logger.warning("three.ws /generate job failed (%s); retrying once", exc)
                resp = await client.post(THREE_WS_GENERATE, headers=headers, json=body)
                if resp.status_code < 400:
                    data = resp.json()
                    if isinstance(data, dict):
                        try:
                            glb_url = await _wait_for_glb(
                                client, data, deadline, THREE_WS_GENERATE
                            )
                            await _download_glb(client, glb_url, dest)
                            return {"ok": True, "textured": True, "provider": "three_ws"}
                        except MeshError:
                            pass
            if base == THREE_WS_FORGE:
                raise
            logger.warning("three.ws /generate job failed (%s); trying forge text", exc)
            base = THREE_WS_FORGE
            resp = await client.post(
                THREE_WS_FORGE,
                headers=headers,
                json={"prompt": shaped, "tier": "draft"},
            )
            if resp.status_code >= 400:
                raise MeshError(
                    f"three.ws forge text failed ({resp.status_code}): {resp.text[:240]}"
                ) from exc
            data = resp.json()
            if not isinstance(data, dict):
                raise MeshError("three.ws forge text returned non-JSON.") from exc
            glb_url = await _wait_for_glb(client, data, deadline, base)
        await _download_glb(client, glb_url, dest)
    return {"ok": True, "textured": True, "provider": "three_ws"}


async def _forge_health(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        resp = await client.get(f"{THREE_WS_FORGE}?health")
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.info("three.ws health unavailable: %s", exc)
        return {}


async def _submit_image_job(
    client: httpx.AsyncClient,
    body: dict[str, Any],
    accept_timeout_s: float,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    accept_deadline = time.monotonic() + accept_timeout_s
    resp = await client.post(THREE_WS_FORGE, headers=headers, json=body)
    while resp.status_code in (502, 503) and time.monotonic() < accept_deadline:
        logger.info(
            "three.ws %s busy; retrying in %.0fs",
            body.get("backend") or "image",
            BUSY_RETRY_S,
        )
        await asyncio.sleep(BUSY_RETRY_S)
        resp = await client.post(THREE_WS_FORGE, headers=headers, json=body)

    if resp.status_code == 429:
        raise MeshBusyError("three.ws rate limit reached; wait a minute and try again.")
    if resp.status_code in (502, 503):
        raise MeshBusyError(
            f"three.ws {body.get('backend') or 'image'} engines are busy."
        )
    if resp.status_code >= 400:
        raise MeshError(
            f"three.ws image-to-3D failed ({resp.status_code}): {resp.text[:240]}"
        )
    data = resp.json()
    if not isinstance(data, dict):
        raise MeshError(f"three.ws returned non-JSON object: {type(data)}")
    return data


async def generate_three_ws_glb_from_image(
    image_url: str,
    dest: Path,
    prompt: str | None = None,
    timeout_s: float = 300.0,
    accept_timeout_s: float = ACCEPT_TIMEOUT_S,
    quality: str = DEFAULT_IMAGE_QUALITY,
) -> dict[str, Any]:
    """
    Image-to-3D. Only /api/forge takes a photo, and it must be a URL three.ws
    can fetch — data URLs are rejected and inline base64 exceeds the body limit.

    Walks the free engines that `/api/forge?health` says are not down. Sending
    `quality=high` used to pin Hunyuan, which is the worker that dies first.
    """
    if not (image_url or "").startswith(("http://", "https://")):
        raise MeshError("three.ws needs a public http(s) image URL.")
    if quality not in IMAGE_QUALITIES:
        quality = DEFAULT_IMAGE_QUALITY
    tier = _tier_for_quality(quality)

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
        health = await _forge_health(client)
        backends = pick_image_backends(health, tier)
        logger.info(
            "three.ws image fleet=%s backends=%s tier=%s",
            health.get("status") or "unknown",
            backends,
            tier,
        )
        errors: list[str] = []
        for backend in backends:
            body = _image_forge_body(
                image_url, tier=tier, backend=backend, prompt=prompt
            )
            try:
                data = await _submit_image_job(client, body, accept_timeout_s)
            except MeshBusyError as exc:
                logger.warning("three.ws %s unavailable: %s", backend, exc)
                errors.append(f"{backend}: {exc}")
                continue
            except MeshError as exc:
                logger.warning("three.ws %s rejected: %s", backend, exc)
                errors.append(f"{backend}: {exc}")
                continue

            logger.info(
                "three.ws image job backend=%s status=%s keys=%s",
                data.get("backend") or backend,
                data.get("status"),
                list(data)[:12],
            )
            deadline = time.monotonic() + timeout_s
            try:
                glb_url = await _wait_for_glb(
                    client,
                    data,
                    deadline,
                    THREE_WS_FORGE,
                    stall_s=_queue_stall_s(backend),
                )
            except MeshBusyError as exc:
                logger.warning("three.ws %s stalled: %s", backend, exc)
                errors.append(f"{backend}: {exc}")
                continue
            except MeshError as exc:
                logger.warning("three.ws %s failed: %s", backend, exc)
                errors.append(f"{backend}: {exc}")
                continue

            await _download_glb(client, glb_url, dest)
            return {
                "ok": True,
                "textured": True,
                "provider": f"three_ws_image:{data.get('backend') or backend}",
            }

    detail = " | ".join(errors) if errors else "no free image engine"
    raise MeshBusyError(f"three.ws photo engines unavailable ({detail}).")
