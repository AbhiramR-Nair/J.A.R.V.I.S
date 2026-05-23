"""Local TTS via Piper subprocess + sounddevice playback.

Piper is invoked as a subprocess per utterance. Text is piped to stdin;
raw PCM (--output_raw) comes back on stdout. sounddevice plays it via a
blocking helper run in an executor so the asyncio event loop stays free.

No streaming in v1 — all PCM is collected before playback starts. Good enough
for typical reply lengths (<30 words) on this hardware. Streaming is a Day 13/
Week 3 upgrade candidate if time-to-first-audio becomes a pain point.
"""

import asyncio
from time import perf_counter

import numpy as np
import sounddevice as sd
from loguru import logger
from pydantic import BaseModel

from backend.config.settings import Settings


class SynthesisResult(BaseModel):
    """Metadata returned after a successful speak() call.

    pcm_bytes is intentionally excluded — we never log or serialize raw audio.
    """

    latency_ms: float
    sample_rate: int
    num_samples: int


class TTSError(Exception):
    """Raised when synthesis or playback cannot be completed.

    Message is pre-sanitised for UI display — no raw subprocess output or
    file paths. Callers (route handlers, dispatcher) catch this type and
    surface it to the user as-is.
    """


def _play_sync(array: np.ndarray, sample_rate: int, device: int | None) -> None:
    """Blocking sounddevice playback. Must be called from an executor.

    sd.play() is non-blocking by default — it returns immediately while audio
    plays on a PortAudio thread. sd.wait() blocks until playback is done.
    Together they give us synchronous completion semantics in a thread.
    """
    sd.play(array, samplerate=sample_rate, device=device)
    sd.wait()


class TTSService:
    """Wraps Piper subprocess + sounddevice playback.

    Lifecycle: constructed once in the FastAPI lifespan, speak() called per
    utterance, close() called on shutdown (currently a no-op but kept for
    symmetry with STTService so the lifespan pattern is consistent).
    """

    def __init__(self, settings: Settings) -> None:
        self._binary = str(settings.piper_binary_path)
        self._voice = str(settings.piper_voice_path)
        self._sample_rate = settings.tts_sample_rate
        self._output_device = settings.tts_output_device
        self._timeout = settings.tts_timeout_seconds
        logger.info("tts service initialised: voice={}", settings.piper_voice_path.name)

    async def synthesize(self, text: str) -> tuple[np.ndarray, SynthesisResult]:
        """Run Piper as a subprocess and return raw PCM as a numpy int16 array.

        Text is sent via stdin. Raw PCM arrives on stdout via --output_raw.
        stderr is captured separately so error details reach the log without
        leaking into the audio buffer.

        Raises:
            TTSError: on subprocess failure, timeout, empty output, or missing binary.
                      Message is safe to display directly in the UI.
        """
        # Short log preview — never log the full text (can be long; hurts grep).
        preview = text[:60] + ("…" if len(text) > 60 else "")
        bound = logger.bind(req_id=f"tts-{len(text)}ch")
        bound.info("synthesising: '{}' ({}chars)", preview, len(text))

        start = perf_counter()
        try:
            # asyncio.create_subprocess_exec keeps the event loop free while Piper runs.
            # stdin=PIPE sends text in; stdout=PIPE receives raw PCM; stderr=PIPE captures
            # Piper's own error output so it doesn't bleed into the PCM stream.
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                "--model", self._voice,
                "--output_raw",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # communicate() sends stdin, waits for process exit, then returns
            # (stdout_bytes, stderr_bytes). It is the safe way to avoid deadlocks
            # when both stdin and stdout are piped.
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(text.encode("utf-8")),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            # Kill the hung subprocess before raising so it doesn't linger.
            try:
                proc.kill()
            except Exception:
                pass
            latency_ms = (perf_counter() - start) * 1000
            bound.warning("tts timed out after {:.0f}ms", latency_ms)
            raise TTSError("TTS took too long — try a shorter reply.")
        except FileNotFoundError:
            latency_ms = (perf_counter() - start) * 1000
            bound.error("piper binary not found at {} ({:.0f}ms)", self._binary, latency_ms)
            raise TTSError("TTS unavailable — Piper binary not found.")
        except Exception as exc:
            latency_ms = (perf_counter() - start) * 1000
            bound.warning("tts subprocess error after {:.0f}ms: {}", latency_ms, exc)
            raise TTSError("TTS failed — could not synthesise audio.") from exc

        latency_ms = (perf_counter() - start) * 1000

        if proc.returncode != 0:
            # Log stderr so we can see Piper's own error message in the log.
            err_detail = stderr.decode("utf-8", errors="replace").strip()
            bound.warning("piper exited {} after {:.0f}ms: {}", proc.returncode, latency_ms, err_detail)
            raise TTSError("TTS failed — Piper returned an error.")

        # np.frombuffer wraps the bytes as an int16 view (zero-copy where possible).
        array = np.frombuffer(stdout, dtype=np.int16)
        if array.size == 0:
            bound.warning("piper produced no audio after {:.0f}ms", latency_ms)
            raise TTSError("TTS produced no audio.")

        result = SynthesisResult(
            latency_ms=latency_ms,
            sample_rate=self._sample_rate,
            num_samples=int(array.size),
        )
        bound.info(
            "tts synth ok: {}samples @ {}Hz ({:.0f}ms)",
            array.size, self._sample_rate, latency_ms,
        )
        return array, result

    async def speak(self, text: str) -> SynthesisResult:
        """synthesize() then play through sounddevice. Returns metadata for logging.

        sounddevice.play() / .wait() are blocking PortAudio C calls — they must
        run in an executor so they don't block the asyncio event loop.

        Raises:
            TTSError: propagated from synthesize(), or on playback failure.
        """
        array, result = await self.synthesize(text)

        loop = asyncio.get_running_loop()
        try:
            # run_in_executor runs the blocking play+wait in a thread pool thread.
            await loop.run_in_executor(
                None, _play_sync, array, self._sample_rate, self._output_device
            )
        except Exception as exc:
            logger.warning("sounddevice playback error: {}", exc)
            raise TTSError("TTS playback failed.") from exc

        return result

    async def close(self) -> None:
        """No-op today — Piper has no persistent process to clean up.

        Kept for symmetry with STTService.close() so the lifespan shutdown
        block can call close() on all services uniformly without special-casing.
        """
        logger.info("tts service closed")
