"""Detect a 'find this in my photos' request and pick matching files."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_PHOTO_SOURCE_RE = re.compile(
    r"\b(?:from|in|on)\s+(?:my\s+)?(?:google\s+)?(?:photos?|files?|drive|folder)\b"
    r"|\b(?:google\s+)?(?:photos?|drive)\b"
    r"|\bmy\s+files\b",
    re.IGNORECASE,
)
_PHOTO_VERB_RE = re.compile(
    r"\b(?:find|get|pull|show|grab|fetch|look\s+up)\b.+"
    r"\b(?:photo|image|picture|pic|pics)\b"
    r"|\b(?:photo|image|picture|pic)\b.+\b(?:from|in)\b",
    re.IGNORECASE,
)
_FILLER_RE = re.compile(
    r"^(?:hey\s+percy[,.\s]*)?(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:find|get|pull|show|grab|fetch|look\s+up)\s+"
    r"(?:me\s+)?(?:this|the|that)?\s*"
    r"(?:image|photo|picture|pic)?\s*"
    r"(?:of\s+)?",
    re.IGNORECASE,
)
_TAIL_RE = re.compile(
    r"\b(?:that\s+)?(?:i\s+)?(?:wanna|want\s+to|need\s+to)?\s*"
    r"(?:3d\s*print(?:ed|able|ing)?|print|sculpt|build|make)\b.*$",
    re.IGNORECASE,
)
_SOURCE_STRIP_RE = re.compile(
    r"\b(?:from|in|on)\s+(?:my\s+)?(?:google\s+)?(?:photos?|files?|drive|folder)\b"
    r"|\b(?:google\s+)?(?:photos?|drive)\b",
    re.IGNORECASE,
)


def is_photo_search(text: str) -> bool:
    """True when the user is asking to pull a picture from Drive, not sculpt from words."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_PHOTO_SOURCE_RE.search(t) or _PHOTO_VERB_RE.search(t))


def photo_query(text: str) -> str:
    """What's in the picture, with the 'from my photos / 3D print' wrapping stripped."""
    t = (text or "").strip()
    t = _FILLER_RE.sub("", t)
    t = _SOURCE_STRIP_RE.sub(" ", t)
    t = _TAIL_RE.sub("", t)
    t = re.sub(r"\b(?:this|that|the|of|please)\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip(" .,!?:;")
    return t or (text or "").strip()


def _name_matches(name: str, query: str) -> bool:
    tokens = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if len(w) > 2]
    if not tokens:
        return False
    hay = name.lower()
    return all(tok in hay for tok in tokens) or any(len(tok) >= 5 and tok in hay for tok in tokens)


async def rank_matches(
    query: str,
    files: list[dict[str, Any]],
    settings: Settings,
    thumbnails: dict[str, bytes] | None = None,
) -> list[str]:
    """
    Return matching Drive file ids, best first.

    Filename is enough for a tiny demo folder. Gemini looks at the pixels when
    the names don't decide it.
    """
    if not files:
        return []
    if len(files) == 1:
        return [files[0]["id"]]

    named = [f["id"] for f in files if _name_matches(f.get("name") or "", query)]
    if named:
        return named

    if not settings.gemini_api_key or not thumbnails:
        return [f["id"] for f in files]

    listing = "\n".join(f"- {f['id']}: {f['name']}" for f in files)
    parts: list[dict[str, Any]] = [
        {
            "text": (
                "Which of these photos match this request?\n"
                f"Request: {query}\n"
                f"Files:\n{listing}\n"
                'Reply JSON only: {"ids":["fileId",...]} — matching ids, best first. '
                "Empty list if none match."
            )
        }
    ]
    for f in files:
        raw = thumbnails.get(f["id"])
        if not raw:
            continue
        import base64

        b64 = base64.b64encode(raw).decode("ascii")
        mime = f.get("mime") or "image/jpeg"
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            mime = "image/jpeg"
        parts.append({"text": f"file {f['id']} ({f['name']}):"})
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(
                url,
                params={"key": settings.gemini_api_key},
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        raw = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "{}")
        )
        parsed = json.loads(raw)
        ids = [i for i in (parsed.get("ids") or []) if isinstance(i, str)]
        known = {f["id"] for f in files}
        return [i for i in ids if i in known] or [f["id"] for f in files]
    except Exception as exc:
        logger.warning("Photo match via Gemini failed: %s", exc)
        return [f["id"] for f in files]


async def find_photos(
    query: str,
    files: list[dict[str, Any]],
    settings: Settings,
    thumbnails: dict[str, bytes] | None = None,
) -> list[dict[str, Any]]:
    ids = await rank_matches(query, files, settings, thumbnails)
    by_id = {f["id"]: f for f in files}
    return [by_id[i] for i in ids if i in by_id]
