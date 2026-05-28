"""First real tool — returns the current local time.

Validates the full tool stack end-to-end: LLM → tool_call → tool_result → LLM → TTS.
Chosen because datetime.now() cannot fail meaningfully, so any failure in the loop
is a wiring bug, not a tool bug — clean signal for Day 20 smoke testing.
"""

from datetime import datetime

from backend.tools import registry


@registry.register(
    name="get_current_time",
    description=(
        "Get the current local date and time. "
        "Use this when the user asks what time it is, what day it is, "
        "or what the current date is. Do not guess — always call this tool."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def get_current_time() -> str:
    """Return the current local time as an ISO-8601 string."""
    return datetime.now().isoformat(timespec="seconds")
