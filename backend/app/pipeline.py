"""Apply intents: codegen → sandbox execution → retry on failure."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ai.intent import extract_named_color
from app.config import Settings
from app.models import CommandResponse, Intent, SessionState
from app.session import save_session
from cad import DEFAULTS
from cad.sandbox import execute_cadquery_script
from cad.builder import merge_params, build_model
from mesh.factory import (
    generate_mesh_glb,
    generate_mesh_glb_from_image,
    mesh_ready,
)
from photos.drive import download_file, list_images
from photos.search import find_photos
from photos.stage import stage_photo
from voice.speech import synthesize_speech

logger = logging.getLogger(__name__)

MAX_RETRIES = 2

# CadQuery models carry real millimetre dimensions, so they are shown life size.
# The clamps only stop a stray script from producing something invisible or
# room-filling in AR.
CAD_MIN_M = 0.04
CAD_MAX_M = 1.00
# Mesh output is unit-normalised, so a sculpt has no real size of its own.
MESH_DEFAULT_M = 0.20
SIZE_MIN_M = 0.03
SIZE_MAX_M = 2.00


def _glb_url(settings: Settings, model_id: str) -> str:
    return f"/media/glb/{model_id}.glb"


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _cad_size_m(settings: Settings, model_id: str) -> float:
    """
    Longest real dimension of a CadQuery GLB, in metres.

    The sandbox already converts mm to metres on export, so this reads straight
    off the bounds — the script's real dimensions survive into the headset.
    """
    try:
        import trimesh

        scene = trimesh.load(str(settings.glb_dir / f"{model_id}.glb"))
        lo, hi = scene.bounds
        longest_m = float(max(hi - lo))
    except Exception as exc:
        logger.info("Could not measure %s: %s", model_id, exc)
        return MESH_DEFAULT_M
    if longest_m <= 0:
        return MESH_DEFAULT_M
    return _clamp(longest_m, CAD_MIN_M, CAD_MAX_M)


def _mesh_size_m(size_mm: float | None) -> float:
    if not size_mm or size_mm <= 0:
        return MESH_DEFAULT_M
    return _clamp(float(size_mm) / 1000.0, SIZE_MIN_M, SIZE_MAX_M)


def _display_size_m(session: SessionState) -> float:
    return _clamp(session.base_size_m * session.scale, SIZE_MIN_M, SIZE_MAX_M)


async def _execute_with_retry(
    script: str,
    original_text: str,
    session: SessionState,
    settings: Settings,
    latency: dict[str, float],
    flatten_color: bool = True,
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
            timeout=45.0,
            color=color,
            flatten_color=flatten_color,
        )
        latency[f"cad_ms_attempt_{attempt}"] = result.get("exec_ms", 0)

        if result["ok"]:
            latency["cad_ms"] = result.get("exec_ms", 0)
            session.template = None
            session.params = {}
            session.model_id = result["model_id"]
            session.glb_url = _glb_url(settings, result["model_id"])
            session.last_script = current_script
            session.last_backend = "cad"
            session.last_mesh_prompt = None
            session.base_size_m = _cad_size_m(settings, result["model_id"])
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


async def _execute_mesh(
    prompt: str,
    session: SessionState,
    settings: Settings,
    latency: dict[str, float],
    size_mm: float | None = None,
    use_nvidia: bool = True,
) -> tuple[bool, str | None, str | None, bool]:
    """Text-to-3D via three.ws / NVIDIA / Meshy. Never falls back to CadQuery."""
    nvidia_key = getattr(settings, "nvidia_api_key", "") or ""
    result = await generate_mesh_glb(
        prompt=prompt,
        output_dir=settings.glb_dir,
        meshy_api_key=settings.meshy_api_key or "",
        nvidia_api_key=nvidia_key if use_nvidia else "",
        three_ws=bool(getattr(settings, "three_ws_enabled", True)),
        timeout_s=150.0,
    )
    latency["mesh_ms"] = result.get("exec_ms", 0)
    if not result.get("ok"):
        return False, None, result.get("error", "Mesh generation failed"), False

    session.template = None
    session.params = {}
    session.model_id = result["model_id"]
    session.glb_url = _glb_url(settings, result["model_id"])
    session.last_script = None
    session.last_backend = "mesh"
    session.last_mesh_prompt = prompt
    session.base_size_m = _mesh_size_m(size_mm)
    save_session(session)
    return True, result["model_id"], None, bool(result.get("textured", True))


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
    - generate + backend=mesh: Meshy text-to-3D (no CadQuery)
    - generate + backend=cad: CadQuery script via sandbox (with retry loop)
    - set_material: CAD rebuild, or mesh re-gen with color in the prompt
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
    textured = False
    response_backend = intent.backend or session.last_backend or "cad"
    candidates: list[dict] = []

    if action == "find_photos":
        response_backend = "mesh"
        try:
            files = await list_images(settings)
        except Exception as exc:
            logger.warning("Drive list failed: %s", exc)
            error_msg = str(exc)
            intent.reply = "I couldn't reach your photos."
            action = "clarify"
            files = []
        if files:
            query = (intent.photo_query or "").strip() or (transcript or "").strip()
            staged: list[dict] = []
            thumbs: dict[str, bytes] = {}
            for item in files:
                try:
                    raw, mime = await download_file(item["id"], settings)
                except Exception as exc:
                    logger.warning("Drive download %s failed: %s", item.get("id"), exc)
                    continue
                thumbs[item["id"]] = raw[: min(len(raw), 400_000)]
                info = stage_photo(
                    raw,
                    settings,
                    mime=mime,
                    name=item.get("name") or "",
                    file_id=item["id"],
                )
                staged.append(
                    {
                        "id": item["id"],
                        "name": item.get("name") or "photo",
                        "preview_url": info["preview_url"],
                        "image_url": info["preview_url"],
                        "build_url": info["build_url"],
                    }
                )
            matches = await find_photos(query, files, settings, thumbs)
            match_ids = {m["id"] for m in matches}
            candidates = [s for s in staged if s["id"] in match_ids] or staged
            session.last_photos = candidates
            save_session(session)
            n = len(candidates)
            if n == 0:
                intent.reply = "I didn't find that in your photos."
                action = "clarify"
            elif n == 1:
                intent.reply = "Found it. Pinch to confirm."
            else:
                intent.reply = f"Found {n}. Pinch to pick one."

    elif action == "generate" and (intent.backend or "cad") == "mesh":
        prompt = (intent.mesh_prompt or "").strip()
        if not prompt:
            intent.reply = "No mesh prompt was generated."
            action = "clarify"
            response_backend = "mesh"
        elif not mesh_ready(settings):
            intent.reply = (
                "Mesh generation isn't configured. "
                "three.ws should work with no key; or set NVIDIA_API_KEY / MESHY_API_KEY."
            )
            action = "clarify"
            response_backend = "mesh"
        else:
            session.scale = 1.0
            success, model_id, error, _mesh_textured = await _execute_mesh(
                prompt, session, settings, latency, size_mm=intent.size_mm
            )
            if success:
                rebuilt = True
                result_model_id = model_id
                textured = True
                response_backend = "mesh"
                session.last_summary = intent.reply
                save_session(session)
            else:
                error_msg = error
                intent.reply = f"Sculpt failed: {error}"
                action = "clarify"
                response_backend = "mesh"

    elif action == "set_scale":
        # Resizing a sculpt is a display change, not a reason to spend 90s
        # rebuilding a model that would come back looking different anyway.
        if not session.model_id:
            error_msg = "No model to resize. Build something first."
            intent.reply = error_msg
            action = "clarify"
        else:
            factor = float(intent.params.get("factor") or 1.0)
            session.scale = _clamp(
                session.scale * factor, SIZE_MIN_M / session.base_size_m,
                SIZE_MAX_M / session.base_size_m,
            )
            response_backend = session.last_backend
            save_session(session)

    elif action == "generate":
        if not intent.script:
            intent.reply = "No code was generated."
            action = "clarify"
        else:
            session.scale = 1.0
            named = extract_named_color(transcript or "")
            if named:
                session.color = named
                session.params["color"] = named
            success, model_id, error = await _execute_with_retry(
                script=intent.script,
                original_text=transcript or "",
                session=session,
                settings=settings,
                latency=latency,
                flatten_color=False,
            )
            if success:
                rebuilt = True
                result_model_id = model_id
                response_backend = "cad"
                session.last_summary = intent.reply
                save_session(session)
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
                session.last_summary = intent.reply
                save_session(session)
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
                session.last_backend = "cad"
                session.last_mesh_prompt = None
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

        rebuild_attempted = False

        if session.last_backend == "mesh" and session.last_mesh_prompt:
            rebuild_attempted = True
            if not mesh_ready(settings):
                error_msg = "Mesh generation isn't configured."
                intent.reply = (
                    "Mesh generation isn't configured. "
                    "three.ws should work with no key; or set NVIDIA_API_KEY / MESHY_API_KEY."
                )
                action = "clarify"
                response_backend = "mesh"
            else:
                prompt = f"{session.last_mesh_prompt}, overall color {color}"
                success, model_id, error, _tex = await _execute_mesh(
                    prompt, session, settings, latency
                )
                if success:
                    rebuilt = True
                    result_model_id = model_id
                    textured = True
                    response_backend = "mesh"
                    session.color = color
                    session.params["color"] = color
                else:
                    error_msg = f"Color change failed: {error}"
                    intent.reply = f"Couldn't apply color: {error}"
                    response_backend = "mesh"
        elif session.last_script:
            rebuild_attempted = True
            success, model_id, error = await _execute_with_retry(
                script=session.last_script,
                original_text="rebuild with new color",
                session=session,
                settings=settings,
                latency=latency,
                flatten_color=True,
            )
            if success:
                rebuilt = True
                result_model_id = model_id
                response_backend = "cad"
            else:
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
                session.last_backend = "cad"
                rebuilt = True
                result_model_id = model_id
                response_backend = "cad"
            except Exception as e:
                logger.warning("Rebuild for color failed: %s", e)
                error_msg = f"Color change failed: {e}"
                intent.reply = f"Couldn't apply color: {e}"

        if not rebuild_attempted:
            error_msg = "No model to apply color to. Build something first."
            intent.reply = error_msg

        save_session(session)

    t0 = time.perf_counter()
    audio_url, tts_ms = await synthesize_speech(intent.reply, settings)
    latency["tts_ms"] = tts_ms if tts_ms else (time.perf_counter() - t0) * 1000

    # Always include model_id when rebuilt is True
    # Client uses fresh model_id + glb_url to know it needs to swap the mesh
    response_model_id = result_model_id if rebuilt else session.model_id

    return CommandResponse(
        ok=error_msg is None,
        transcript=transcript,
        reply=intent.reply,
        action=action,
        rebuilt=rebuilt,
        color=session.color,
        glb_url=session.glb_url,
        model_id=response_model_id,
        reply_audio_url=audio_url,
        session=session,
        latency_ms=latency,
        error=error_msg,
        textured=textured,
        backend=response_backend,
        display_size_m=_display_size_m(session),
        candidates=candidates if action == "find_photos" else [],
    )


async def _cad_from_photo(
    image_path: Path,
    session: SessionState,
    settings: Settings,
    latency: dict[str, float],
    hint: str = "",
) -> tuple[bool, str | None, str | None]:
    """Rebuild the photo's subject as CadQuery when the sculptors are down."""
    from ai.intent import codegen_from_photo

    t0 = time.perf_counter()
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    try:
        intent = await codegen_from_photo(
            image_path.read_bytes(), mime, settings, hint=hint
        )
    except Exception as exc:
        logger.warning("Photo codegen failed: %s", exc)
        return False, None, str(exc)
    latency["photo_codegen_ms"] = (time.perf_counter() - t0) * 1000

    if not intent.script:
        return False, None, "Gemini returned no script for the photo."

    session.scale = 1.0
    success, model_id, error = await _execute_with_retry(
        script=intent.script,
        original_text=f"the object in the photo{f' ({hint})' if hint else ''}",
        session=session,
        settings=settings,
        latency=latency,
        flatten_color=False,
    )
    if success:
        session.last_summary = intent.reply
        save_session(session)
    return success, model_id, error


