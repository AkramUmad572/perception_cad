"""Apply structured intents: rebuild CAD or update material only."""

from __future__ import annotations

import time
from typing import Any

from app.config import Settings
from app.models import CommandResponse, Intent, SessionState
from app.session import save_session
from cad.builder import build_model, merge_params
from voice.speech import synthesize_speech


def _glb_url(settings: Settings, model_id: str) -> str:
    # Relative path so Quest Browser (via Vite HTTPS proxy) can fetch same-origin.
    return f"/media/glb/{model_id}.glb"


async def apply_intent(
    intent: Intent,
    session: SessionState,
    settings: Settings,
    transcript: str | None = None,
    extra_latency: dict[str, float] | None = None,
) -> CommandResponse:
    latency: dict[str, float] = dict(extra_latency or {})
    rebuilt = False
    action = intent.action

    if action == "create":
        template = intent.template or "ring"
        params = merge_params(template, intent.params)
        color = str(params.get("color", "#C0C0C0"))
        model_id, _, build_ms = build_model(template, params, settings)
        latency["cad_ms"] = build_ms
        session.template = template  # type: ignore[assignment]
        session.params = params
        session.color = color
        session.model_id = model_id
        session.glb_url = _glb_url(settings, model_id)
        rebuilt = True
        save_session(session)

    elif action == "modify":
        if not session.template:
            intent.reply = "No model yet. Say build me a ring first."
            action = "clarify"
        else:
            params = merge_params(session.template, {**session.params, **intent.params})
            params["color"] = session.color
            model_id, _, build_ms = build_model(session.template, params, settings)
            latency["cad_ms"] = build_ms
            session.params = params
            session.model_id = model_id
            session.glb_url = _glb_url(settings, model_id)
            rebuilt = True
            save_session(session)

    elif action == "set_material":
        color = str(intent.params.get("color", session.color))
        session.color = color
        session.params["color"] = color
        # Fast path: no CAD rebuild — client applies color locally.
        # Optionally rebuild so GLB embeds color for reloads.
        if session.template and intent.params.get("rebuild", False):
            params = merge_params(session.template, session.params)
            model_id, _, build_ms = build_model(session.template, params, settings)
            latency["cad_ms"] = build_ms
            session.model_id = model_id
            session.glb_url = _glb_url(settings, model_id)
            rebuilt = True
        save_session(session)

    # TTS after model work so geometry never waits on voice
    t0 = time.perf_counter()
    audio_url, tts_ms = await synthesize_speech(intent.reply, settings)
    latency["tts_ms"] = tts_ms if tts_ms else (time.perf_counter() - t0) * 1000

    return CommandResponse(
        ok=True,
        transcript=transcript,
        reply=intent.reply,
        action=action,
        rebuilt=rebuilt,
        color=session.color,
        glb_url=session.glb_url,
        reply_audio_url=audio_url,
        session=session,
        latency_ms=latency,
    )
