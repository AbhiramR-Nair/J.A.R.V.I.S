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
from io import BytesIO
from typing import Optional

import numpy as np
import sounddevice as sd
from loguru import logger


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
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._dtype = dtype
        self._device_index = device_index
        self._max_seconds = max_seconds
        self._max_frames = max_seconds * sample_rate

        # Buffer accumulates numpy chunks from the callback; joined at stop.
        # list-of-arrays avoids O(n²) repeated np.concatenate inside the callback.
        self._buffer: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None
        self._recording = False

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
                # Common causes: mic permission denied, device unplugged, wrong index.
                logger.error(f"audio recorder: failed to open stream — {exc}")
                self._stream = None

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

            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.warning(f"audio recorder: error closing stream — {exc}")
            finally:
                self._stream = None

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

                # Max-duration guard: auto-stop if the user leaves PTT held too long.
                total_frames = sum(len(chunk) for chunk in self._buffer)
                if total_frames >= self._max_frames:
                    logger.warning(
                        f"audio recorder: max duration ({self._max_seconds}s) reached — "
                        "auto-stopping. Release Alt+Space."
                    )
                    # Can't call stop_recording() here — it acquires the same lock.
                    # Set the flag; the dispatcher's next stop_recording call will
                    # find the full buffer and serialise it.
                    self._recording = False

        except Exception as exc:
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