async def build_from_image(
    image_url: str,
    session: SessionState,
    settings: Settings,
    prompt: str = "",
    speak: bool = True,
    quality: str = "draft",
    image_path: Path | None = None,
) -> CommandResponse:
    """Image-to-3D: a reference photo beats describing the object in words."""
    latency: dict[str, float] = {}
    t0 = time.perf_counter()

    result = await generate_mesh_glb_from_image(
        image_url,
        settings.glb_dir,
        prompt=prompt,
        quality=quality,
        image_path=image_path,
        hf_token=getattr(settings, "hf_token", "") or "",
        hf_space=bool(getattr(settings, "hf_space_enabled", True)),
        three_ws=bool(getattr(settings, "three_ws_enabled", True)),
    )
    latency["mesh_ms"] = result.get("exec_ms", (time.perf_counter() - t0) * 1000)

    if not result.get("ok"):
        error = str(result.get("error") or "Image-to-3D failed.")
        logger.info("Photo lane unavailable (%s); trying text sculpt", error)

        text_prompt = (prompt or "").strip()
        if image_path and image_path.exists() and settings.gemini_api_key:
            try:
                from ai.intent import mesh_prompt_from_photo

                mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
                text_prompt = await mesh_prompt_from_photo(
                    image_path.read_bytes(), mime, settings, hint=prompt
                )
                latency["photo_describe_ms"] = (time.perf_counter() - t0) * 1000
            except Exception as exc:
                logger.warning("Photo describe failed: %s", exc)

        if text_prompt:
            ok, model_id, mesh_error, textured = await _execute_mesh(
                text_prompt, session, settings, latency, use_nvidia=False
            )
            if ok:
                reply = (
                    "The photo engine was busy, so I sculpted it from "
                    "what I saw in the picture."
                )
                audio_url, tts_ms = (
                    await synthesize_speech(reply, settings) if speak else (None, 0)
                )
                latency["tts_ms"] = tts_ms
                return CommandResponse(
                    ok=True,
                    reply=reply,
                    action="generate",
                    rebuilt=True,
                    color=session.color,
                    glb_url=session.glb_url,
                    model_id=model_id,
                    reply_audio_url=audio_url,
                    session=session,
                    latency_ms=latency,
                    textured=textured,
                    backend="mesh",
                    display_size_m=_display_size_m(session),
                )
            error = f"{error} | text-sculpt: {mesh_error}"

        logger.info("Text sculpt unavailable; trying CAD from the photo")

        if image_path and image_path.exists():
            ok, model_id, cad_error = await _cad_from_photo(
                image_path, session, settings, latency, hint=prompt
            )
            if ok:
                reply = "The sculptor was down, so I modelled it in CAD instead."
                audio_url, tts_ms = (
                    await synthesize_speech(reply, settings) if speak else (None, 0)
                )
                latency["tts_ms"] = tts_ms
                return CommandResponse(
                    ok=True,
                    reply=reply,
                    action="generate",
                    rebuilt=True,
                    color=session.color,
                    glb_url=session.glb_url,
                    model_id=model_id,
                    reply_audio_url=audio_url,
                    session=session,
                    latency_ms=latency,
                    textured=False,
                    backend="cad",
                    display_size_m=_display_size_m(session),
                )
            error = f"{error} | cad-from-photo: {cad_error}"

        if result.get("error_type") == "busy":
            reply = "The free sculpting service is down. Try again in a few minutes."
        else:
            reply = "I couldn't build that from the photo."
        audio_url, _ = await synthesize_speech(reply, settings) if speak else (None, 0)
        return CommandResponse(
            ok=False,
            reply=reply,
            action="clarify",
            session=session,
            latency_ms=latency,
            error=error,
            backend="mesh",
            reply_audio_url=audio_url,
        )

    session.template = None
    session.params = {}
    session.model_id = result["model_id"]
    session.glb_url = _glb_url(settings, result["model_id"])
    session.last_script = None
    session.last_backend = "mesh"
    session.last_mesh_prompt = prompt or session.last_mesh_prompt
    session.base_size_m = MESH_DEFAULT_M
    session.scale = 1.0
    save_session(session)

    reply = "Built that from your photo."
    audio_url, tts_ms = await synthesize_speech(reply, settings) if speak else (None, 0)
    latency["tts_ms"] = tts_ms

    return CommandResponse(
        ok=True,
        reply=reply,
        action="generate",
        rebuilt=True,
        color=session.color,
        glb_url=session.glb_url,
        model_id=result["model_id"],
        reply_audio_url=audio_url,
        session=session,
        latency_ms=latency,
        textured=bool(result.get("textured", True)),
        backend="mesh",
        display_size_m=_display_size_m(session),
    )


