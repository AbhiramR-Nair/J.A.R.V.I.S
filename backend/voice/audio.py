"""
Push-to-talk audio recorder using sounddevice.InputStream.

Threading model:
  - sounddevice runs its audio callback on a private OS thread (not asyncio).
  - start_recording / stop_recording are called from the asyncio dispatcher via
    run_in_executor, so they may run on a threadpool thread.
  - _on_audio only appends to a list under a lock — no I/O, no async, no blocking.
  - The lock guards _buffer and _stream access across these two thread contexts.
"""

import threading
import wave
from collections.abc import Callable
from io import BytesIO
from typing import Optional

import numpy as np
import sounddevice as sd
from loguru import logger

from backend.config.settings import get_settings


class AudioCaptureError(Exception):
    """Raised when the audio device cannot be opened or fails mid-recording.

    User-facing message is safe to display directly — callers should not
    wrap this in a generic "internal error" response.
    """


# Maps substrings found in PortAudio error messages to user-readable hints.
# Checked case-insensitively; first match wins. Extend here when new variants
# are found in the wild rather than touching call sites.
_PORTAUDIO_ERROR_HINTS: dict[str, str] = {
    # MME error 1 = MMSYSERR_ERROR: Windows returns this when the privacy
    # setting blocks microphone access. Must be checked before the broader
    # "unanticipated host error" entry because both share that prefix.
    "mme error 1": (
        "Microphone access denied. "
        "Open Windows Settings → Privacy → Microphone and allow access."
    ),
    # MME error 2 = MMSYSERR_ALLOCATED: device is held by another process.
    "unanticipated host error": (
        "Microphone is in use by another application. "
        "Close other audio apps and try again."
    ),
    "device unavailable": (
        "Microphone unavailable — try reconnecting your headset."
    ),
    "access is denied": (
        "Microphone access denied. "
        "Open Windows Settings → Privacy → Microphone and allow access."
    ),
}
_PORTAUDIO_FALLBACK = "Microphone unavailable — check connections and try again."


def _classify_portaudio_error(exc: sd.PortAudioError) -> str:
    """Return a user-facing message for a PortAudio open failure."""
    raw = str(exc)
    logger.debug(f"portaudio error raw string: {raw!r}")
    msg = raw.lower()
    for keyword, hint in _PORTAUDIO_ERROR_HINTS.items():
        if keyword in msg:
            return hint
    return _PORTAUDIO_FALLBACK


