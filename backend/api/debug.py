"""
Debug endpoints. Not part of the public API surface — used for inspecting
internal state during development. Disable or gate behind a settings flag
before shipping to anything multi-user.
"""
from fastapi import APIRouter

from backend.prompts.system_prompts import JARVIS_SYSTEM_PROMPT


router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/system-prompt")
async def get_system_prompt() -> dict:
    """Return the currently-assembled JARVIS system prompt."""
    return {
        "length_chars": len(JARVIS_SYSTEM_PROMPT),
        "prompt": JARVIS_SYSTEM_PROMPT,
    }
