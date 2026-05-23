# Chat router. Wired to the LLM router (Gemini primary, Groq fallback) on Day 4.
# Day 5: persists every exchange to SQLite under the active project.
# Day 6: retrieves relevant memories before LLM call; stores important exchanges after.
# Day 10: fires TTS as a background task after returning the HTTP response.

import asyncio

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from backend.config.logging import request_id_var
from backend.config.settings import get_settings
from backend.llm.base import LLMError
from backend.llm.router import get_router
from backend.memory import importance, vector_store
from backend.memory.sqlite_store import (
    get_active_project,
    get_or_create_session_conversation,
    save_memory,
    save_message,
)
from backend.models.chat import ChatRequest, ChatResponse
from backend.voice.tts import TTSError, TTSService

router = APIRouter(prefix="/chat", tags=["chat"])


async def _speak_safely(tts: TTSService, text: str) -> None:
    """Fire-and-forget wrapper around tts.speak().

    Called via asyncio.create_task() so it runs concurrently with — and after —
    the HTTP response being sent. Errors are logged but never re-raised; by the
    time this runs there is no open HTTP response to attach an error to.
    Day 11's state machine will broadcast speaking_failed over WebSocket instead.
    """
    try:
        result = await tts.speak(text)
        logger.info("chat tts ok: latency={:.0f}ms samples={}", result.latency_ms, result.num_samples)
    except TTSError as exc:
        logger.warning("chat tts failed (non-fatal): {}", exc)
    except Exception as exc:
        logger.exception("chat tts unexpected error: {}", exc)


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    """Send a message to the LLM, persist the exchange, and return the reply.

    Flow: resolve active project → retrieve relevant memories → inject into
    prompt → save user message → call LLM → save assistant message →
    score importance → conditionally store memory → schedule TTS → return response.
    """
    rid = request_id_var.get()
    settings = get_settings()
    logger.info(f"chat request: {req.message[:80]!r}")

    # Resolve the active project and today's conversation before calling the LLM.
    project = get_active_project()
    project_id: int = project["id"]
    project_name: str = project["name"]
    conversation_id = get_or_create_session_conversation(project_id)

    # --- Pre-LLM: retrieve relevant memories and inject into the prompt -------
    # Failure here is non-fatal: we log and proceed without context rather than
    # returning an error to the user for a memory subsystem issue.
    try:
        memories = await vector_store.search(req.message, project_id, k=3)
    except Exception as e:
        logger.warning(f"vector search failed, proceeding without memory: {e}")
        memories = []

    if memories:
        memory_block = "\n".join(f"- {m}" for m in memories)
        augmented_message = (
            f"[Relevant context from past conversations:]\n{memory_block}\n\n"
            f"[User's current message:]\n{req.message}"
        )
        logger.debug(f"injected {len(memories)} memories into prompt")
    else:
        augmented_message = req.message

    # Persist the original user message (not the augmented version — keep DB clean).
    save_message(
        conversation_id=conversation_id,
        project_id=project_id,
        role="user",
        content=req.message,
    )

    try:
        result = await get_router().generate(augmented_message)
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

    # --- Post-LLM: score importance and conditionally store memory ------------
    # Each step is wrapped independently: a failure at any point logs and moves on.
    # Memory storage must never propagate an error back to the chat response.
    try:
        combined_text = f"User: {req.message}\nAssistant: {result.text}"
        importance_val = await importance.score(combined_text)

        if importance_val >= settings.importance_threshold:
            chroma_id = await vector_store.add(
                combined_text,
                project_id,
                metadata={"importance": importance_val, "role": "exchange"},
            )
            save_memory(combined_text, project_id, importance_val, chroma_id)
            logger.info(f"memory stored: importance={importance_val}, chroma_id={chroma_id[:8]}")
        else:
            logger.debug(f"memory skipped: importance={importance_val} < threshold={settings.importance_threshold}")
    except Exception as e:
        logger.error(f"memory storage failed (non-fatal): {e}")

    # Schedule TTS as a fire-and-forget background task. asyncio.create_task() puts it
    # on the event loop and returns immediately — the HTTP response goes back to the
    # caller without waiting for audio to finish playing. If TTS fails, _speak_safely
    # logs it; the chat response is unaffected. Day 11 adds speaking_failed WS events.
    asyncio.create_task(_speak_safely(request.app.state.tts_service, result.text))

    return ChatResponse(
        reply=result.text,
        provider=result.provider,
        model=result.model,
        project_name=project_name,
        request_id=rid,
    )