class AudioRecorder:
    """
    Lifecycle:
      __init__  → stores config, does NOT open a stream (mic stays closed at boot)
      start_recording()  → opens InputStream, begins buffering chunks
      stop_recording()   → closes stream, serialises buffer to WAV bytes, returns them
      is_recording       → bool property

    start/stop are sync and safe to call from run_in_executor (they release the GIL
    while sounddevice talks to PortAudio). stop_recording is idempotent: calling it
    when not recording returns b"" with a log warning.
    """

    def __init__(
        self,
        sample_rate: int,
        channels: int,
        dtype: str,
        device_index: Optional[int],
        max_seconds: int,
        notify_cap_hit: Optional[Callable[[], None]] = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._dtype = dtype
        self._device_index = device_index
        self._max_seconds = max_seconds
        self._max_frames = max_seconds * sample_rate
        # Calibration max read once at construction (cached singleton — no file I/O).
        # Stored as an instance field so the callback never touches the settings module.
        self._mic_calibration_max = get_settings().mic_calibration_max
        # Latest per-chunk RMS amplitude, normalized to [0.0, 1.0].
        # Written by the callback (OS thread), read by the broadcast task (asyncio thread).
        # Plain float assignment is atomic in CPython under the GIL — no lock needed for reads.
        self._latest_amplitude: float = 0.0
        # Called (once) when the 30s cap fires. Must be thread-safe — the callback
        # runs on a PortAudio OS thread, not the asyncio loop. The caller is
        # responsible for using loop.call_soon_threadsafe inside the callable.
        self._notify_cap_hit = notify_cap_hit

        # Buffer accumulates numpy chunks from the callback; joined at stop.
        # list-of-arrays avoids O(n²) repeated np.concatenate inside the callback.
        self._buffer: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None
        self._recording = False
        self._cap_notified = False      # prevents the cap callable firing more than once
        self._callback_error: Optional[Exception] = None  # set by callback on hardware fault

        device_label = f"index={device_index}" if device_index is not None else "default"
        logger.info(
            f"audio recorder initialized "
            f"(device={device_label}, {sample_rate} Hz, "
            f"ch={channels}, dtype={dtype})"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_recording(self) -> None:
        """Open the sounddevice InputStream and start buffering audio chunks."""
        with self._lock:
            if self._recording:
                logger.warning("audio recorder: start_recording called while already recording — ignored")
                return

            self._buffer.clear()
            self._cap_notified = False
            self._callback_error = None

            try:
                # blocksize drives how many frames arrive per callback invocation.
                # 50 ms at 16 kHz = 800 frames — small enough for snappy PTT release.
                blocksize = int(self._sample_rate * 50 / 1000)

                self._stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=self._channels,
                    dtype=self._dtype,
                    device=self._device_index,
                    blocksize=blocksize,
                    callback=self._on_audio,
                )
                self._stream.start()
                self._recording = True
                logger.info("audio recorder: stream opened")
            except sd.PortAudioError as exc:
                self._stream = None
                # Raise so the orchestrator can show the user a clear error and
                # attempt recovery, instead of silently transitioning to LISTENING
                # with no active stream.
                raise AudioCaptureError(_classify_portaudio_error(exc)) from exc

    def stop_recording(self) -> bytes:
        """
        Close the stream, join buffered chunks, return WAV bytes.
        Returns b"" if called when not recording (logs a warning).
        """
        with self._lock:
            if not self._recording:
                logger.warning("audio recorder: stop_recording called while not recording — returning b''")
                return b""

            self._recording = False
            # Zero out amplitude immediately so the broadcast task doesn't read a stale
            # value in the window between stream stop and task cancellation (T-3).
            self._latest_amplitude = 0.0

            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.warning(f"audio recorder: error closing stream — {exc}")
            finally:
                self._stream = None

            # Surface any exception the callback thread stored mid-recording.
            # This is the only safe place to raise it — the callback can't raise
            # directly (sounddevice would just disable the stream silently).
            if self._callback_error is not None:
                err = self._callback_error
                self._callback_error = None
                raise AudioCaptureError(
                    "Microphone disconnected — please reconnect and try again"
                ) from err

            if not self._buffer:
                logger.warning("audio recorder: buffer is empty — returning b''")
                return b""

            # Join all chunks into one contiguous array, then serialise to WAV.
            audio_data = np.concatenate(self._buffer, axis=0)
            frame_count = len(audio_data)
            self._buffer.clear()

            logger.info(f"audio recorder: stream closed, captured {frame_count} frames "
                        f"({frame_count / self._sample_rate:.2f}s)")

        return self._to_wav(audio_data)

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def latest_amplitude(self) -> float:
        """Most recent per-chunk RMS amplitude, normalized to [0.0, 1.0].

        Updated ~every 50ms while recording (sounddevice blocksize).
        Safe to read from any thread without a lock (CPython GIL, plain float).
        Returns 0.0 when not recording.
        """
        return self._latest_amplitude

    @classmethod
    def test_open(
        cls,
        device_index: Optional[int],
        sample_rate: int,
        channels: int,
        dtype: str,
    ) -> None:
        """Try opening an InputStream against *device_index* and close it immediately.

        Called before swapping the active recorder so the old one stays live
        until we know the new device actually works. Raises AudioCaptureError
        if PortAudio rejects the open (device in use, permission denied, etc.).
        """
        try:
            blocksize = int(sample_rate * 50 / 1000)
            stream = sd.InputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype=dtype,
                device=device_index,
                blocksize=blocksize,
            )
            stream.start()
            stream.stop()
            stream.close()
        except sd.PortAudioError as exc:
            raise AudioCaptureError(_classify_portaudio_error(exc)) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_audio(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags,
    ) -> None:
        """
        sounddevice callback — runs on a private OS thread, NOT the asyncio loop.
        Only appends to the buffer under the lock. No I/O, no blocking calls here.
        sounddevice silently disables the stream if this function raises, so all
        exceptions are caught and logged.
        """
        try:
            if status:
                # status flags signal overruns, underruns, etc. Log but keep going.
                logger.warning(f"audio recorder callback status: {status}")

            with self._lock:
                if not self._recording:
                    return

                # indata shape is (frames, channels); copy() because the buffer
                # backing indata is reused by sounddevice on the next callback.
                self._buffer.append(indata.copy())

                # Normalize int16 → [-1.0, 1.0] float before RMS so calibration_max
                # lives in the same [0, 1] domain regardless of dtype.
                rms = float(np.sqrt(np.mean((indata.astype(np.float32) / 32768.0) ** 2)))
                self._latest_amplitude = min(rms / self._mic_calibration_max, 1.0)

                # Max-duration guard: auto-stop if the user leaves PTT held too long.
                total_frames = sum(len(chunk) for chunk in self._buffer)
                if total_frames >= self._max_frames:
                    logger.warning(
                        f"audio recorder: max duration ({self._max_seconds}s) reached — "
                        "auto-stopping. Release Alt+Space."
                    )
                    # Can't call stop_recording() here — it acquires the same lock.
                    # Set the flag; the orchestrator's on_recording_cap_hit will
                    # drain the buffer and dispatch the turn.
                    self._recording = False
                    # Notify the orchestrator exactly once via the thread-safe callable.
                    if not self._cap_notified and self._notify_cap_hit is not None:
                        self._cap_notified = True
                        self._notify_cap_hit()

        except Exception as exc:
            # Can't raise from the callback — sounddevice would disable the stream.
            # Store it for stop_recording() to raise on the main/executor thread.
            self._callback_error = exc
            logger.exception(f"audio recorder callback error: {exc}")

    def _to_wav(self, audio_data: np.ndarray) -> bytes:
        """
        Serialise a (N, channels) int16 numpy array to RIFF/WAV bytes.
        Uses stdlib `wave` — no scipy dependency.
        setsampwidth(2) = 2 bytes per sample = 16-bit PCM.
        """
        buf = BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)                      # int16 = 2 bytes
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio_data.tobytes())
        return buf.getvalue()
