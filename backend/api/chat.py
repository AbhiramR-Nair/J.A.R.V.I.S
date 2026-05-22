# Chat router. Day 3 is a stub: it echoes the message back with the request_id.
# Day 4 wires this to the LLM router (Gemini primary, OpenAI fallback);
# Day 5 persists the exchange to SQLite. No external call happens here yet.

from fastapi import APIRouter
from loguru import logger

from backend.config.logging import request_id_var
from backend.models.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Stub. Day 4 wires this to the LLM router. Day 5 saves to SQLite.
    For now: log the message, return a placeholder reply with the request_id."""
    # request_id_var is set by the HTTP middleware in main.py for this request.
    rid = request_id_var.get()
    logger.info(f"Chat stub received: {req.message[:60]}...")
    return ChatResponse(
        reply=f"(stub) I received: {req.message}",
        request_id=rid,
    )
