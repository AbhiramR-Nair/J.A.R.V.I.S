"""
Audio device management endpoints (Day 8).

GET  /audio/devices  — list all input-capable audio devices
GET  /audio/device   — return the currently saved input device (or null)
POST /audio/device   — persist a new device choice and rebuild the recorder

The settings panel UI (Day 17) will call these. For now, curl is sufficient.
"""

import sounddevice as sd
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from backend.config.runtime_settings import get_input_device, set_input_device
from backend.config.settings import get_settings
from backend.models.voice import DeviceInfo, SetDevicePayload
from backend.voice.audio import AudioRecorder

router = APIRouter(prefix="/audio", tags=["audio"])


def _input_devices() -> list[DeviceInfo]:
    """Return all devices that have at least one input channel."""
    devices = sd.query_devices()
    result = []
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            result.append(DeviceInfo(
                index=idx,
                name=dev["name"],
                channels=dev["max_input_channels"],
            ))
    return result


@router.get("/devices", response_model=list[DeviceInfo])
async def list_devices() -> list[DeviceInfo]:
    """Return all input-capable audio devices on this system."""
    return _input_devices()


@router.get("/device", response_model=DeviceInfo | None)
async def get_device() -> DeviceInfo | None:
    """Return the saved input device, or null if none has been chosen yet."""
    saved = get_input_device()
    if saved is None:
        return None
    return DeviceInfo(index=saved["index"], name=saved["name"], channels=0)


@router.post("/device", response_model=DeviceInfo)
async def set_device(payload: SetDevicePayload, request: Request) -> DeviceInfo:
    """
    Persist a new mic choice and hot-swap the AudioRecorder.

    Returns 409 if a recording is currently in progress (can't swap mid-capture).
    Returns 422 if the index doesn't exist or isn't an input device.
    """
    recorder: AudioRecorder = request.app.state.audio_recorder

    if recorder.is_recording:
        raise HTTPException(
            status_code=409,
            detail="Cannot change device while recording is in progress.",
        )

    # Validate the index against the live device list.
    inputs = _input_devices()
    match = next((d for d in inputs if d.index == payload.index), None)
    if match is None:
        raise HTTPException(
            status_code=422,
            detail=f"Device index {payload.index} is not a valid input device.",
        )

    # Persist the choice to data/settings.json.
    set_input_device(index=match.index, name=match.name)

    # Rebuild the recorder with the new device so the next PTT uses it.
    # The old recorder is not recording (checked above), so no cleanup needed.
    settings = get_settings()
    request.app.state.audio_recorder = AudioRecorder(
        sample_rate=settings.audio_sample_rate,
        channels=settings.audio_channels,
        dtype=settings.audio_dtype,
        device_index=match.index,
        max_seconds=settings.recording_max_seconds,
    )
    logger.info(f"audio device changed → index={match.index} name={match.name!r}")

    return match
