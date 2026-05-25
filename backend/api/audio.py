"""
Audio device management endpoints (Day 8).

GET  /audio/devices  — list all input-capable audio devices
GET  /audio/device   — return the currently saved input device (or null)
POST /audio/device   — persist a new device choice and rebuild the recorder

The settings panel UI (Day 17) will call these. For now, curl is sufficient.
"""

import time
import wave
from io import BytesIO

import numpy as np
import sounddevice as sd
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from backend.config.runtime_settings import get_input_device, set_input_device
from backend.config.settings import get_settings
from backend.models.voice import DeviceInfo, SetDevicePayload, TestMicResult
from backend.voice.audio import AudioCaptureError, AudioRecorder

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

    settings = get_settings()

    # Test-open the new device before touching the active recorder.
    # If this raises, the existing recorder is still live and PTT still works.
    try:
        AudioRecorder.test_open(
            device_index=match.index,
            sample_rate=settings.audio_sample_rate,
            channels=settings.audio_channels,
            dtype=settings.audio_dtype,
        )
    except AudioCaptureError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Persist the choice to data/settings.json.
    set_input_device(index=match.index, name=match.name)

    # Rebuild the recorder with the new device so the next PTT uses it.
    # The old recorder is not recording (checked above), so no stream to close.
    request.app.state.audio_recorder = AudioRecorder(
        sample_rate=settings.audio_sample_rate,
        channels=settings.audio_channels,
        dtype=settings.audio_dtype,
        device_index=match.index,
        max_seconds=settings.recording_max_seconds,
    )
    logger.info(f"audio device changed → index={match.index} name={match.name!r}")

    return match


def _run_mic_test(recorder: AudioRecorder, sample_rate: int, duration_s: float) -> TestMicResult:
    """Sync helper: record for duration_s, compute peak, play back. Runs in executor.

    Returns a TestMicResult. Never raises — errors are returned as success=False.
    """
    t_start = time.monotonic()

    try:
        recorder.start_recording()
    except AudioCaptureError as exc:
        return TestMicResult(success=False, peak_amplitude=0.0, duration_ms=0, error=str(exc))

    time.sleep(duration_s)

    try:
        wav_bytes = recorder.stop_recording()
    except AudioCaptureError as exc:
        return TestMicResult(
            success=False,
            peak_amplitude=0.0,
            duration_ms=int((time.monotonic() - t_start) * 1000),
            error=str(exc),
        )

    duration_ms = int((time.monotonic() - t_start) * 1000)

    if not wav_bytes:
        return TestMicResult(success=False, peak_amplitude=0.0, duration_ms=duration_ms, error="No audio captured.")

    # Decode WAV → numpy int16 → compute peak amplitude (normalised 0.0–1.0).
    buf = BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16)
    peak = float(np.max(np.abs(audio))) / 32768.0

    # Play back so the user can hear whether the capture sounded right.
    audio_float = audio.astype(np.float32) / 32768.0
    sd.play(audio_float, sample_rate)
    sd.wait()

    return TestMicResult(success=True, peak_amplitude=round(peak, 4), duration_ms=duration_ms)


@router.post("/test-mic", response_model=TestMicResult)
async def test_mic(request: Request) -> TestMicResult:
    """Record 3 seconds through the active mic, play it back, and report the peak amplitude.

    Rejects with 409 if a conversation turn is in progress (can't share the recorder).
    The response takes ~6 seconds (3s record + ~3s playback) — normal for this endpoint.
    """
    import asyncio

    from backend.models.voice import VoiceState

    conv = request.app.state.conversation
    if conv.state not in (VoiceState.IDLE, VoiceState.MUTED):
        raise HTTPException(
            status_code=409,
            detail="Cannot test mic while a conversation is active.",
        )

    recorder: AudioRecorder = request.app.state.audio_recorder
    settings = get_settings()

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, _run_mic_test, recorder, settings.audio_sample_rate, 3.0
    )

    logger.info(
        f"mic test: success={result.success} peak={result.peak_amplitude} "
        f"duration={result.duration_ms}ms"
    )
    return result
