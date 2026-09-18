"""List and download images from a public Google Drive folder."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/heic")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0))


async def list_images(settings: Settings) -> list[dict[str, Any]]:
    """Image files in the configured public folder."""
    folder = (settings.google_drive_folder_id or "").strip()
    key = (settings.google_drive_api_key or "").strip()
    if not folder or not key:
        raise RuntimeError("Drive isn't configured. Set GOOGLE_DRIVE_API_KEY and GOOGLE_DRIVE_FOLDER_ID.")

    query = f"'{folder}' in parents and trashed = false"
    params = {
        "q": query,
        "fields": "files(id,name,mimeType,thumbnailLink)",
        "pageSize": 20,
        "key": key,
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    async with _client() as client:
        resp = await client.get(DRIVE_FILES, params=params)
        if resp.status_code >= 400:
            raise RuntimeError(f"Drive list failed ({resp.status_code}): {resp.text[:240]}")
        data = resp.json()

    out: list[dict[str, Any]] = []
    for f in data.get("files") or []:
        mime = (f.get("mimeType") or "").lower()
        name = f.get("name") or ""
        if mime.startswith("image/") or name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            out.append(
                {
                    "id": f.get("id"),
                    "name": name,
                    "mime": mime or "image/jpeg",
                }
            )
    return [item for item in out if item.get("id")]


async def download_file(file_id: str, settings: Settings) -> tuple[bytes, str]:
    """Return (bytes, mime)."""
    key = (settings.google_drive_api_key or "").strip()
    async with _client() as client:
        resp = await client.get(
            f"{DRIVE_FILES}/{file_id}",
            params={"alt": "media", "key": key, "supportsAllDrives": "true"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Drive download failed ({resp.status_code}): {resp.text[:200]}")
        mime = resp.headers.get("content-type") or "image/jpeg"
        if ";" in mime:
            mime = mime.split(";", 1)[0].strip()
        return resp.content, mime
