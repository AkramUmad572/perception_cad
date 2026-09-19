"""FastAPI entrypoint for Perception CAD."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ai.intent import parse_intent
from app import jobs
from app.config import get_settings
from app.models import CommandRequest, CommandResponse, PhotoChooseRequest, ScriptRequest
from app.pipeline import (
    apply_intent,
    build_chosen_photo,
    build_from_image,
    confirm_chosen_photo,
    execute_script_direct,
)
from app.session import clear_session, get_session
from mesh.refimage import isolate_subject
from voice.speech import transcribe_audio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("perception_cad")

settings = get_settings()
app = FastAPI(title="Perception CAD", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media/glb", StaticFiles(directory=str(settings.glb_dir)), name="glb")
app.mount("/media/audio", StaticFiles(directory=str(settings.audio_dir)), name="audio")
app.mount("/media/ref", StaticFiles(directory=str(settings.ref_dir)), name="ref")

WEB_DIST = Path(__file__).resolve().parents[2] / "web-client" / "dist"


@app.get("/api/health")
async def health():
    from cad.builder import _cadquery_available
    from mesh.factory import mesh_providers, mesh_ready

    providers = mesh_providers(settings)
    return {
        "ok": True,
        "cadquery": _cadquery_available(),
        "sandbox": True,
        "stt": bool(
            settings.elevenlabs_api_key
            or settings.deepgram_api_key
            or settings.openai_api_key
        ),
        "tts": bool(settings.elevenlabs_api_key),
        "llm": bool(settings.gemini_api_key or settings.openai_api_key),
        "llm_provider": (
            "gemini"
            if settings.gemini_api_key
            else "openai"
            if settings.openai_api_key
            else None
        ),
        "mesh": mesh_ready(settings),
        "mesh_provider": providers[0] if providers else None,
        "mesh_providers": providers,
        "stt_provider": (
            "elevenlabs"
            if settings.elevenlabs_api_key
            else "deepgram"
            if settings.deepgram_api_key
            else "openai"
            if settings.openai_api_key
            else None
        ),
        "drive": bool(settings.google_drive_api_key and settings.google_drive_folder_id),
    }


@app.get("/api/session/{session_id}")
async def session_state(session_id: str = "default"):
    return get_session(session_id)


@app.post("/api/session/{session_id}/reset")
async def reset_session(session_id: str = "default"):
    """Wipe the in-headset session so the next find-photo starts clean."""
    clear_session(session_id)
    return get_session(session_id)


@app.post("/api/command", response_model=CommandResponse)
async def command(body: CommandRequest):
    t_all = time.perf_counter()
    session = get_session(body.session_id)
    intent, intent_ms = await parse_intent(
        body.text,
        settings,
        session.template,
        session.params,
        session.last_script,
        last_summary=session.last_summary,
        current_color=session.color,
        last_backend=session.last_backend,
        last_mesh_prompt=session.last_mesh_prompt,
    )
    result = await apply_intent(
        intent,
        session,
        settings,
        transcript=body.text,
        extra_latency={"intent_ms": intent_ms},
    )
    result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
    return result


@app.post("/api/script", response_model=CommandResponse)
async def execute_script(body: ScriptRequest):
    """
    Execute a CadQuery script directly in the sandbox.

    This endpoint is for Taha's codegen integration - bypasses intent parsing
    and executes the script directly with full safety (timeout, no FS/network,
    non-manifold rejection).

    The script must define a 'result', 'solid', or 'model' variable.
    """
    t_all = time.perf_counter()
    session = get_session(body.session_id)

    result = await execute_script_direct(
        script=body.script,
        session=session,
        settings=settings,
        color=body.color,
    )
    result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
    return result


@app.post("/api/image", response_model=CommandResponse)
async def image_to_3d(
    image: UploadFile = File(...),
    session_id: str = Form("default"),
    prompt: str = Form(""),
    quality: str = Form("draft"),
):
    """
    Build a model from a reference photo.

    three.ws fetches the image itself, so PUBLIC_BASE_URL must be an address
    reachable from the internet (a tunnel in dev) — not localhost.
    """
    t_all = time.perf_counter()
    session = get_session(session_id)
    try:
        raw = await image.read()
        if not raw or len(raw) < 1024:
            return CommandResponse(
                ok=False,
                reply="That image didn't come through. Try another one.",
                action="clarify",
                session=session,
                latency_ms={"total_ms": (time.perf_counter() - t_all) * 1000},
            )

        suffix = Path(image.filename or "ref.png").suffix.lower() or ".png"
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            suffix = ".png"
        ref_id = uuid.uuid4().hex[:12]
        raw_path = settings.ref_dir / f"{ref_id}_raw{suffix}"
        raw_path.write_bytes(raw)

        # Image-to-3D rebuilds whatever fills the frame, so an un-cut photo
        # comes back as a flat card. Fall back to the original if the cut fails.
        served = f"{ref_id}_raw{suffix}"
        cut_path = settings.ref_dir / f"{ref_id}.png"
        cut = isolate_subject(raw_path, cut_path)
        if cut.get("ok"):
            served = cut_path.name
        else:
            logger.info("Subject isolation skipped: %s", cut.get("reason"))

        base = settings.public_base_url.rstrip("/")
        image_url = f"{base}/media/ref/{served}"
        # HF Spaces upload the local file. Only three.ws still needs a public URL.

        local = settings.ref_dir / served
        result = await build_from_image(
            image_url,
            session,
            settings,
            prompt=prompt,
            quality=quality,
            image_path=local if local.exists() else None,
        )
        result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
        return result
    except Exception as exc:
        logger.exception("Image-to-3D failed: %s", exc)
        return CommandResponse(
            ok=False,
            reply="That photo didn't work — try another.",
            action="clarify",
            session=session,
            error=str(exc),
            latency_ms={"total_ms": (time.perf_counter() - t_all) * 1000},
        )


@app.post("/api/photos/confirm", response_model=CommandResponse)
async def confirm_photo(body: PhotoChooseRequest):
    """Speak a confirmation as soon as the user pinches a photo."""
    session = get_session(body.session_id)
    return await confirm_chosen_photo(body.file_id, session, settings)


@app.post("/api/photos/choose", response_model=CommandResponse)
async def choose_photo(body: PhotoChooseRequest):
    """
    Start building the Drive photo the user picked in AR.

    Returns a job id immediately — the sculpt takes minutes, which is longer
    than the connection between the headset and here reliably survives.
    """
    session = get_session(body.session_id)
    file_id = body.file_id
    session_id = body.session_id

    async def work() -> CommandResponse:
        t_all = time.perf_counter()
        result = await build_chosen_photo(file_id, get_session(session_id), settings)
        result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
        return result

    return CommandResponse(
        ok=True,
        reply="Building that from your photo.",
        action="building",
        session=session,
        backend="mesh",
        job_id=jobs.start(work),
    )


@app.get("/api/jobs/{job_id}", response_model=CommandResponse)
async def job_status(job_id: str, session_id: str = "default"):
    """Poll a detached build. `action` stays "building" until it lands."""
    session = get_session(session_id)
    job = jobs.get(job_id)

    if job is None:
        return CommandResponse(
            ok=False,
            reply="That build is gone. Ask me to find the photo again.",
            action="clarify",
            session=session,
            error=f"Unknown job {job_id}",
        )

    if job.result is not None:
        job.result.job_id = job_id
        return job.result

    if job.done:
        return CommandResponse(
            ok=False,
            reply="That build failed. Try again.",
            action="clarify",
            session=session,
            error=job.error,
            job_id=job_id,
        )

    # No reply_audio_url: a poll every few seconds must not talk over itself.
    return CommandResponse(
        ok=True,
        reply="Still sculpting…",
        action="building",
        session=session,
        backend="mesh",
        job_id=job_id,
        latency_ms={"elapsed_ms": job.elapsed_s * 1000},
    )


@app.post("/api/voice", response_model=CommandResponse)
async def voice(
    audio: UploadFile = File(...),
    session_id: str = Form("default"),
):
    t_all = time.perf_counter()
    session = get_session(session_id)
    try:
        raw = await audio.read()
        if not raw or len(raw) < 200:
            return CommandResponse(
                ok=False,
                transcript="",
                reply="Hold to talk a bit longer, then release.",
                action="clarify",
                session=session,
                latency_ms={"total_ms": (time.perf_counter() - t_all) * 1000},
            )

        transcript, stt_ms = await transcribe_audio(
            raw, audio.filename or "audio.webm", settings
        )
        if not transcript:
            return CommandResponse(
                ok=False,
                transcript="",
                reply="I didn't catch that. Try again.",
                action="clarify",
                session=session,
                latency_ms={"stt_ms": stt_ms},
            )

        intent, intent_ms = await parse_intent(
            transcript,
            settings,
            session.template,
            session.params,
            session.last_script,
            last_summary=session.last_summary,
            current_color=session.color,
            last_backend=session.last_backend,
            last_mesh_prompt=session.last_mesh_prompt,
        )
        result = await apply_intent(
            intent,
            session,
            settings,
            transcript=transcript,
            extra_latency={"stt_ms": stt_ms, "intent_ms": intent_ms},
        )
        result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
        return result
    except Exception as exc:
        logger.exception("Voice pipeline failed: %s", exc)
        return CommandResponse(
            ok=False,
            transcript=None,
            reply="Voice failed — try again.",
            action="clarify",
            session=session,
            latency_ms={"total_ms": (time.perf_counter() - t_all) * 1000},
        )


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:

    @app.get("/")
    async def root_fallback():
        return {
            "message": "Perception CAD API. Build web-client (npm run build) or use Vite dev server.",
            "health": "/api/health",
        }


def main():
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
