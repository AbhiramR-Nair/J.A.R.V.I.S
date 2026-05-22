# Chat router. Wired to the LLM router (Gemini primary, OpenAI fallback) on Day 4.
# Day 5 will persist the exchange to SQLite.

from fastapi import APIRouter, HTTPException
from loguru import logger

from backend.config.logging import request_id_var
from backend.llm.base import LLMError
from backend.llm.router import get_router
from backend.models.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Send a message to the LLM and return the reply.
    Gemini is tried first; OpenAI is the fallback on transient failures.
    Day 5 will persist the exchange to SQLite."""
    rid = request_id_var.get()
    logger.info(f"chat request: {req.message[:80]!r}")

    try:
        result = await get_router().generate(req.message)
    except LLMError as e:
        # All providers failed. Surface a human-readable message — no stack trace.
        logger.error(f"chat failed, all providers exhausted: {e}")
        raise HTTPException(status_code=503, detail="LLM unavailable, please try again.")

    return ChatResponse(
        reply=result.text,
        provider=result.provider,
        model=result.model,
        request_id=rid,
    )
