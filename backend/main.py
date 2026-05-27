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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.api import audio, chat, debug, health, memory, projects, voice, window
from backend.api.voice import manager as ws_manager
from backend.config.logging import configure_logging, request_id_var
from backend.config.runtime_settings import get_input_device
from backend.config.settings import get_settings
from backend.database.db import get_db
from backend.desktop import hotkeys
from backend.llm.router import get_router
from backend.services.conversation import ConversationOrchestrator
from backend.voice.audio import AudioRecorder
from backend.voice.stt import STTService
from backend.voice.tts import TTSService

# Logging is set up once here (console + rotating file + request-ID patcher).
# Replaces the Day-2 inline loguru setup so the request_id_var actually threads through.
configure_logging()


async def _handle_event_side_effects(app: "FastAPI", event: dict) -> None:
    """Route PTT/mute hotkey events to the conversation orchestrator.

    Thin routing layer — all audio, STT, LLM, and TTS logic now lives in
    ConversationOrchestrator. Guard against events that arrive in the ~3s
    window before lifespan finishes constructing all subsystems.
    """
    if not getattr(app.state, "ready", False):
        return
    # Defensive: orchestrator is constructed after ready=True, but guard anyway.
    if not hasattr(app.state, "conversation"):
        return

    etype = event.get("type")
    conv = app.state.conversation

    if etype == "ptt_start":
        await conv.on_ptt_start()
    elif etype == "ptt_end":
        await conv.on_ptt_end()
    elif etype == "mute_toggle":
        await conv.on_mute_toggle()
    elif etype == "recording_cap_hit":
        await conv.on_recording_cap_hit()


async def _dispatch_events(
    app: "FastAPI", queue: "asyncio.Queue[dict]"
) -> None:
    """Single consumer of the hotkey event queue. Runs for the server lifetime.

    For each event:
      1. Route to the conversation orchestrator (side-effects live there now)
      2. Broadcast to all WebSocket clients so the frontend also sees raw events

    Side-effect failures are caught and logged so a broken orchestrator path
    never silences the UI — the WebSocket badge still updates.
    """
    while True:
        event = await queue.get()
        try:
            await _handle_event_side_effects(app, event)
        except Exception as exc:
            logger.exception(f"dispatcher side-effect error: {exc}")
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
    # notify_cap_hit bridges the PortAudio callback thread into the asyncio event queue
    # via loop.call_soon_threadsafe — the only safe way to enqueue from a non-async thread.
    device = get_input_device()
    def _on_cap_hit() -> None:
        loop.call_soon_threadsafe(
            event_queue.put_nowait, {"type": "recording_cap_hit"}
        )

    app.state.audio_recorder = AudioRecorder(
        sample_rate=get_settings().audio_sample_rate,
        channels=get_settings().audio_channels,
        dtype=get_settings().audio_dtype,
        device_index=device["index"] if device else None,
        max_seconds=get_settings().recording_max_seconds,
        notify_cap_hit=_on_cap_hit,
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

    # Build the ConversationOrchestrator last; it depends on all three services above.
    # broadcast is ws_manager.broadcast so the orchestrator can push events to the React app.
    app.state.conversation = ConversationOrchestrator(
        recorder=app.state.audio_recorder,
        stt=app.state.stt_service,
        llm=get_router(),
        tts=app.state.tts_service,
        broadcast=ws_manager.broadcast,
        settings=s,
    )

    # ready flag: gate on all four subsystems being fully constructed.
    # An early PTT press before this line cannot trigger a half-built service.
    app.state.ready = True

    yield  # server runs here

    # Shutdown LIFO: conversation → tts → stt → recorder (reverse of construction order).
    await app.state.conversation.close()
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
app.include_router(projects.router)
app.include_router(window.router)
app.include_router(debug.router)
