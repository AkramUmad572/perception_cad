"""Apply structured intents: rebuild CAD, update material, or execute freeform scripts."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import Settings
from app.models import CommandResponse, Intent, SessionState
from app.session import save_session
from cad.builder import build_model, build_from_script, merge_params, DEFAULTS
from voice.speech import synthesize_speech

logger = logging.getLogger(__name__)


def _glb_url(settings: Settings, model_id: str) -> str:
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
    error_msg = None

    if action == "create":
        template = intent.template or "ring"
        if template not in DEFAULTS:
            intent.reply = f"Unknown shape '{template}'. Try ring, box, or cylinder."
            action = "clarify"
        else:
            params = merge_params(template, intent.params)
            color = str(params.get("color", "#C0C0C0"))
            try:
                model_id, _, build_ms = build_model(template, params, settings)
                latency["cad_ms"] = build_ms
                session.template = template  # type: ignore[assignment]
                session.params = params
                session.color = color
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                session.last_script = None
                rebuilt = True
                save_session(session)
            except Exception as e:
                logger.exception("Build failed: %s", e)
                error_msg = str(e)
                intent.reply = f"Build failed: {e}"
                action = "clarify"

    elif action == "modify":
        if not session.template:
            intent.reply = "No model yet. Say build me a ring first."
            action = "clarify"
        else:
            params = merge_params(session.template, {**session.params, **intent.params})
            params["color"] = session.color
            try:
                model_id, _, build_ms = build_model(session.template, params, settings)
                latency["cad_ms"] = build_ms
                session.params = params
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                rebuilt = True
                save_session(session)
            except Exception as e:
                logger.exception("Modify failed: %s", e)
                error_msg = str(e)
                intent.reply = f"Modification failed: {e}"
                action = "clarify"

    elif action == "execute_script":
        if not intent.script:
            intent.reply = "No script provided."
            action = "clarify"
        else:
            try:
                color = session.color or "#C0C0C0"
                model_id, _, build_ms = build_from_script(
                    intent.script,
                    settings,
                    color=color,
                    timeout=30.0,
                )
                latency["cad_ms"] = build_ms
                session.template = None
                session.params = {}
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                session.last_script = intent.script
                rebuilt = True
                save_session(session)
            except Exception as e:
                logger.exception("Script execution failed: %s", e)
                error_msg = str(e)
                intent.reply = f"Script failed: {e}"
                action = "clarify"

    elif action == "set_material":
        color = str(intent.params.get("color", session.color))
        session.color = color
        session.params["color"] = color
        if session.template and intent.params.get("rebuild", False):
            try:
                params = merge_params(session.template, session.params)
                model_id, _, build_ms = build_model(session.template, params, settings)
                latency["cad_ms"] = build_ms
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                rebuilt = True
            except Exception as e:
                logger.warning("Rebuild for color failed: %s", e)
        save_session(session)

    t0 = time.perf_counter()
    audio_url, tts_ms = await synthesize_speech(intent.reply, settings)
    latency["tts_ms"] = tts_ms if tts_ms else (time.perf_counter() - t0) * 1000

    return CommandResponse(
        ok=error_msg is None,
        transcript=transcript,
        reply=intent.reply,
        action=action,
        rebuilt=rebuilt,
        color=session.color,
        glb_url=session.glb_url,
        reply_audio_url=audio_url,
        session=session,
        latency_ms=latency,
        error=error_msg,
    )


async def execute_script_direct(
    script: str,
    session: SessionState,
    settings: Settings,
    color: str = "#C0C0C0",
) -> CommandResponse:
    """
    Direct script execution endpoint - bypasses intent parsing.
    Used when Taha's codegen provides CadQuery scripts directly.
    """
    latency: dict[str, float] = {}
    error_msg = None

    try:
        model_id, _, build_ms = build_from_script(
            script,
            settings,
            color=color,
            timeout=30.0,
        )
        latency["cad_ms"] = build_ms
        session.template = None
        session.params = {}
        session.color = color
        session.model_id = model_id
        session.glb_url = _glb_url(settings, model_id)
        session.last_script = script
        save_session(session)

        return CommandResponse(
            ok=True,
            transcript=None,
            reply="Model built from script.",
            action="execute_script",
            rebuilt=True,
            color=color,
            glb_url=session.glb_url,
            reply_audio_url=None,
            session=session,
            latency_ms=latency,
        )
    except Exception as e:
        logger.exception("Direct script execution failed: %s", e)
        error_msg = str(e)
        return CommandResponse(
            ok=False,
            transcript=None,
            reply=f"Script execution failed: {e}",
            action="clarify",
            rebuilt=False,
            color=session.color,
            glb_url=session.glb_url,
            reply_audio_url=None,
            session=session,
            latency_ms=latency,
            error=error_msg,
        )
