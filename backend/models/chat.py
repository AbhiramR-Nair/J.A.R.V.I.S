# Pydantic models for chat request/response shapes.
# These define the API contract; FastAPI uses them for validation and OpenAPI docs.
# Fields marked Day-N comments are placeholders until that day's work fills them.

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    project_id: str | None = None  # None means "use active project" (Day 5)


class ChatResponse(BaseModel):
    reply: str
    request_id: str
