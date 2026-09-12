"""Perception CAD backend settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
STORAGE = ROOT / "storage"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    public_base_url: str = "http://127.0.0.1:8000"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Google Gemini (preferred when set — free-tier friendly)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"

    glb_dir: Path = STORAGE / "glb"
    audio_dir: Path = STORAGE / "audio"
    prefer_cadquery: bool = True


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.glb_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    return settings
