"""Apply intents: codegen → sandbox execution → retry on failure."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import Settings
from app.models import CommandResponse, Intent, SessionState
from app.session import save_session
from cad import DEFAULTS
from cad.sandbox import execute_cadquery_script
from cad.builder import merge_params, build_model
from voice.speech import synthesize_speech

logger = logging.getLogger(__name__)

MAX_RETRIES = 2

_model_version_counter = 0


def _next_model_version() -> int:
    """Generate a monotonically increasing model version for cache-busting."""
    global _model_version_counter
    _model_version_counter += 1
    return _model_version_counter


def _glb_url(settings: Settings, model_id: str) -> str:
    return f"/media/glb/{model_id}.glb"


async def _execute_with_retry(
    script: str,
    original_text: str,
    session: SessionState,
    settings: Settings,
    latency: dict[str, float],
) -> tuple[bool, str | None, str | None]:
    """
    Execute CadQuery script in sandbox with retry loop on failure.
    
    Uses Tabish's canonical entrypoint: execute_cadquery_script()
    Returns dict: {ok, glb_path, model_id, exec_ms} or {ok=False, error, error_type}
    
    Returns (success, model_id_or_none, error_or_none).
    """
    from ai.intent import repair_and_retry

    color = session.color or "#C0C0C0"
    current_script = script
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        result = execute_cadquery_script(
            script=current_script,
            output_dir=settings.glb_dir,
            timeout=30.0,
            color=color,
        )
        latency[f"cad_ms_attempt_{attempt}"] = result.get("exec_ms", 0)

        if result["ok"]:
            latency["cad_ms"] = result.get("exec_ms", 0)
            session.template = None
            session.params = {}
            session.model_id = result["model_id"]
            session.glb_url = _glb_url(settings, result["model_id"])
            session.last_script = current_script
            save_session(session)
            return True, result["model_id"], None

        last_error = result.get("error", "Unknown error")
        error_type = result.get("error_type", "execution")
        logger.warning("Sandbox exec failed (attempt %d): [%s] %s", attempt + 1, error_type, last_error)

        if error_type == "security":
            return False, None, f"Security violation: {last_error}"

        if attempt < MAX_RETRIES:
            logger.info("Attempting repair via LLM...")
            repair_intent = await repair_and_retry(
                original_text=original_text,
                failed_script=current_script,
                error=last_error,
                settings=settings,
                max_retries=1,
            )
            if repair_intent.action == "generate" and repair_intent.script:
                current_script = repair_intent.script
                logger.info("Retrying with repaired script")
            else:
                break

    return False, None, last_error


async def apply_intent(
    intent: Intent,
    session: SessionState,
    settings: Settings,
    transcript: str | None = None,
    extra_latency: dict[str, float] | None = None,
) -> CommandResponse:
    """
    Apply an Intent to session state.
    
    Handles:
    - generate: Execute CadQuery script via sandbox (with retry loop)
    - set_material: Change color AND ALWAYS rebuild to return fresh model_id/glb_url
    - create/modify: Legacy template path (kept for backward compat)
    - clarify/noop: No CAD action
    
    IMPORTANT: For any action that changes the model (generate, set_material, modify),
    we MUST return a new model_id and glb_url so the client can swap the mesh.
    A "successful" color change with no new asset would be a client-invisible no-op.
    """
    latency: dict[str, float] = dict(extra_latency or {})
    rebuilt = False
    action = intent.action
    error_msg = None
    result_model_id: str | None = None

    if action == "generate":
        if not intent.script:
            intent.reply = "No code was generated."
            action = "clarify"
        else:
            success, model_id, error = await _execute_with_retry(
                script=intent.script,
                original_text=transcript or "",
                session=session,
                settings=settings,
                latency=latency,
            )
            if success:
                rebuilt = True
                result_model_id = model_id
            else:
                error_msg = error
                intent.reply = f"Build failed: {error}"
                action = "clarify"

    elif action == "execute_script":
        if not intent.script:
            intent.reply = "No script provided."
            action = "clarify"
        else:
            success, model_id, error = await _execute_with_retry(
                script=intent.script,
                original_text=transcript or "",
                session=session,
                settings=settings,
                latency=latency,
            )
            if success:
                rebuilt = True
                result_model_id = model_id
            else:
                error_msg = error
                intent.reply = f"Script failed: {error}"
                action = "clarify"

    elif action == "create":
        template = intent.template
        if template and template in DEFAULTS:
            params = merge_params(template, intent.params)
            color = str(params.get("color", session.color or "#C0C0C0"))
            try:
                model_id, _, build_ms = build_model(template, params, settings)
                latency["cad_ms"] = build_ms
                session.template = template
                session.params = params
                session.color = color
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                session.last_script = None
                rebuilt = True
                result_model_id = model_id
                save_session(session)
            except Exception as e:
                logger.exception("Legacy build failed: %s", e)
                error_msg = str(e)
                intent.reply = f"Build failed: {e}"
                action = "clarify"
        else:
            intent.reply = f"Unknown template '{template}'."
            action = "clarify"

    elif action == "modify":
        if not session.template or session.template not in DEFAULTS:
            intent.reply = "No model to modify. Say what you'd like to build."
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
                result_model_id = model_id
                save_session(session)
            except Exception as e:
                logger.exception("Modify failed: %s", e)
                error_msg = str(e)
                intent.reply = f"Modification failed: {e}"
                action = "clarify"

    elif action == "set_material":
        color = str(intent.params.get("color", session.color))
        session.color = color
        session.params["color"] = color
        
        # CRITICAL: Always rebuild to return a new model_id/glb_url.
        # A "successful" set_material with no new asset is a client no-op.
        rebuild_attempted = False
        
        if session.last_script:
            rebuild_attempted = True
            success, model_id, error = await _execute_with_retry(
                script=session.last_script,
                original_text="rebuild with new color",
                session=session,
                settings=settings,
                latency=latency,
            )
            if success:
                rebuilt = True
                result_model_id = model_id
            else:
                # Color change requested but rebuild failed - this is an error
                error_msg = f"Color change failed: {error}"
                intent.reply = f"Couldn't apply color: {error}"
        elif session.template and session.template in DEFAULTS:
            rebuild_attempted = True
            try:
                params = merge_params(session.template, session.params)
                model_id, _, build_ms = build_model(session.template, params, settings)
                latency["cad_ms"] = build_ms
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                rebuilt = True
                result_model_id = model_id
            except Exception as e:
                logger.warning("Rebuild for color failed: %s", e)
                error_msg = f"Color change failed: {e}"
                intent.reply = f"Couldn't apply color: {e}"
        
        if not rebuild_attempted:
            # No script or template to rebuild - cannot apply color to nothing
            error_msg = "No model to apply color to. Build something first."
            intent.reply = error_msg
        
        save_session(session)

    t0 = time.perf_counter()
    audio_url, tts_ms = await synthesize_speech(intent.reply, settings)
    latency["tts_ms"] = tts_ms if tts_ms else (time.perf_counter() - t0) * 1000

    # Always include model_id and version when rebuilt is True
    # Client MUST receive these to know it needs to swap the mesh
    response_model_id = result_model_id if rebuilt else session.model_id
    response_model_version = _next_model_version() if rebuilt else None

    return CommandResponse(
        ok=error_msg is None,
        transcript=transcript,
        reply=intent.reply,
        action=action,
        rebuilt=rebuilt,
        color=session.color,
        glb_url=session.glb_url,
        model_id=response_model_id,
        model_version=response_model_version,
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
    Used when external codegen provides CadQuery scripts directly.
    """
    latency: dict[str, float] = {}
    session.color = color

    success, model_id, error = await _execute_with_retry(
        script=script,
        original_text="direct script execution",
        session=session,
        settings=settings,
        latency=latency,
    )

    if success:
        return CommandResponse(
            ok=True,
            transcript=None,
            reply="Model built from script.",
            action="execute_script",
            rebuilt=True,
            color=color,
            glb_url=session.glb_url,
            model_id=model_id,
            model_version=_next_model_version(),
            reply_audio_url=None,
            session=session,
            latency_ms=latency,
        )
    else:
        return CommandResponse(
            ok=False,
            transcript=None,
            reply=f"Script execution failed: {error}",
            action="clarify",
            rebuilt=False,
            color=session.color,
            glb_url=session.glb_url,
            model_id=None,
            model_version=None,
            reply_audio_url=None,
            session=session,
            latency_ms=latency,
            error=error,
        )
