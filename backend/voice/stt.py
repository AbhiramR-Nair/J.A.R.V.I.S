"""Cloud STT via Groq Whisper-large-v3.

No local fallback in v1 per Version_1_plan.md — on Groq failure the dispatcher
emits a transcription_failed event with a user-readable message.
"""

import asyncio
from pathlib import Path
from time import perf_counter

import httpx
from groq import AsyncGroq, GroqError
from loguru import logger
from pydantic import BaseModel


class TranscriptionResult(BaseModel):
    """Typed result returned by STTService.transcribe.

    Pydantic model so it serialises directly onto the WebSocket payload in the dispatcher.
    """

    text: str
    latency_ms: float
    model: str
    language: str | None


class STTError(Exception):
    """Raised when transcription cannot be completed.

    The message is pre-sanitised for display in the UI — no raw tracebacks or
    API internals. Dispatcher catches this type and broadcasts transcription_failed.
    """


class STTService:
    """Wrapper around Groq's async transcription API.

    One AsyncGroq client is built in __init__ and reused for every call.
    Building a new client per transcription would force a new TLS handshake
    each time (~100 ms overhead) — not acceptable for a real-time voice loop.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        language: str | None,
        temperature: float,
        timeout_seconds: float,
    ) -> None:
        self._model = model
        self._language = language
        self._temperature = temperature
        # Timeout is passed to the SDK so it enforces it at the HTTP transport level.
        self._client = AsyncGroq(api_key=api_key, timeout=timeout_seconds)
        logger.info("stt service initialized: model={}", model)

    async def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Send audio_path to Groq Whisper and return a typed TranscriptionResult.

        Latency is logged on both success and failure so 'Groq is slow' can be
        distinguished from 'Groq is failing' when triaging issues.

        Raises:
            STTError: on any network, auth, timeout, or empty-audio failure.
                      Message is safe to display directly in the UI.
        """
        # Bind the WAV filename as a synthetic request ID so related log lines
        # are grouped when multiple transcriptions appear in the log.
        bound = logger.bind(req_id=audio_path.stem)
        bound.info("transcribing: {}", audio_path)

        start = perf_counter()
        try:
            with audio_path.open("rb") as fh:
                # Groq expects a (filename, file_obj, content_type) tuple for `file`.
                # The filename is metadata only; the WAV bytes in fh are what matter.
                response = await self._client.audio.transcriptions.create(
                    file=(audio_path.name, fh, "audio/wav"),
                    model=self._model,
                    response_format="json",
                    language=self._language,
                    temperature=self._temperature,
                )
        except (GroqError, httpx.TimeoutException, asyncio.TimeoutError) as exc:
            # GroqError is the base for all SDK exceptions: auth errors, rate limits,
            # connection errors, and API-level timeouts all inherit from it.
            # httpx.TimeoutException catches any transport-level timeout that leaks through.
            latency_ms = (perf_counter() - start) * 1000
            bound.warning("stt failed after {:.0f}ms: {}", latency_ms, exc)
            raise STTError("I couldn't hear that — try again.") from exc

        latency_ms = (perf_counter() - start) * 1000
        text = response.text.strip()

        # Empty audio (mis-press or pure silence) returns an empty string from Whisper.
        # Surface this as a distinct, friendlier message rather than an empty transcript.
        if not text:
            bound.warning("stt returned empty transcript ({:.0f}ms)", latency_ms)
            raise STTError("I didn't hear anything.")

        bound.info("stt ok: '{}...' ({:.0f}ms, model={})", text[:60], latency_ms, self._model)
        return TranscriptionResult(
            text=text,
            latency_ms=latency_ms,
            model=self._model,
            language=self._language,
        )

    async def close(self) -> None:
        """Release the underlying httpx client. Called from the FastAPI lifespan shutdown."""
        await self._client.close()
        logger.info("stt service closed")
