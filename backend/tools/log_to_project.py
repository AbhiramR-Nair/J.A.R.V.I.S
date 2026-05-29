"""Tool: save a note to the active project's long-term memory.

Writes to both stores so the fact is both SQL-queryable and semantically
searchable:
  1. ChromaDB (vector_store.add) — enables recall_from_project to find it later.
  2. SQLite  (sqlite_store.save_memory) — durable fallback; chroma_id=None if
     the vector store step failed, so the text is never silently lost.

Importance is hard-coded to 10 — a deliberately logged note is always worth
keeping. The importance scorer (importance.py) is only for auto-stored turns.
"""

from loguru import logger

from backend.memory import sqlite_store, vector_store
from backend.tools import registry


@registry.register(
    name="log_to_project",
    description=(
        "Save a note to the active project's long-term memory. "
        "Use this when the user says 'log this: …', 'note that …', "
        "'remember that …', or 'save this'. "
        "Pass the fact verbatim as 'content'. "
        "Always call this — never just say you've noted it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The note or fact to save to the active project's memory, verbatim.",
            },
        },
        "required": ["content"],
    },
)
async def log_to_project(content: str) -> str:
    """Write content to both ChromaDB and SQLite under the active project."""
    project = sqlite_store.get_active_project()
    project_id: int = project["id"]

    # Step 1: embed and store in ChromaDB. Treat failure as non-fatal — the
    # SQLite row still lands so no text is silently dropped.
    chroma_id: str | None = None
    try:
        chroma_id = await vector_store.add(
            content,
            project_id=project_id,
            metadata={"importance": 10, "source": "log_to_project"},
        )
    except Exception as exc:
        logger.warning(f"log_to_project: ChromaDB write failed, SQLite-only: {exc}")

    # Step 2: always write to SQLite. chroma_id is None if step 1 failed.
    sqlite_store.save_memory(
        text=content,
        project_id=project_id,
        importance=10,
        chroma_id=chroma_id,
    )

    return f"Logged to project '{project['name']}'."
