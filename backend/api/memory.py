# Memory router. Day 3 is a stub returning an empty list.
# Day 5 wires SQLite (project-scoped reads/writes); Day 6 adds the Chroma vector store.

from fastapi import APIRouter

from backend.models.memory import MemoryListResponse

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=MemoryListResponse)
async def list_memory() -> MemoryListResponse:
    """Stub. Day 5 wires SQLite, Day 6 wires Chroma."""
    return MemoryListResponse(items=[])
