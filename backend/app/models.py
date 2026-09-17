"""Shared request/response and session models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TemplateName = Literal["ring", "box", "cylinder"]
ActionName = Literal["create", "modify", "set_material", "execute_script", "noop", "clarify"]


class CadParams(BaseModel):
    inner_diameter_mm: float | None = None
    outer_diameter_mm: float | None = None
    height_mm: float | None = None
    width_mm: float | None = None
    depth_mm: float | None = None
    diameter_mm: float | None = None
    color: str | None = None


class Intent(BaseModel):
    action: ActionName = "noop"
    template: TemplateName | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    script: str | None = None
    reply: str = "Okay."


class CommandRequest(BaseModel):
    text: str
    session_id: str = "default"


class ScriptRequest(BaseModel):
    """Direct CadQuery script execution request."""
    script: str
    session_id: str = "default"
    color: str = "#C0C0C0"


class SessionState(BaseModel):
    session_id: str = "default"
    template: TemplateName | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    color: str = "#C0C0C0"
    model_id: str | None = None
    glb_url: str | None = None
    last_script: str | None = None


class CommandResponse(BaseModel):
    ok: bool = True
    transcript: str | None = None
    reply: str
    action: ActionName
    rebuilt: bool = False
    color: str | None = None
    glb_url: str | None = None
    reply_audio_url: str | None = None
    session: SessionState
    latency_ms: dict[str, float] = Field(default_factory=dict)
    error: str | None = None
