# Pydantic models for memory request/response shapes.
# These define the API contract; FastAPI uses them for validation and OpenAPI docs.
# Fields marked Day-N comments are placeholders until that day's work fills them.

from datetime import datetime

from pydantic import BaseModel


class MemoryItem(BaseModel):
    id: str
    content: str
    project_id: str
    importance: int  # 1-10 (Day 6) — range not enforced yet
    created_at: datetime


class MemoryListResponse(BaseModel):
    items: list[MemoryItem]
