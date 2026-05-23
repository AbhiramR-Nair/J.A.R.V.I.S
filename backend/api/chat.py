# Chat router. Wired to the LLM router (Gemini primary, Groq fallback) on Day 4.
# Day 5: persists every exchange to SQLite under the active project.

from fastapi import APIRouter, HTTPException
from loguru import logger

from backend.config.logging import request_id_var
from backend.llm.base import LLMError
from backend.llm.router import get_router
from backend.memory.sqlite_store import (
    get_active_project,
    get_or_create_session_conversation,
    save_message,
)
from backend.models.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Send a message to the LLM, persist the exchange, and return the reply.

    Flow: resolve active project → get/create today's conversation →
    save user message → call LLM → save assistant message → return response.
    """
    rid = request_id_var.get()
    logger.info(f"chat request: {req.message[:80]!r}")

    # Resolve the active project and today's conversation before calling the LLM.
    project = get_active_project()
    project_id: int = project["id"]
    project_name: str = project["name"]
    conversation_id = get_or_create_session_conversation(project_id)

    # Persist the user turn before the LLM call so it's recorded even if LLM fails.
    save_message(
        conversation_id=conversation_id,
        project_id=project_id,
        role="user",
        content=req.message,
    )

    try:
        result = await get_router().generate(req.message)
    except LLMError as e:
        logger.error(f"chat failed, all providers exhausted: {e}")
        raise HTTPException(status_code=503, detail="LLM unavailable, please try again.")

    # Persist the assistant reply with provider metadata.
    save_message(
        conversation_id=conversation_id,
        project_id=project_id,
        role="assistant",
        content=result.text,
        provider=result.provider,
        model=result.model,
    )

    return ChatResponse(
        reply=result.text,
        provider=result.provider,
        model=result.model,
        project_name=project_name,
        request_id=rid,
    )
