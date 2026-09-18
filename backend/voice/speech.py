"""Speech-to-text and text-to-speech helpers (ElevenLabs first)."""

from __future__ import annotations

import logging
import re
import time
import uuid

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


PERCY_ALIASES = re.compile(
    # Optional leftover wake names — not "see" / "i see" (real commands can start that way).
    r"^\s*(?:pursee|persey|piercy|mercy|merci|percy|perce|purse|pc)[,.\s]*",
    re.IGNORECASE,
)

HEY_PERCY_ALIASES = re.compile(
    r"^\s*(?:"
    r"hey\s+(?:percy|mercy|merce|merci|pursey|pursee|purse|persey|piercy|see|perce|pc)|"
    r"a\s+(?:mercy|percy|merce|pursey)"
    r")[,.\s]*",
    re.IGNORECASE,
)


def normalize_wake_word(transcript: str) -> str:
    """
    Normalize common STT misrecognitions of wake words at start of utterance.
    
    'Hey Percy' variants (canonical wake phrase):
      hey mercy, hey see, hey merce, hey pursey, a mercy → hey percy
    
    Legacy leftover wake names (still stripped if STT prefixes them):
      Mercy, Merci, PC, purse, perce, persey, piercy → Percy
    Bare "see" / "I see" are left alone so they are not treated as a wake word.
    """
    match = HEY_PERCY_ALIASES.match(transcript)
    if match:
        remainder = transcript[match.end():].strip()
        if remainder:
            return f"hey percy, {remainder}"
        return "hey percy"
    
    match = PERCY_ALIASES.match(transcript)
    if match:
        remainder = transcript[match.end():].strip()
        if remainder:
            return f"Percy, {remainder}"
        return "Percy"
    return transcript

CAD_KEYTERMS = [
    # Wake word and common misrecognitions
    "Percy",
    "percy",
    # Generic CAD/modeling verbs
    "build",
    "make",
    "create",
    "design",
    # Dimension/size words
    "thicker",
    "thinner",
    "bigger",
    "smaller",
    "taller",
    "shorter",
    "wider",
    "diameter",
    "millimeters",
    # Colors
    "yellow",
    "blue",
    "red",
    "green",
    "gold",
    "golden",
]


def _audio_mime(filename: str) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".wav"):
        return "audio/wav"
    if lower.endswith(".mp3"):
        return "audio/mpeg"
    if lower.endswith(".m4a"):
        return "audio/mp4"
    if lower.endswith(".ogg"):
        return "audio/ogg"
    return "audio/webm"


async def _elevenlabs_stt(
    audio_bytes: bytes, filename: str, settings: Settings
) -> str:
    """Transcribe with ElevenLabs Scribe. Raises on failure."""
    mime = _audio_mime(filename)
    name = filename or "utterance.webm"

    async with httpx.AsyncClient(timeout=90.0) as client:
        last_err: Exception | None = None
        for model_id in ("scribe_v2", "scribe_v1"):
            # Try with keyterms, then without if rejected
            for with_keyterms in (True, False):
                try:
                    # Put fields in `files` (not `data`) so AsyncClient does not
                    # try a sync multipart encode (httpx: "sync request with AsyncClient").
                    files: list[tuple[str, tuple]] = [
                        ("file", (name, audio_bytes, mime)),
                        ("model_id", (None, model_id)),
                        ("language_code", (None, "eng")),
                        ("tag_audio_events", (None, "false")),
                    ]
                    if with_keyterms:
                        for term in CAD_KEYTERMS:
                            files.append(("keyterms", (None, term)))

                    resp = await client.post(
                        "https://api.elevenlabs.io/v1/speech-to-text",
                        headers={"xi-api-key": settings.elevenlabs_api_key},
                        files=files,
                    )
                    if resp.status_code == 422 and with_keyterms:
                        logger.info("Scribe rejected keyterms; retrying plain")
                        continue
                    resp.raise_for_status()
                    transcript = (resp.json().get("text") or "").strip()
                    transcript = normalize_wake_word(transcript)
                    logger.info("STT(%s): %r", model_id, transcript)
                    return transcript
                except Exception as exc:
                    last_err = exc
                    logger.warning(
                        "ElevenLabs STT %s keyterms=%s failed: %s",
                        model_id,
                        with_keyterms,
                        exc,
                    )
        raise RuntimeError(f"ElevenLabs STT failed: {last_err}")


async def transcribe_audio(
    audio_bytes: bytes, filename: str, settings: Settings
) -> tuple[str, float]:
    """Return (transcript, stt_ms). ElevenLabs → Deepgram → OpenAI Whisper."""
    t0 = time.perf_counter()

    if settings.elevenlabs_api_key:
        try:
            transcript = await _elevenlabs_stt(audio_bytes, filename, settings)
            return transcript, (time.perf_counter() - t0) * 1000
        except Exception as exc:
            logger.warning("ElevenLabs STT unavailable: %s", exc)

    if settings.deepgram_api_key:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.deepgram.com/v1/listen"
                "?model=nova-2&smart_format=true&language=en",
                headers={
                    "Authorization": f"Token {settings.deepgram_api_key}",
                    "Content-Type": "application/octet-stream",
                },
                content=audio_bytes,
            )
            resp.raise_for_status()
            data = resp.json()
            transcript = (
                data.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
                .get("transcript", "")
                .strip()
            )
            transcript = normalize_wake_word(transcript)
            logger.info("STT(deepgram): %r", transcript)
            return transcript, (time.perf_counter() - t0) * 1000

    if settings.openai_api_key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            tmp = settings.audio_dir / f"in_{uuid.uuid4().hex[:8]}_{filename}"
            tmp.write_bytes(audio_bytes)
            try:
                with tmp.open("rb") as f:
                    result = await client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        language="en",
                        prompt=(
                            "CAD voice commands: build me a ring, box, cube, cylinder, "
                            "make it yellow, make the band thicker, make it bigger."
                        ),
                    )
                transcript = (result.text or "").strip()
                transcript = normalize_wake_word(transcript)
                logger.info("STT(whisper): %r", transcript)
                return transcript, (time.perf_counter() - t0) * 1000
            finally:
                tmp.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("OpenAI Whisper STT failed: %s", exc)

    raise RuntimeError(
        "Speech-to-text failed. Check ELEVENLABS_API_KEY (Speech to Text permission)."
    )


async def synthesize_speech(text: str, settings: Settings) -> tuple[str | None, float]:
    """ElevenLabs TTS. Returns (relative audio url path or None, tts_ms)."""
    t0 = time.perf_counter()
    if not settings.elevenlabs_api_key or not text.strip():
        return None, 0.0

    out_name = f"reply_{uuid.uuid4().hex[:10]}.mp3"
    out_path = settings.audio_dir / out_name

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
                headers={
                    "xi-api-key": settings.elevenlabs_api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": text,
                    "model_id": "eleven_turbo_v2_5",
                    "voice_settings": {"stability": 0.4, "similarity_boost": 0.7},
                },
            )
            resp.raise_for_status()
            out_path.write_bytes(resp.content)
        return f"/media/audio/{out_name}", (time.perf_counter() - t0) * 1000
    except Exception as exc:
        logger.warning("ElevenLabs TTS failed: %s", exc)
        return None, (time.perf_counter() - t0) * 1000
