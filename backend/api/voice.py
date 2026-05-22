# Voice router: the static state probe plus the WebSocket the frontend listens on.
# Day 3 is a stub — the WS just accepts, says hello, and logs inbound frames.
# Day 7 attaches hotkey events, Day 11 the state machine, Day 16 amplitude data.

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from backend.models.voice import VoiceStateResponse

router = APIRouter(tags=["voice"])


@router.get("/voice-state", response_model=VoiceStateResponse)
async def voice_state() -> VoiceStateResponse:
    """Stub. Day 11 wires this to services/conversation.py state machine."""
    return VoiceStateResponse(state="idle")


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    """WebSocket for voice events. Day 7 attaches hotkey events,
    Day 11 attaches state-machine transitions, Day 16 attaches amplitude.
    For now: accept, send a 'connected' hello, log inbound messages, no broadcast."""
    await ws.accept()
    # WebSockets bypass the HTTP request-ID middleware, so we mint our own here and
    # bind it explicitly on each log line. No auth: backend binds to 127.0.0.1, so
    # the same-machine frontend is the only possible client in v1.
    rid = str(uuid.uuid4())
    logger.bind(request_id=rid).info("WS /ws/voice connected")
    try:
        await ws.send_json({"type": "connected", "request_id": rid})
        while True:
            try:
                msg = await ws.receive_json()
            except ValueError:
                # Malformed (non-JSON) frame — log and keep the connection alive
                # rather than letting the decode error tear down the handler.
                logger.bind(request_id=rid).warning("WS recv: malformed JSON frame, ignored")
                continue
            logger.bind(request_id=rid).debug(f"WS recv: {msg}")
    except WebSocketDisconnect:
        logger.bind(request_id=rid).info("WS /ws/voice disconnected")
