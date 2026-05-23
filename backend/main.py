"""
FastAPI entry point for research-jarvis.
Day 2: single /health endpoint so the boot test passes.
Day 3 adds /chat, /memory, /voice-state, and WebSocket.
Day 7 adds hotkey listener startup and the asyncio queue drainer.
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.api import audio, chat, health, memory, voice
from backend.api.voice import manager as ws_manager
from backend.config.logging import configure_logging, request_id_var
from backend.config.runtime_settings import get_input_device
from backend.config.settings import get_settings
from backend.database.db import get_db
from backend.desktop import hotkeys
from backend.voice.audio import AudioRecorder
from backend.voice.stt import STTError, STTService
from backend.voice.tts import TTSService

# Logging is set up once here (console + rotating file + request-ID patcher).
# Replaces the Day-2 inline loguru setup so the request_id_var actually threads through.
configure_logging()


async def _save_recording(wav_bytes: bytes) -> Path:
    """Write WAV bytes to data/recordings/{iso8601}.wav and return the path."""
    recordings_dir = get_settings().recordings_dir
    recordings_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = recordings_dir / f"{timestamp}.wav"
    path.write_bytes(wav_bytes)
    logger.info(f"recording saved: {path}")
    return path


async def _handle_event_side_effects(
    app: "FastAPI", event: dict, queue: "asyncio.Queue[dict]"
) -> None:
    """
    Branch on event type and drive audio start/stop as side-effects.

    Runs inside _dispatch_events before the WebSocket broadcast so the UI
    always gets the event even if audio fails.

    sounddevice start/stop are sync and potentially blocking, so they run in
    a threadpool executor rather than directly on the asyncio event loop.
    """
    # Drop events that arrive before lifespan finishes wiring everything up.
    if not getattr(app.state, "ready", False):
        return

    recorder = app.state.audio_recorder
    loop = asyncio.get_running_loop()
    etype = event.get("type")

    if etype == "ptt_start":
        # run_in_executor hands the blocking call to a threadpool thread so the
        # event loop stays free to handle WebSocket messages during recording.
        await loop.run_in_executor(None, recorder.start_recording)

    elif etype == "ptt_end":
        wav_bytes = await loop.run_in_executor(None, recorder.stop_recording)
        if wav_bytes:
            path = await _save_recording(wav_bytes)
            # Inject recording_saved into the queue so the STT branch picks it up.
            await queue.put({"type": "recording_saved", "path": str(path)})
        else:
            # Tap too fast, or recording never started before release.
            # Give explicit feedback so the user knows the press was too short.
            await ws_manager.broadcast({"type": "transcription_failed", "error": "I didn't hear anything."})

    elif etype == "recording_saved":
        # Day-8 hand-off: recording_saved is now intercepted here to trigger STT.
        # The event still flows through to broadcast() below so the UI filename badge
        # keeps working as a debug signal during Week 2.
        path = Path(event["path"])
        await ws_manager.broadcast({"type": "transcribing", "path": str(path)})

        try:
            result = await app.state.stt_service.transcribe(path)
        except STTError as exc:
            # STTError messages are pre-sanitised in stt.py — safe to send to the UI verbatim.
            await ws_manager.broadcast({
                "type": "transcription_failed",
                "error": str(exc),
            })
            return

        await ws_manager.broadcast({
            "type": "transcription_complete",
            "text": result.text,
            "latency_ms": result.latency_ms,
        })
        # TODO Day 10: add elif etype == "chat_response" branch — TTS speaks the text.
        # TODO Day 11: replace echo with real LLM call via services/conversation.py.

    elif etype == "mute_toggle":
        # Muting mid-recording aborts cleanly — discard the partial audio.
        if recorder.is_recording:
            await loop.run_in_executor(None, recorder.stop_recording)
            logger.info("audio recorder: aborted by mute toggle")


async def _dispatch_events(
    app: "FastAPI", queue: "asyncio.Queue[dict]"
) -> None:
    """
    Single consumer of the hotkey event queue. Runs for the server lifetime.

    For each event:
      1. Side-effects (audio start/stop; Day 11 will add conversation state)
      2. Broadcast to all WebSocket clients

    Side-effect failures are caught and logged so a broken audio path never
    silences the UI — the badge still updates even if the mic dies.
    """
    while True:
        event = await queue.get()
        try:
            await _handle_event_side_effects(app, event, queue)
        except Exception as exc:
            logger.exception(f"dispatcher side-effect error: {exc}")
        # recording_saved is consumed by the STT branch, which broadcasts transcribing +
        # transcription_complete/failed. Re-broadcasting it here would arrive milliseconds
        # after transcription_failed and cause React to batch-drop the error toast state update.
        if event.get("type") != "recording_saved":
            await ws_manager.broadcast(event)


# lifespan replaces the deprecated @app.on_event("startup") pattern.
# Code before `yield` runs at startup; code after `yield` runs at shutdown.
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB on boot so the schema and seed run before any request arrives.
    get_db()

    # Hotkey listener setup (Day 7).
    # Order matters: init() must run before start_listener() so the queue and loop
    # references are in place before the first keypress could arrive.
    event_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()
    hotkeys.init(loop=loop, queue=event_queue)
    hotkeys.start_listener()

    # Store the drainer task on app.state to prevent garbage collection.
    # asyncio.create_task() returns a Task object; if nothing holds a reference
    # to it, the GC can silently cancel it — the task disappears after a few events.
    app.state.drainer_task = asyncio.create_task(_dispatch_events(app, event_queue))
    app.state.hotkey_queue = event_queue

    # Build the AudioRecorder with the user's saved device, or None for system default.
    # Device choice is persisted in data/settings.json via runtime_settings.
    device = get_input_device()
    app.state.audio_recorder = AudioRecorder(
        sample_rate=get_settings().audio_sample_rate,
        channels=get_settings().audio_channels,
        dtype=get_settings().audio_dtype,
        device_index=device["index"] if device else None,
        max_seconds=get_settings().recording_max_seconds,
    )

    # Build the STTService with one persistent AsyncGroq client.
    # Constructed after audio_recorder so shutdown can mirror LIFO order.
    s = get_settings()
    app.state.stt_service = STTService(
        api_key=s.groq_api_key,
        model=s.stt_model,
        language=s.stt_language,
        temperature=s.stt_temperature,
        timeout_seconds=s.stt_timeout_seconds,
    )

    # Build TTSService after STTService so shutdown closes in reverse order: tts → stt → recorder.
    app.state.tts_service = TTSService(s)

    # ready flag: gate on all three subsystems being fully constructed.
    # An early PTT press before this line cannot trigger a half-built service.
    app.state.ready = True

    yield  # server runs here

    # Shutdown LIFO: tts → stt → recorder (reverse of construction order).
    await app.state.tts_service.close()
    await app.state.stt_service.close()
    if app.state.audio_recorder.is_recording:
        app.state.audio_recorder.stop_recording()
        logger.info("audio recorder: stopped on shutdown")


app = FastAPI(title="research-jarvis", version="0.1.0", lifespan=lifespan)

# CORS, tightened from the Day-2 wildcard to an explicit allow-list.
# expose_headers is the key bit: without it the browser hides X-Request-ID from
# JS, so the frontend (Task 6) couldn't read the ID off a fetch response.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_dev_url],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-Request-ID"],
    allow_credentials=False,
)


# This middleware runs once per HTTP request. It does three jobs:
#   1) Mint a UUID and stash it in the ContextVar so every log inside this
#      request automatically carries it.
#   2) Time the request (rough perf log).
#   3) Echo the ID back as X-Request-ID so the frontend can correlate.
# WebSocket connections don't go through HTTP middleware — they get their own
# request_id set inside the ws handler.
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Generate a request ID, bind it to logs via ContextVar, time the request,
    attach the ID as a response header. Logs one line at start, one at end."""
    rid = str(uuid.uuid4())
    # reset(token) in finally guarantees the ID is cleared even on error, so it
    # can't leak into the next request handled by a reused worker.
    token = request_id_var.set(rid)
    start = time.perf_counter()
    logger.info(f"→ {request.method} {request.url.path}")
    try:
        response = await call_next(request)
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(f"← {request.method} {request.url.path} done in {elapsed_ms:.1f}ms")
        request_id_var.reset(token)
    response.headers[get_settings().request_id_header] = rid
    return response


# Mount the per-concern routers. One file per concern (health/chat/memory/voice)
# matches the architecture skill and keeps this entry point thin.
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(voice.router)
app.include_router(audio.router)
