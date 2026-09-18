"""Session store for the active CAD model.

Backed by a file so a backend restart doesn't drop whatever the user is
looking at — the GLB is already on disk, only the pointer to it was fragile.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

from app.config import STORAGE
from app.models import SessionState

logger = logging.getLogger(__name__)

SESSION_FILE = STORAGE / "sessions.json"

_sessions: dict[str, SessionState] = {}
_loaded = False


def _load() -> None:
    """Read sessions from disk once, on first access."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not SESSION_FILE.exists():
        return
    try:
        raw = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        for sid, data in raw.items():
            try:
                _sessions[sid] = SessionState.model_validate(data)
            except Exception as exc:
                logger.warning("Dropping unreadable session %s: %s", sid, exc)
    except Exception as exc:
        # A corrupt file must not take the backend down; start clean instead.
        logger.warning("Could not read %s: %s", SESSION_FILE.name, exc)


def _flush() -> None:
    """Write via a temp file so a crash mid-write can't corrupt the store."""
    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {sid: s.model_dump() for sid, s in _sessions.items()}
        fd, tmp = tempfile.mkstemp(dir=str(SESSION_FILE.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, SESSION_FILE)
    except Exception as exc:
        logger.warning("Could not persist sessions: %s", exc)


def get_session(session_id: str = "default") -> SessionState:
    _load()
    if session_id not in _sessions:
        _sessions[session_id] = SessionState(session_id=session_id)
    return _sessions[session_id]


def save_session(state: SessionState) -> SessionState:
    _load()
    _sessions[state.session_id] = state
    _flush()
    return state


def clear_session(session_id: str = "default") -> None:
    """Clear a session (useful for testing)."""
    _load()
    if session_id in _sessions:
        del _sessions[session_id]
        _flush()
