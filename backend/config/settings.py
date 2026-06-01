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
    # Free-tier quota is per-model-per-day. When a model hits 429, switch to another.
    # gemini-flash-lite-latest: works when 2.0-flash/2.5-flash quotas are exhausted.
    # Switch back to gemini-2.0-flash once quota resets or billing is enabled.
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_model_heavy: str = "gemini-2.5-flash"
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
    # Set USE_LLM_IMPORTANCE_SCORER=true in .env to restore the LLM path for A/B testing.
    # Default False: heuristic scorer runs instead, saving one LLM call per turn.
    use_llm_importance_scorer: bool = False

    # Audio capture (Day 8)
    # These values are locked to what Groq Whisper and openWakeWord both require.
    # Do not change sample_rate or dtype without checking downstream consumers.
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_dtype: str = "int16"
    audio_chunk_ms: int = 50            # sounddevice callback granularity in ms
    recording_max_seconds: int = 30     # PTT hard cap; auto-stops to protect memory
    recordings_dir: Path = Path("data/recordings")
    recordings_max_age_days: int = 14    # WAV files older than this are deleted on each save
    audio_open_timeout_seconds: float = 2.0  # reserved for device open validation
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

    # TTS — Piper (Day 10)
    # piper_binary_path: extracted zip lands at piper/piper/piper.exe on this machine.
    # tts_sample_rate: read from en_US-lessac-medium.onnx.json; must match the voice
    #   file — a mismatch produces chipmunk/slow-mo audio with no other error.
    # tts_output_device=None means sounddevice uses the system default output device.
    # tts_timeout_seconds: hard ceiling on Piper subprocess; long replies on a slow CPU
    #   can take several seconds to synth — 30s is a generous but sane upper bound.
    piper_binary_path: Path = Path("piper/piper/piper.exe")
    piper_voice_path: Path = Path("piper_voices/en_GB-alan-medium.onnx")
    tts_sample_rate: int = 22050
    tts_output_device: int | None = None
    tts_timeout_seconds: int = 30

    # Amplitude broadcasting (Day 16)
    # mic_calibration_max: RMS in normalized [-1, 1] float space that maps to amplitude 1.0.
    # Normal laptop mic speech ≈ 0.05–0.12 RMS; 0.1 saturates at loud-but-normal volume.
    # tts_calibration_max: Piper output runs hotter than mic; 0.3 keeps it in range.
    # Tune these if the orb barely reacts (lower) or is always pinned at max (raise).
    mic_calibration_max: float = 0.1
    tts_calibration_max: float = 0.3
    amplitude_broadcast_hz: int = 20     # how often the orchestrator broadcasts amplitude to the frontend

    # Conversation orchestrator (Day 11)
    error_recovery_seconds: int = 3      # how long ERROR state waits before auto-recovering to IDLE
    recent_messages_limit: int = 4       # last N messages used as recency context for LLM
    semantic_k: int = 3                  # top-k ChromaDB results for semantic context
    context_char_cap: int = 6000         # crude char cap on injected context (~1500 tokens)

    # Tool-calling (Day 20)
    max_tool_calls: int = 5              # safety cap on tool-call loop iterations (Q8)

    # Summarizer (Day 23)
    # summarizer_direct_threshold: papers shorter than this skip map-reduce (single call).
    # summarizer_chunk_max_concurrent: Semaphore cap — 3 in-flight chunk calls at a time.
    #   Prevents bursting Gemini's per-minute rate limit on 20+ chunk papers.
    # summarizer_chunk_summary_target: target char length for each intermediate summary.
    # summarizer_model: must be the heavy model — JSON mode quality matters here.
    summarizer_direct_threshold: int = 12000
    summarizer_chunk_max_concurrent: int = 3
    summarizer_chunk_summary_target: int = 400
    summarizer_model: str = "gemini-2.5-flash"  # restored after Day 24 quota reset
    summarizer_chunk_model: str = "gemini-flash-lite-latest"  # map stage (plain text, high RPM)

    # Web search (Day 25)
    # tavily_search_depth: "advanced" gives better research results; "basic" is faster/cheaper.
    # tavily_include_answer: Tavily pre-synthesises an answer, reducing LLM token load.
    # web_search_content_cap: max chars of each result's content passed to the LLM.
    # grounding_model: Gemini model for Google-Search-grounded calls (T-3, if built).
    tavily_search_depth: str = "advanced"
    tavily_include_answer: bool = True
    web_search_content_cap: int = 800
    grounding_model: str = "gemini-flash-lite-latest"

    # App launcher + timer (Day 26)
    timer_max_minutes: float = 1440.0          # reject timers > 24h
    notification_timeout_seconds: int = 10     # how long the plyer toast stays visible
    timer_announce_on_idle: bool = True        # speak completion only when voice loop is IDLE

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