async def build_chosen_photo(
    file_id: str,
    session: SessionState,
    settings: Settings,
) -> CommandResponse:
    """Sculpt the Drive photo the user pinched in the picker."""
    chosen = next((p for p in session.last_photos if p.get("id") == file_id), None)
    if not chosen:
        reply = "I don't have that photo anymore. Ask me to find it again."
        audio_url, _ = await synthesize_speech(reply, settings)
        return CommandResponse(
            ok=False,
            reply=reply,
            action="clarify",
            session=session,
            reply_audio_url=audio_url,
            backend="mesh",
        )

    prompt = chosen.get("name") or ""
    # The staged cut-out on disk, for the CAD fallback when sculpting is down.
    local = settings.ref_dir / chosen["build_url"].rsplit("/", 1)[-1]
    return await build_from_image(
        chosen["build_url"],
        session,
        settings,
        prompt=prompt,
        quality="draft",
        image_path=local,
    )


def _spoken_photo_name(chosen: dict) -> str:
    raw = (chosen.get("name") or "this").strip()
    stem = raw.rsplit(".", 1)[0] if "." in raw else raw
    label = stem.replace("_", " ").replace("-", " ").strip()
    return label or "this"


async def confirm_chosen_photo(
    file_id: str,
    session: SessionState,
    settings: Settings,
) -> CommandResponse:
    """Spoken confirmation the moment the user pinches — before the long sculpt."""
    chosen = next((p for p in session.last_photos if p.get("id") == file_id), None)
    if not chosen:
        reply = "I don't have that photo anymore. Ask me to find it again."
        audio_url, _ = await synthesize_speech(reply, settings)
        return CommandResponse(
            ok=False,
            reply=reply,
            action="clarify",
            session=session,
            reply_audio_url=audio_url,
            backend="mesh",
        )
    label = _spoken_photo_name(chosen)
    reply = f"Sounds good. Building this image of {label}."
    audio_url, tts_ms = await synthesize_speech(reply, settings)
    return CommandResponse(
        ok=True,
        reply=reply,
        action="confirm_photo",
        session=session,
        reply_audio_url=audio_url,
        backend="mesh",
        latency_ms={"tts_ms": tts_ms},
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
            reply_audio_url=None,
            session=session,
            latency_ms=latency,
            textured=False,
            backend="cad",
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
            reply_audio_url=None,
            session=session,
            latency_ms=latency,
            error=error,
        )
