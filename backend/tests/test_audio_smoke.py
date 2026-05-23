"""
Manual smoke test for AudioRecorder — NOT a pytest test, run directly.

Usage:
    python -m backend.tests.test_audio_smoke

What it does:
    1. Opens the default mic (or device_index from data/settings.json if saved)
    2. Records for 3 seconds while printing a countdown
    3. Stops, writes data/recordings/smoke.wav
    4. Asserts the file starts with b"RIFF" and is > 50 KB (sanity for 3s 16kHz mono int16)
    5. Prints PASS or FAIL with a clear message

Listen to smoke.wav in Windows Media Player to confirm your voice is audible.
"""

import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path when run as `python -m backend.tests.test_audio_smoke`
# (the -m flag does this automatically, but explicit is harmless)

from backend.config.runtime_settings import get_input_device
from backend.config.settings import get_settings
from backend.voice.audio import AudioRecorder


def main() -> None:
    settings = get_settings()
    device = get_input_device()
    device_index = device["index"] if device else None
    device_label = device["name"] if device else "system default"

    print(f"Smoke test: recording 3s from '{device_label}' at 16kHz mono int16")

    recorder = AudioRecorder(
        sample_rate=settings.audio_sample_rate,
        channels=settings.audio_channels,
        dtype=settings.audio_dtype,
        device_index=device_index,
        max_seconds=settings.recording_max_seconds,
    )

    recorder.start_recording()
    if not recorder.is_recording:
        print("FAIL — stream did not open. Check mic permissions and device index.")
        sys.exit(1)

    for i in range(3, 0, -1):
        print(f"  recording... {i}s remaining")
        time.sleep(1)

    wav_bytes = recorder.stop_recording()

    if not wav_bytes:
        print("FAIL — stop_recording returned empty bytes.")
        sys.exit(1)

    if not wav_bytes[:4] == b"RIFF":
        print(f"FAIL — WAV header wrong: got {wav_bytes[:4]!r}, expected b'RIFF'")
        sys.exit(1)

    min_expected = 50 * 1024   # 50 KB for 3 seconds of 16kHz mono int16
    if len(wav_bytes) < min_expected:
        print(f"FAIL — WAV too small: {len(wav_bytes)} bytes (expected > {min_expected})")
        sys.exit(1)

    out_path = Path("data/recordings/smoke.wav")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(wav_bytes)

    print(f"PASS — {len(wav_bytes):,} bytes written to {out_path}")
    print("Open smoke.wav in Windows Media Player and confirm your voice is audible.")


if __name__ == "__main__":
    main()
