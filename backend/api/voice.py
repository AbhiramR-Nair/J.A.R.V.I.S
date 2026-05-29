# Voice router: static state probe + WebSocket that the frontend listens on.
# Day 3: stub WS that accepts, sends hello, logs inbound frames.
# Day 7: ConnectionManager for fan-out broadcast of hotkey events.
# Day 11: state machine wired in. Day 16: amplitude data added.

import uuid
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from loguru import logger

from backend.models.voice import SpeakRequest, SpeakResponse, VoiceStateResponse
from backend.voice.tts import TTSError

router = APIRouter(tags=["voice"])


# ConnectionManager holds all active WebSocket connections and lets any part
# of the backend broadcast a JSON event to all of them at once. In v1 there
# is only ever one client (the React app), but the fan-out pattern costs nothing
# and avoids special-casing a single-connection assumption throughout.
class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    async def broadcast(self, message: dict) -> None:
        # Iterate a snapshot copy: if a send fails we remove the dead connection
        # from the live set mid-loop without corrupting the iteration.
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception as exc:
                logger.warning(f"WS broadcast failed, dropping connection: {exc}")
                self.disconnect(ws)


# Module-level singleton imported by main.py to wire up the queue drainer.
manager = ConnectionManager()


@router.post("/speak", response_model=SpeakResponse)
async def speak(payload: SpeakRequest, request: Request) -> SpeakResponse:
    """Debug endpoint: synthesise and play text via TTS, blocking until playback ends.

    Synchronous (awaits full playback) so curl can confirm audio played before returning.
    Use this to isolate TTS issues from LLM/chat issues — if this works, the service is fine.
    Not used by the real voice loop; that goes through /chat (fire-and-forget) or Day 11's
    conversation state machine.
    """
    tts = request.app.state.tts_service
    try:
        result = await tts.speak(payload.text)
    except TTSError as exc:
        # TTSError messages are pre-sanitised in tts.py — safe to return verbatim.
        return JSONResponse(status_code=503, content={"error": str(exc)})
    except Exception as exc:
        logger.exception("unexpected error in POST /speak: {}", exc)
        return JSONResponse(status_code=500, content={"error": "Internal TTS error."})

    return SpeakResponse(latency_ms=result.latency_ms, num_samples=result.num_samples)


@router.get("/voice-state", response_model=VoiceStateResponse)
async def voice_state() -> VoiceStateResponse:
    """Stub. Day 11 wires this to services/conversation.py state machine."""
    return VoiceStateResponse(state="idle")


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    """Accept a WebSocket connection and hold it open for server-push events.
    Day 7: server pushes hotkey events (ptt_start, ptt_end, mute_toggle).
    Day 11: state-machine transitions added. Day 16: amplitude data added.
    Inbound client messages are logged but not yet acted on."""
    await manager.connect(ws)
    rid = str(uuid.uuid4())
    logger.bind(request_id=rid).info("WS /ws/voice connected")
    try:
        await ws.send_json({"type": "connected", "request_id": rid})
        # Push the current active project immediately so the UI chip initialises
        # without waiting for a REST fetch. Non-fatal if DB isn't ready yet.
        try:
            from backend.memory.sqlite_store import get_active_project
            active = get_active_project()
            await ws.send_json({"type": "project_changed", "name": active["name"]})
        except Exception:
            pass
        while True:
            try:
                msg = await ws.receive_json()
            except ValueError:
                logger.bind(request_id=rid).warning("WS recv: malformed JSON frame, ignored")
                continue
            logger.bind(request_id=rid).debug(f"WS recv: {msg}")

            # pdf_dropped: frontend sends this when the user drops a PDF onto the window.
            # Store it as the pending path and broadcast pdf_pending so the UI can show a cue.
            if msg.get("type") == "pdf_dropped":
                raw_path = msg.get("path", "").strip()
                if raw_path:
                    from backend.services.pending_drop import set_pending_pdf
                    set_pending_pdf(Path(raw_path))
                    await manager.broadcast({"type": "pdf_pending", "path": raw_path})
                    logger.bind(request_id=rid).info(f"pdf_dropped: stored pending path '{raw_path}'")
    except WebSocketDisconnect:
        manager.disconnect(ws)
        logger.bind(request_id=rid).info("WS /ws/voice disconnected")
