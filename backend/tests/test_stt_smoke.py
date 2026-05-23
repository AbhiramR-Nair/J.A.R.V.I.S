"""
Manual smoke test for STTService — NOT a pytest test, run directly.

Usage:
    python -m backend.tests.test_stt_smoke

What it does:
    1. Records 3 seconds of audio (reuses AudioRecorder from Day 8)
    2. Saves to data/recordings/stt_smoke.wav
    3. Calls STTService.transcribe() against Groq Whisper-large-v3
    4. Prints the transcript and latency
    5. Exits 0 on success, 1 on failure

Requires GROQ_API_KEY to be set in .env.
Say something clearly during the 3-second window — technical terms like
"ABL1 kinase T315I mutation" are a good test of Whisper accuracy.
"""

import asyncio
import sys
from pathlib import Path

from backend.config.runtime_settings import get_input_device
from backend.config.settings import get_settings
from backend.voice.audio import AudioRecorder
from backend.voice.stt import STTError, STTService


async def main() -> None:
    settings = get_settings()

    if not settings.groq_api_key:
        print("FAIL — GROQ_API_KEY is not set in .env")
        sys.exit(1)

    # --- Step 1: record 3 seconds of audio ---
    device = get_input_device()
    device_index = device["index"] if device else None
    device_label = device["name"] if device else "system default"
    print(f"Recording 3s from '{device_label}' — say something now!")

    recorder = AudioRecorder(
        sample_rate=settings.audio_sample_rate,
        channels=settings.audio_channels,
        dtype=settings.audio_dtype,
        device_index=device_index,
        max_seconds=settings.recording_max_seconds,
    )

    recorder.start_recording()
    if not recorder.is_recording:
        print("FAIL — could not open mic stream.")
        sys.exit(1)

    import time
    for i in range(3, 0, -1):
        print(f"  {i}s remaining...")
        time.sleep(1)

    wav_bytes = recorder.stop_recording()
    if not wav_bytes:
        print("FAIL — stop_recording returned empty bytes.")
        sys.exit(1)

    out_path = Path("data/recordings/stt_smoke.wav")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(wav_bytes)
    print(f"Saved {len(wav_bytes):,} bytes to {out_path}")

    # --- Step 2: transcribe via Groq ---
    stt = STTService(
        api_key=settings.groq_api_key,
        model=settings.stt_model,
        language=settings.stt_language,
        temperature=settings.stt_temperature,
        timeout_seconds=settings.stt_timeout_seconds,
    )

    print(f"Sending to Groq ({settings.stt_model})...")
    try:
        result = await stt.transcribe(out_path)
    except STTError as exc:
        print(f"FAIL — STTError: {exc}")
        await stt.close()
        sys.exit(1)
    finally:
        await stt.close()

    print(f"\nPASS")
    print(f"  Transcript : {result.text}")
    print(f"  Latency    : {result.latency_ms:.0f} ms")
    print(f"  Model      : {result.model}")
    print(f"  Language   : {result.language}")


if __name__ == "__main__":
    asyncio.run(main())
