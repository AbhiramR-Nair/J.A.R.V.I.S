"""Tool: search the active project's memory for previously logged facts.

project_id MUST be passed to vector_store.search() — this is what enforces
cross-project isolation. Each project has its own named ChromaDB collection
("project_<id>"). Omitting project_id would query the wrong collection.

An empty result is not an error — return [] and let the LLM say it found nothing.
"""

from backend.config.settings import get_settings
from backend.memory import sqlite_store, vector_store
from backend.tools import registry


@registry.register(
    name="recall_from_project",
    description=(
        "Search the active project's memory for previously logged facts. "
        "Use this when the user asks what was said, concluded, or logged earlier, "
        "e.g. 'what did we say about X?', 'what did we conclude about X?', or 'recall X'. "
        "Call this to search memory rather than guessing from the current conversation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in the active project's memory.",
            },
        },
        "required": ["query"],
    },
)
async def recall_from_project(query: str) -> list[str]:
    """Return up to semantic_k relevant facts from the active project's ChromaDB collection."""
    project = sqlite_store.get_active_project()
    project_id: int = project["id"]

    # project_id scopes the search to this project's collection only.
    # vector_store.search already returns list[str] — no mapping needed.
    return await vector_store.search(
        query,
        project_id=project_id,
        k=get_settings().semantic_k,
    )
