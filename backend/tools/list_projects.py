"""Tool: list all projects the user has created.

Returns a list of project name strings, with "(active)" appended to the
currently active project so the spoken answer is immediately useful.
"""

from backend.memory import sqlite_store
from backend.tools import registry


@registry.register(
    name="list_projects",
    description=(
        "List all of the user's projects. "
        "Use this when the user asks what projects exist, "
        "e.g. 'what projects do I have?' or 'list my projects'. "
        "Call this rather than guessing."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def list_projects() -> list[str]:
    """Return project names, marking the active one with '(active)'."""
    rows = sqlite_store.list_projects()
    return [
        f"{r['name']} (active)" if r["is_active"] else r["name"]
        for r in rows
    ]
