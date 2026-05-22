"""
FastAPI entry point for research-jarvis.
Day 2: single /health endpoint so the boot test passes.
Day 3 adds /chat, /memory, /voice-state, and WebSocket.
"""

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.api import chat, health, memory, voice
from backend.config.logging import configure_logging, request_id_var
from backend.config.settings import get_settings

# Logging is set up once here (console + rotating file + request-ID patcher).
# Replaces the Day-2 inline loguru setup so the request_id_var actually threads through.
configure_logging()

app = FastAPI(title="research-jarvis", version="0.1.0")

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
