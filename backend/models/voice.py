# Pydantic models for voice request/response shapes.
# These define the API contract; FastAPI uses them for validation and OpenAPI docs.
# Fields marked Day-N comments are placeholders until that day's work fills them.

from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


# Day 10: TTS speak endpoint models
class SpeakRequest(BaseModel):
    # min_length=1 rejects empty strings; max_length=2000 prevents runaway synth jobs.
    # The validator below additionally rejects whitespace-only strings that would
    # pass the length check but produce near-silence from Piper.
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank or whitespace only")
        return v


class SpeakResponse(BaseModel):
    latency_ms: float
    num_samples: int
