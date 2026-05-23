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

from backend.api import chat, health, memory, voice
from backend.api.voice import manager as ws_manager
from backend.config.logging import configure_logging, request_id_var
from backend.config.settings import get_settings
from backend.database.db import get_db
from backend.desktop import hotkeys

# Logging is set up once here (console + rotating file + request-ID patcher).
# Replaces the Day-2 inline loguru setup so the request_id_var actually threads through.
configure_logging()


async def _drain_events(queue: "asyncio.Queue[dict]") -> None:
    """Long-running task: reads hotkey events from the queue and broadcasts them
    to all connected WebSocket clients. Runs for the lifetime of the server."""
    while True:
        event = await queue.get()
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
    app.state.drainer_task = asyncio.create_task(_drain_events(event_queue))
    app.state.hotkey_queue = event_queue

    yield  # server runs here


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
