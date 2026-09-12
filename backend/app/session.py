"""In-memory session store for the active CAD model."""

from __future__ import annotations

from app.models import SessionState

_sessions: dict[str, SessionState] = {}


def get_session(session_id: str = "default") -> SessionState:
    if session_id not in _sessions:
        _sessions[session_id] = SessionState(session_id=session_id)
    return _sessions[session_id]


def save_session(state: SessionState) -> SessionState:
    _sessions[state.session_id] = state
    return state
