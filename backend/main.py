"""
FastAPI entry point for research-jarvis.
Day 2: single /health endpoint so the boot test passes.
Day 3 adds /chat, /memory, /voice-state, and WebSocket.
"""

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

# Remove default loguru handler and add one that includes timestamps + level colour.
logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")

app = FastAPI(title="research-jarvis", version="0.1.0")

# Allow the Vite dev server (port 5173) to call the API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    logger.info("health check")
    return {"status": "ok"}
