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

    # Meshy (paid) or NVIDIA TRELLIS (free key) or three.ws (keyless draft).
    meshy_api_key: str = ""
    nvidia_api_key: str = ""
    three_ws_enabled: bool = True
    # Optional free Hugging Face token. Raises ZeroGPU daily budget (~5 min).
    hf_token: str = ""
    hf_space_enabled: bool = True

    # Public Drive folder of demo photos. Empty = photo search is off.
    google_drive_api_key: str = ""
    google_drive_folder_id: str = ""

    glb_dir: Path = STORAGE / "glb"
    audio_dir: Path = STORAGE / "audio"
    # Reference photos for image-to-3D. three.ws fetches these by URL, so they
    # have to be reachable from outside this machine.
    ref_dir: Path = STORAGE / "ref"
    prefer_cadquery: bool = True


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.glb_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    settings.ref_dir.mkdir(parents=True, exist_ok=True)
    return settings
