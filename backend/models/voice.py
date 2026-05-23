# Pydantic models for voice request/response shapes.
# These define the API contract; FastAPI uses them for validation and OpenAPI docs.
# Fields marked Day-N comments are placeholders until that day's work fills them.

from typing import Literal

from pydantic import BaseModel

VoiceStateLiteral = Literal[
    "idle", "listening", "transcribing", "thinking", "speaking", "muted", "error"
]


class VoiceStateResponse(BaseModel):
    state: VoiceStateLiteral
    message: str | None = None  # human-readable note, mainly for "error"


# Day 8: audio device models
class DeviceInfo(BaseModel):
    index: int
    name: str
    channels: int


class SetDevicePayload(BaseModel):
    index: int
