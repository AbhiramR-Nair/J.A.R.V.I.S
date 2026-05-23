"""Application settings, loaded from .env via Pydantic Settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Pydantic Settings reads from .env automatically based on field names.
# Empty string defaults are deliberate — we don't have Groq/Tavily keys yet,
# and we don't want imports to fail. The *callers* of those services (Day 9, 25)
# will check `settings.groq_api_key` for emptiness and error there with a clear message.
class Settings(BaseSettings):
    """Loads config from .env via Pydantic Settings.

    Empty defaults are intentional for keys we don't have yet (Groq, Tavily).
    Code that *uses* a key checks emptiness and errors clearly; importing this
    module must never fail because a key is missing.
    """

    # API keys (some may be empty in early weeks)
    gemini_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    tavily_api_key: str = ""

    # Server config
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_dev_url: str = "http://localhost:5173"

    # Logging
    log_level: str = "INFO"
    log_file: str = "data/logs/jarvis.log"

    # LLM model selection
    # gemini_model_heavy is reserved for quality-critical calls: Day 6 importance
    # scoring and Days 22-24 paper summarization. Chat uses the flash model.
    gemini_model: str = "gemini-2.5-flash"
    gemini_model_heavy: str = "gemini-2.5-pro"
    openai_model: str = "gpt-4o-mini"  # kept for reference; not used as fallback
    groq_model: str = "llama-3.3-70b-versatile"  # fallback LLM; free tier

    # Database
    db_path: str = "data/jarvis.db"

    # ChromaDB
    chroma_persist_dir: str = "data/chroma"
    # Embeddings (text-embedding-004 was retired; gemini-embedding-001 is the replacement)
    gemini_embedding_model: str = "gemini-embedding-001"

    # Memory importance threshold: exchanges scoring below this are not stored
    importance_threshold: float = 4.0

    # Audio capture (Day 8)
    # These values are locked to what Groq Whisper and openWakeWord both require.
    # Do not change sample_rate or dtype without checking downstream consumers.
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_dtype: str = "int16"
    audio_chunk_ms: int = 50            # sounddevice callback granularity in ms
    recording_max_seconds: int = 30     # PTT hard cap; auto-stops to protect memory
    recordings_dir: Path = Path("data/recordings")
    runtime_settings_path: Path = Path("data/settings.json")

    # STT — Groq Whisper (Day 9)
    # stt_model is pinned here so Day-12 can swap to whisper-large-v3-turbo without touching code.
    # stt_language="en" improves accuracy for technical identifiers (ABL1, T315I, RNA-seq).
    # stt_temperature=0.0 keeps output deterministic when re-testing the same WAV.
    # stt_timeout_seconds is a hard ceiling so a hung Groq call cannot wedge the dispatcher.
    stt_model: str = "whisper-large-v3"
    stt_language: str | None = "en"
    stt_temperature: float = 0.0
    stt_timeout_seconds: float = 10.0

    # Misc
    app_version: str = "0.1.0"
    request_id_header: str = "X-Request-ID"

    # v2 form: a class-level SettingsConfigDict, NOT an inner `class Config:`
    # (the latter silently does nothing in Pydantic v2).
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Cached singleton: read .env once at first call, reuse thereafter.
# Avoids re-parsing the file per request and guarantees consistent values.
@lru_cache
def get_settings() -> Settings:
    return Settings()
