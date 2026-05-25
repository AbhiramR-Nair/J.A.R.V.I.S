# Pydantic models for voice request/response shapes.
# These define the API contract; FastAPI uses them for validation and OpenAPI docs.
# Fields marked Day-N comments are placeholders until that day's work fills them.

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

VoiceStateLiteral = Literal[
    "idle", "listening", "transcribing", "thinking", "speaking", "muted", "error"
]


# String enum so values serialise as plain strings in JSON (e.g. "idle" not "VoiceState.IDLE").
# Used by ConversationOrchestrator; the Literal above is used by Pydantic field declarations.
class VoiceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MUTED = "muted"
    ERROR = "error"


class VoiceStateResponse(BaseModel):
    state: VoiceStateLiteral
    message: str | None = None  # human-readable note, mainly for "error"


# ---------------------------------------------------------------------------
# Day 11 — WebSocket event models (backend → frontend)
# ---------------------------------------------------------------------------

# Broadcast on every state transition. Drives the UI label and (Week 3) blob animation.
class StateChangedEvent(BaseModel):
    type: Literal["state_changed"] = "state_changed"
    state: VoiceStateLiteral
    prev_state: VoiceStateLiteral


# Sent once the LLM finishes generating. Frontend adds the text bubble immediately;
# audio follows separately via speaking_started / speaking_ended.
class AssistantMessageEvent(BaseModel):
    type: Literal["assistant_message"] = "assistant_message"
    text: str
    turn_id: str


# Sent just before tts.speak() is called so the blob can animate on the first frame.
class SpeakingStartedEvent(BaseModel):
    type: Literal["speaking_started"] = "speaking_started"
    turn_id: str


# Sent after tts.speak() returns cleanly; triggers the SPEAKING → IDLE transition.
class SpeakingEndedEvent(BaseModel):
    type: Literal["speaking_ended"] = "speaking_ended"
    turn_id: str


# Mirror of transcription_failed — broadcast when TTS errors so the UI can show a toast.
class SpeakingFailedEvent(BaseModel):
    type: Literal["speaking_failed"] = "speaking_failed"
    reason: str
    turn_id: str


# Day 12: broadcast when the 30s PTT cap fires so the UI can warn the user.
class RecordingCapHitEvent(BaseModel):
    type: Literal["recording_cap_hit"] = "recording_cap_hit"


# Day 12: broadcast when the orchestrator successfully rebuilds against the default device.
class AudioDeviceRecoveredEvent(BaseModel):
    type: Literal["audio_device_recovered"] = "audio_device_recovered"


# Day 12: response model for POST /audio/test-mic.
class TestMicResult(BaseModel):
    success: bool
    peak_amplitude: float   # normalised 0.0–1.0; < 0.05 suggests silent/broken mic
    duration_ms: int
    error: str | None = None


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
