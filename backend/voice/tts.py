"""Local TTS via Piper subprocess + sounddevice playback.

Piper is invoked as a subprocess per utterance. Text is piped to stdin;
raw PCM (--output_raw) comes back on stdout. sounddevice plays it via a
blocking helper run in an executor so the asyncio event loop stays free.

No streaming in v1 — all PCM is collected before playback starts. Good enough
for typical reply lengths (<30 words) on this hardware. Streaming is a Day 13/
Week 3 upgrade candidate if time-to-first-audio becomes a pain point.
"""

import asyncio
import re
from time import perf_counter

import numpy as np
import sounddevice as sd
from loguru import logger
from pydantic import BaseModel

from backend.config.settings import Settings


def _clean_for_tts(text: str) -> str:
    """Strip markdown formatting that Piper would speak as literal characters.

    Order matters: handle bold+italic (***) before bold (**) before italic (*),
    then clean up anything the regex didn't catch as matched pairs.
    """
    # Matched pairs: ***bold+italic***, **bold**, *italic*
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    # Inline code: `code`
    text = re.sub(r'`+([^`]*)`+', r'\1', text)
    # ATX headers: # ## ###
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Bullet list markers at line start (- item / * item)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    # Numbered list markers (1. 2.)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Any remaining stray asterisks (unmatched)
    text = re.sub(r'\*', '', text)
    # Collapse excess whitespace introduced by stripping
    text = re.sub(r'  +', ' ', text)
    return text.strip()


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


def _make_callback(audio: np.ndarray, service: "TTSService"):
    """Factory for the OutputStream pull callback.

    Returns a closure that fills outdata chunk-by-chunk from audio, computes
    per-chunk RMS amplitude, and writes it to service._latest_amplitude.
    Raises sd.CallbackStop when the array is exhausted.
    """
    cursor = 0

    def _cb(outdata: np.ndarray, frames: int, time_info, status) -> None:
        nonlocal cursor
        remaining = len(audio) - cursor

        # Guard against a spurious extra callback after CallbackStop (PortAudio impl detail).
        if remaining <= 0:
            outdata[:] = 0
            raise sd.CallbackStop

        n = min(frames, remaining)
        chunk = audio[cursor : cursor + n]

        # outdata shape is (frames, 1) for mono — fill the channel column.
        outdata[:n, 0] = chunk
        if n < frames:
            outdata[n:] = 0  # zero-pad the tail of the last partial chunk

        cursor += n

        # Normalize int16 → [-1, 1] float before RMS so tts_calibration_max
        # lives in the same [0, 1] domain as mic_calibration_max.
        rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768.0) ** 2)))
        service._latest_amplitude = min(rms / service._tts_calibration_max, 1.0)

        if cursor >= len(audio):
            raise sd.CallbackStop

    return _cb


def _play_with_amplitude(
    audio: np.ndarray,
    sample_rate: int,
    device: int | None,
    service: "TTSService",
) -> None:
    """Blocking OutputStream playback with per-chunk amplitude tracking.

    Replaces _play_sync. Must be called from an executor (this function blocks).
    Stores the active stream on service._active_stream so cancel_playback() can
    abort it from the asyncio thread. The finally block guarantees cleanup
    whether playback completes normally, is aborted, or raises.
    """
    # blocksize ~50ms matches the mic callback rate; gives amplitude at ~20Hz.
    blocksize = int(sample_rate * 0.05)
    callback = _make_callback(audio, service)

    try:
        with sd.OutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            device=device,
            blocksize=blocksize,
            callback=callback,
        ) as stream:
            service._active_stream = stream
            while stream.active:
                sd.sleep(20)  # poll every 20ms; fine in an executor thread
    finally:
        # Runs on normal completion AND on abort from cancel_playback().
        service._active_stream = None
        service._latest_amplitude = 0.0


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
        # Calibration max for RMS normalization (same domain as mic: normalized float).
        self._tts_calibration_max = settings.tts_calibration_max
        # Latest per-chunk RMS amplitude [0.0, 1.0]. Written by OutputStream callback
        # (executor thread); read by broadcast task (asyncio thread). GIL-atomic.
        self._latest_amplitude: float = 0.0
        # Active OutputStream while speak() is playing. Set in _play_with_amplitude
        # before the polling loop; cleared in its finally block. None when idle.
        self._active_stream: sd.OutputStream | None = None
        logger.info("tts service initialised: voice={}", settings.piper_voice_path.name)

    @property
    def latest_amplitude(self) -> float:
        """Most recent per-chunk RMS amplitude during TTS playback, normalized to [0.0, 1.0].

        Updated ~every 50ms while playing (OutputStream blocksize).
        Returns 0.0 when not playing. Safe to read from any thread (GIL-atomic float).
        """
        return self._latest_amplitude

    async def synthesize(self, text: str) -> tuple[np.ndarray, SynthesisResult]:
        """Run Piper as a subprocess and return raw PCM as a numpy int16 array.

        Text is sent via stdin. Raw PCM arrives on stdout via --output_raw.
        stderr is captured separately so error details reach the log without
        leaking into the audio buffer.

        Raises:
            TTSError: on subprocess failure, timeout, empty output, or missing binary.
                      Message is safe to display directly in the UI.
        """
        text = _clean_for_tts(text)
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
            # _play_with_amplitude blocks in the executor while the OutputStream plays.
            # It updates self._latest_amplitude per chunk and self._active_stream so
            # cancel_playback() can abort it mid-play.
            await loop.run_in_executor(
                None, _play_with_amplitude, array, self._sample_rate, self._output_device, self
            )
        except Exception as exc:
            logger.warning("sounddevice playback error: {}", exc)
            raise TTSError("TTS playback failed.") from exc

        return result

    async def cancel_playback(self) -> None:
        """Stop any in-flight TTS playback immediately. Safe to call when nothing plays.

        Aborts the explicit OutputStream stored in _active_stream. abort() discards
        buffered audio and returns immediately — faster than stop() which drains first.
        The _play_with_amplitude finally block then clears _active_stream and resets
        _latest_amplitude; we also zero it here in case that finally runs after a brief
        delay on the executor thread.
        """
        stream = self._active_stream
        if stream is not None and stream.active:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, stream.abort)
        self._latest_amplitude = 0.0

    async def close(self) -> None:
        """No-op today — Piper has no persistent process to clean up.

        Kept for symmetry with STTService.close() so the lifespan shutdown
        block can call close() on all services uniformly without special-casing.
        """
        logger.info("tts service closed")
