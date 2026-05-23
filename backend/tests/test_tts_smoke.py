"""
Manual smoke test for TTSService — NOT a pytest test, run directly.

Usage:
    python -m backend.tests.test_tts_smoke

What it does:
    1. Constructs TTSService from current settings
    2. Synthesises "Day ten TTS smoke test." via Piper
    3. Plays the audio through the default output device
    4. Prints latency and sample count
    5. Exits 0 on success, 1 on failure

Also runs a longer sentence to catch any buffering / cut-off bugs
that a short test would hide.
"""

import asyncio
import sys

from backend.config.settings import get_settings
from backend.voice.tts import TTSError, TTSService

SHORT = "Day ten TTS smoke test."
LONG = (
    "The ABL1 kinase domain harbours several clinically relevant resistance mutations, "
    "of which T315I is the most notorious, conferring approximately forty-fold resistance "
    "to first-generation tyrosine kinase inhibitors and necessitating the use of "
    "ponatinib in many cases."
)


async def main() -> None:
    settings = get_settings()
    tts = TTSService(settings)

    for label, text in [("SHORT", SHORT), ("LONG", LONG)]:
        print(f"\n[{label}] Speaking: '{text[:70]}{'…' if len(text) > 70 else ''}'")
        try:
            result = await tts.speak(text)
        except TTSError as exc:
            print(f"FAIL — TTSError: {exc}")
            await tts.close()
            sys.exit(1)

        print(f"  PASS — latency={result.latency_ms:.0f}ms  samples={result.num_samples}  rate={result.sample_rate}Hz")

    await tts.close()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
