"""NVIDIA NIM TRELLIS text-to-3D. Free rate-limited key from build.nvidia.com."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from mesh.meshy import MeshError, _download_glb

logger = logging.getLogger(__name__)

NVIDIA_TRELLIS = "https://ai.api.nvidia.com/v1/genai/microsoft/trellis"
NVIDIA_STATUS = "https://api.nvcf.nvidia.com/v2/nvcf/pexec/status/{req_id}"
PROMPT_MAX = 77


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "NVCF-POLL-SECONDS": "120",
    }


def _req_id(resp: httpx.Response) -> str | None:
    for key in ("nvcf-reqid", "NVCF-REQID", "nvcf-request-id"):
        val = resp.headers.get(key)
        if val:
            return val
    return None


def _strip_data_url(raw: str) -> str:
    if "," in raw and raw.strip().startswith("data:"):
        return raw.split(",", 1)[1]
    return raw


def _write_base64_glb(raw: str, dest: Path) -> None:
    blob = base64.b64decode(_strip_data_url(raw), validate=False)
    if len(blob) < 12:
        raise MeshError("NVIDIA returned an empty GLB.")
    dest.write_bytes(blob)


def _extract_glb_payload(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (base64, url) if present."""
    artifacts = data.get("artifacts")
    if isinstance(artifacts, list):
        for art in artifacts:
            if not isinstance(art, dict):
                continue
            b64 = art.get("base64") or art.get("b64_json")
            if isinstance(b64, str) and b64:
                return b64, None
            url = art.get("url")
            if isinstance(url, str) and url.startswith("http"):
                return None, url
    for key in ("glb", "model", "artifact", "output"):
        val = data.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return None, val
        if isinstance(val, str) and len(val) > 80:
            return val, None
        if isinstance(val, dict):
            b64, url = _extract_glb_payload(val)
            if b64 or url:
                return b64, url
    url = data.get("url") or data.get("glb_url")
    if isinstance(url, str) and url.startswith("http"):
        return None, url
    return None, None


async def _poll_nvcf(
    client: httpx.AsyncClient,
    api_key: str,
    req_id: str,
    deadline: float,
) -> dict[str, Any] | bytes:
    url = NVIDIA_STATUS.format(req_id=req_id)
    headers = _headers(api_key)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MeshError("NVIDIA TRELLIS timed out while generating.")
        resp = await client.get(url, headers=headers)
        if resp.status_code == 202:
            await asyncio.sleep(min(3.0, max(remaining, 0.5)))
            continue
        if resp.status_code in (401, 403):
            raise MeshError("NVIDIA rejected the API key.")
        if resp.status_code == 402:
            raise MeshError("NVIDIA credits expired.")
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").lower()
        if "json" not in ctype and resp.content[:4] == b"glTF":
            return resp.content
        return resp.json()


async def generate_nvidia_glb(
    prompt: str,
    dest: Path,
    api_key: str,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    if not api_key:
        raise MeshError("NVIDIA_API_KEY is not set.")
    clipped = prompt.strip()[:PROMPT_MAX]
    deadline = time.monotonic() + timeout_s
    payload = {
        "mode": "text",
        "prompt": clipped,
        "output_format": "glb",
        "no_texture": False,
        "samples": 1,
        "seed": 0,
        "slat_cfg_scale": 3,
        "ss_cfg_scale": 7.5,
        "slat_sampling_steps": 25,
        "ss_sampling_steps": 25,
    }
    async with httpx.AsyncClient(timeout=130.0, follow_redirects=True) as client:
        resp = await client.post(NVIDIA_TRELLIS, headers=_headers(api_key), json=payload)
        if resp.status_code in (401, 403):
            raise MeshError("NVIDIA rejected the API key. Get a free one at https://build.nvidia.com/settings/api-key")
        if resp.status_code == 402:
            raise MeshError("NVIDIA credits expired.")
        if resp.status_code == 202:
            req = _req_id(resp)
            if not req:
                raise MeshError("NVIDIA returned 202 without a request id.")
            logger.info("NVIDIA TRELLIS polling %s", req)
            body = await _poll_nvcf(client, api_key, req, deadline)
            if isinstance(body, bytes):
                dest.write_bytes(body)
                return {"ok": True, "textured": True, "provider": "nvidia"}
            data = body
        else:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise MeshError(f"NVIDIA TRELLIS failed ({resp.status_code}): {resp.text[:240]}") from exc
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype and resp.content[:4] == b"glTF":
                dest.write_bytes(resp.content)
                return {"ok": True, "textured": True, "provider": "nvidia"}
            data = resp.json()

        b64, url = _extract_glb_payload(data if isinstance(data, dict) else {})
        if b64:
            _write_base64_glb(b64, dest)
        elif url:
            await _download_glb(client, url, dest)
        else:
            raise MeshError("NVIDIA TRELLIS succeeded without a GLB payload.")
    return {"ok": True, "textured": True, "provider": "nvidia"}
