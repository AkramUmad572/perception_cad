"""FastAPI entrypoint for Perception CAD."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ai.intent import parse_intent
from app.config import get_settings
from app.models import CommandRequest, CommandResponse, ScriptRequest
from app.pipeline import apply_intent, execute_script_direct
from app.session import get_session
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

WEB_DIST = Path(__file__).resolve().parents[2] / "web-client" / "dist"


@app.get("/api/health")
async def health():
    from cad.builder import _cadquery_available

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
        "stt_provider": (
            "elevenlabs"
            if settings.elevenlabs_api_key
            else "deepgram"
            if settings.deepgram_api_key
            else "openai"
            if settings.openai_api_key
            else None
        ),
    }


@app.get("/api/session/{session_id}")
async def session_state(session_id: str = "default"):
    return get_session(session_id)


@app.post("/api/command", response_model=CommandResponse)
async def command(body: CommandRequest):
    t_all = time.perf_counter()
    session = get_session(body.session_id)
    intent, intent_ms = await parse_intent(
        body.text, settings, session.template, session.params
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
            transcript, settings, session.template, session.params
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
