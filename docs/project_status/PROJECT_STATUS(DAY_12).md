# Project Status — Day 12

**Period covered:** Day 12 (Audio Robustness)
**Status:** Complete — all P0 tasks and P1 tasks done. Commit `f98a4c4`.
**Environment:** Windows 11, Python 3.13.5, FastAPI, React 18 + Vite, Groq Whisper-large-v3, Piper `en_US-lessac-medium`, Gemini 2.5 Flash

> Checkpoint summary for Day 12: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 13.

---

## 1. What has been done

Day 12 hardened the audio layer for real-world Windows conditions: mic disconnects,
permission errors, the 30s recording cap, and device validation. Day 11 closed the
happy path; Day 12 closes the sad paths.

| Task | What landed | Status |
|---|---|---|
| 12.1a — `_handle_error` lock assertion | `assert self._lock.locked()` added as first line of `_handle_error` in `conversation.py` | Done |
| 12.1b — Recordings cleanup | `backend/voice/cleanup.py` — `prune_recordings(dir, max_age_days)` deletes WAVs older than 14 days. Wired into `_save_recording` via executor. `recordings_max_age_days=14` in settings | Done |
| 12.2 — Validate `POST /audio/device` | `AudioCaptureError` class defined in `voice/audio.py`. `test_open` classmethod tries opening a stream briefly before the swap; route returns HTTP 400 if it fails | Done |
| 12.3 — 30s cap notification | `notify_cap_hit` callable injected into `AudioRecorder.__init__`; callback fires it via `loop.call_soon_threadsafe`. `RecordingCapHitEvent` added. `on_recording_cap_hit` in orchestrator. `_drain_recording` factored out (shared by `on_ptt_end` and `on_recording_cap_hit`). UI shows toast "Recording stopped at 30s limit." | Done |
| 12.4 — Mic disconnect recovery | `_callback_error` field stores exceptions from the PortAudio thread. `stop_recording` raises `AudioCaptureError` if set. `_drain_recording` catches it → ERROR state. `on_ptt_start` catches open failure → `_rebuild_recorder` (default device) → retry → `AudioDeviceRecoveredEvent` on success | Done |
| 12.5 — Test mic button | `POST /audio/test-mic`: 3s record + playback + peak amplitude. Rejects with 409 if not IDLE/MUTED. `SettingsPanel.tsx` built out with result badge (green/yellow/red). `state` property added to `ConversationOrchestrator` for clean state access | Done |
| 12.6 — PortAudio error classification | `_PORTAUDIO_ERROR_HINTS` dict + `_classify_portaudio_error` helper. Classifies by MME error code (empirically determined). Both `start_recording` and `test_open` use it | Done |
| 12.7 — Loud input verification | Manual test: yelled into mic, app did not crash, STT returned garbled text and handled it normally | Done |
| 12.8 — Verification + journal + commit | All 11 checklist items ticked, journal updated, commit `f98a4c4` | Done |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. `notify_cap_hit` callable — dependency inversion for the 30s cap

The PortAudio callback runs on a private OS thread. It cannot touch the asyncio event
loop directly (`asyncio` is not thread-safe). The recorder also should not import the
orchestrator — that would create a circular dependency.

The solution: inject a pre-bound callable at construction time in `main.py` lifespan:

```python
def _on_cap_hit() -> None:
    loop.call_soon_threadsafe(event_queue.put_nowait, {"type": "recording_cap_hit"})
```

This callable captures `loop` and `event_queue` from the lifespan closure. The recorder
stores it and calls it from the callback thread. `call_soon_threadsafe` is the only
asyncio primitive guaranteed safe from non-async threads. The recorder stays
loop-agnostic — it just calls a function, unaware of what's on the other end.

The `_cap_notified` flag prevents the callable firing on every subsequent callback after
the cap fires (the callback continues being called with zeroed buffers until
`stop_recording` closes the stream).

### 2. `_drain_recording` helper — shared between `on_ptt_end` and `on_recording_cap_hit`

Before Day 12, `on_ptt_end` contained the stop→validate→transition→spawn logic inline.
`on_recording_cap_hit` needed the same sequence. Rather than duplicating 15 lines:

- `_drain_recording()` is a private method that must be called **with the lock held**.
  It returns `bytes` (non-empty = dispatch a turn) or `b""` (don't spawn a task).
- Both callers acquire the lock, call `_drain_recording()`, exit the lock, then
  conditionally spawn `_process_turn`.

The "returns bytes, caller spawns" pattern was chosen over "spawns internally" because
spawning a task inside a helper while the lock is held is semantically confusing —
the task runs concurrently with the caller, which still holds the lock. Returning bytes
and spawning after the `async with` block exits makes the concurrency boundary explicit.

### 3. `_callback_error` — surfacing callback exceptions safely

PortAudio runs the audio callback on its own thread. If that thread raises, sounddevice
silently disables the stream rather than propagating the exception. There is no
direct path from the callback thread to the asyncio world without thread-safe bridges.

The chosen pattern:
1. Callback catches all exceptions, stores on `self._callback_error`, logs them.
2. `stop_recording` (called from the executor, on the asyncio side) inspects
   `self._callback_error` after closing the stream and raises `AudioCaptureError`.
3. `_drain_recording` catches `AudioCaptureError` from the executor call and handles it.

This is the minimal-footprint way to bridge the threading boundary. The alternative
(a second `call_soon_threadsafe` from the callback for error events) would be correct
but requires careful ordering — the callback could fire an error event before the cap
event, creating unexpected state transitions.

### 4. `_rebuild_recorder` — default device fallback

When `start_recording` fails in `on_ptt_start`, the orchestrator attempts to rebuild
the recorder against `device_index=None` (system default). Reasons for this approach:

- After a USB mic is unplugged and replugged, Windows re-enumerates devices. The original
  numeric index may no longer point to the same hardware.
- The system default (`None`) is always whatever Windows currently considers primary —
  reliable even after re-enumeration.
- The `notify_cap_hit` callable is preserved from the old recorder
  (`self._recorder._notify_cap_hit`) so the 30s cap still works after rebuild.

If the rebuild also fails, the orchestrator transitions to ERROR with a user-facing
message and auto-recovers in 3s. The user's saved device preference in
`data/settings.json` is NOT updated — the rebuild is a transient fallback, not a
permanent device change.

### 5. PortAudio error classification by MME error code

The plan specified keyword matching against PortAudio error strings. The initial
keywords (`"access is denied"`, `"unanticipated host error"`) were educated guesses
based on Windows documentation.

Empirical testing revealed that Windows privacy blocking (Settings → Privacy →
Microphone → off) does **not** return "access is denied". The actual PortAudio string
is:

```
Error opening InputStream: Unanticipated host error [PaErrorCode -9999]:
'Undefined external error.' [MME error 1]
```

`MME error 1` is `MMSYSERR_ERROR` (generic/unspecified Windows multimedia error) —
which is what Windows returns when it blocks a device at the OS level. `MME error 2`
(`MMSYSERR_ALLOCATED`) would indicate "device in use by another process."

The fix: add `"mme error 1"` as the first entry in `_PORTAUDIO_ERROR_HINTS` (checked
before the broader `"unanticipated host error"` catch). Since the dict is checked in
insertion order and first-match wins, the more specific MME-code check runs first.

---

## 3. Problems faced and how they were handled

### Problem 1 — `_pending_wav` anti-pattern in first `_drain_recording` implementation

**What happened:** The first implementation of `_drain_recording` stored `wav_bytes` on
`self._pending_wav` so the caller could read it after the method returned (while still
inside `async with self._lock:`). This is an anti-pattern — storing ephemeral return
values as instance attributes couples the caller and callee in non-obvious ways and
is not safe under any concurrent access.

**Fix:** `_drain_recording` was immediately rewritten to return `bytes | None`. The
callers (`on_ptt_end`, `on_recording_cap_hit`) capture the return value inside the
lock block, exit the lock, then spawn `_process_turn(wav_bytes)` if non-empty. The
lock is released by the `async with` block exit, not by the method itself.

**Lesson:** Helpers that run "with lock held" should communicate via return values,
not side channels on `self`. Using instance attributes for this masks the data flow
and will cause confusion when adding new callers later.

### Problem 2 — Missing toast on `on_ptt_start` failure (UI only showed "Status: error")

**What happened:** When both `start_recording` and the rebuild attempt failed,
`on_ptt_start` called `_handle_error(str(exc2))`. `_handle_error` transitions the
state machine to ERROR and broadcasts a `StateChangedEvent` — but no user-readable
toast. The UI showed the state badge change ("Status: error") but no explanatory
message. The user saw the error state without knowing why.

**Root cause:** `_handle_error` is a state-machine primitive. It doesn't know about
toast-level messaging — that's the caller's responsibility. Every other error path
in the codebase (`_drain_recording`, STT failure in `_run_pipeline`) explicitly
broadcasts `transcription_failed` before calling `_handle_error`. The `on_ptt_start`
error path was the only one that skipped this step.

**Fix:** Added `await self._broadcast({"type": "transcription_failed", "error": str(exc2)})`
before `await self._handle_error(str(exc2))` in the rebuild failure branch. The
existing `transcription_failed` handler in `App.tsx` shows the message as a toast
with 3s auto-clear.

Also fixed a double-wrapping bug in the same block: the original code passed
`f"Microphone unavailable: {exc2}. Check connections and try again."` to `_handle_error`,
but `exc2` is already a classified user-friendly message from `_classify_portaudio_error`.
The wrapping produced log noise like `"Microphone unavailable: Microphone is in use by
another application. ..."`. Fixed by passing `str(exc2)` directly.

**Rule going forward:** whenever `_handle_error` is called, also broadcast a
`transcription_failed` (or equivalent) event to give the user a readable message.
`_handle_error` alone is not enough for user-facing feedback.

### Problem 3 — PortAudio keyword mismatch for Windows privacy denied

**What happened:** `_PORTAUDIO_ERROR_HINTS` mapped `"access is denied"` to the privacy
message. The Windows privacy block produced "Unanticipated host error [MME error 1]",
which matched the *in-use* hint instead. The user saw "Microphone is in use by another
application" when privacy was blocked — technically wrong.

**How discovered:** Added `logger.warning(f"portaudio error raw string: {raw!r}")` to
`_classify_portaudio_error`. Note: the initial version used `logger.debug` which is
suppressed at the default `INFO` log level — had to change to `warning` before the
log appeared.

**Fix:** Added `"mme error 1"` as the first key in `_PORTAUDIO_ERROR_HINTS`, mapping to
the privacy-denied message. `"unanticipated host error"` remains as the fallback for
other variants of that error class. The log line was reverted to `logger.debug`.

---

## 4. Heads-up: downstream complications to watch

### `_rebuild_recorder` accesses a private attribute of the old recorder

`_rebuild_recorder` reads `self._recorder._notify_cap_hit` to preserve the callable
on the new recorder. This accesses a private (`_`) attribute of `AudioRecorder` from
outside the class. It works because `ConversationOrchestrator` owns the recorder and
both are in the same package — but if `AudioRecorder`'s internals are refactored (e.g.,
the field is renamed), this will fail silently (callable becomes `None`) and the 30s
cap will stop notifying the orchestrator after a rebuild.

**Mitigation:** If `AudioRecorder` is ever refactored, check `_rebuild_recorder` in
`conversation.py`. Alternatively, expose `notify_cap_hit` as a public property on
`AudioRecorder` at some future point.

### Rebuild uses system default device — user's saved preference is ignored

After a rebuild, `self._recorder` uses `device_index=None` (Windows default). The
user's saved device choice in `data/settings.json` is not consulted. On the next
session restart, `main.py` lifespan will read `settings.json` and restore the correct
device — but within the current session, PTT will use the system default.

**Implication:** If the user's primary mic is a USB device and they unplug/replug it,
the rebuilt recorder targets the system default (probably built-in mic) not the USB mic.
The `AudioDeviceRecoveredEvent` toast says "Switched to default microphone" which makes
this visible. The fix (read `settings.json` and try the saved device first in
`_rebuild_recorder`) is a small improvement for Day 17 when the settings panel gains
a device picker.

### `test_open` briefly opens the mic device during `POST /audio/device`

The device validation in `POST /audio/device` calls `AudioRecorder.test_open`, which
opens an `InputStream`, starts it, stops it, and closes it in quick succession. On
some audio drivers this produces a brief pop or interrupts other audio momentarily.
On the i3 laptop with the current driver stack this hasn't been observed — but it's
worth knowing if audio glitches are reported after a device swap.

### `_callback_error` only surfaces when `stop_recording` is called

If a hardware fault fires mid-recording but the user never releases the PTT key (stays
in LISTENING state indefinitely), `_callback_error` is set but never raised — the
orchestrator sits in LISTENING, the UI shows "listening", and nothing visible happens.
The error is only raised when `stop_recording` is called (on PTT release or mute).

In practice this is acceptable: the 30s cap will auto-stop the recording regardless,
at which point `stop_recording` runs and the error surfaces. But if the cap also fails
(e.g., the callback exception also broke the frame counting), the user is stuck in
LISTENING until they release the key.

**Mitigation:** The 30s cap fires `notify_cap_hit` before the frame-counting check
that might be affected. In the current code ordering, the cap notification fires
first, so this scenario is unlikely. Worth revisiting if both fires are ever observed
together.

### PortAudio error classification is Windows/MME specific

`_PORTAUDIO_ERROR_HINTS` uses MME error codes (`[MME error 1]`, etc.) which are
Windows Multimedia Extension constants. This will not match on other audio backends
(WASAPI, DirectSound, ASIO on Windows; ALSA/PulseAudio on Linux). Since this is a
Windows-only daily-driver tool this is acceptable, but it's worth noting if the
project is ever run on another OS.

The `_PORTAUDIO_FALLBACK` message remains the safe default for any unrecognised string.

---

## 5. How to verify Day 12

```powershell
# 1. Happy path regression
# Hold Alt+Space, say "what's the capital of France?", release.
# Expected: spoken "Paris" within ~5s.

# 2. 30s cap (lower the setting to test quickly)
# Edit settings.py: recording_max_seconds = 5, restart backend.
# Hold Alt+Space for 6+ seconds, say something in first 2s, then stay silent.
# Expected: "Recording stopped at 30s limit." toast at ~5s; transcript of what was said; spoken reply.
# Restore recording_max_seconds = 30.

# 3. Invalid device rejected
# curl.exe -X POST http://localhost:8000/audio/device -H "Content-Type: application/json" -d '{"index": 999}'
# Expected: {"detail": "Device index 999 is not a valid input device."}

# 4. Test mic — working
# Open settings panel → click "Test mic" → speak normally.
# Expected: green "✓ Working — peak: 0.XXX" after playback.

# 5. Test mic — silent
# Open settings panel → click "Test mic" → stay silent.
# Expected: yellow "⚠ Silent — check mic (peak: 0.000)".

# 6. Permission denied
# Windows Settings → Privacy → Microphone → off. Restart backend. PTT.
# Expected: "Microphone access denied. Open Windows Settings → Privacy → Microphone and allow access."
# Toggle back on after.

# 7. Loud input
# Hold Alt+Space, speak very loudly. Release.
# Expected: no crash; garbled transcript or normal STT error.
```

All 7 checks passed on 2026-05-25.

---

## 6. Open items before Day 13

- [ ] Voice-pipeline SKILL.md needs updating to reflect Day 12 changes:
  `AudioCaptureError` in service contracts, corrected 30s cap behaviour,
  new events (`recording_cap_hit`, `audio_device_recovered`), Bluetooth MME gotcha
- [ ] `_rebuild_recorder` private-attr access (`_notify_cap_hit`) — low risk for now,
  worth a comment in the code pointing here
- [ ] Saved device preference not restored after rebuild within a session — acceptable
  for v1; revisit at Day 17 settings panel

---

## 7. Files changed this day

```
NEW:
  backend/voice/cleanup.py              — prune_recordings(dir, max_age_days) helper
  frontend/src/components/SettingsPanel.tsx  — rebuilt from empty stub; mic test button

EDIT:
  backend/voice/audio.py               — AudioCaptureError; _PORTAUDIO_ERROR_HINTS +
                                         _classify_portaudio_error; test_open classmethod;
                                         _callback_error field; start_recording raises on
                                         open failure; stop_recording raises on callback error;
                                         notify_cap_hit plumbing + _cap_notified flag
  backend/api/audio.py                 — POST /audio/device: test_open validation;
                                         POST /audio/test-mic: _run_mic_test + endpoint
  backend/models/voice.py              — RecordingCapHitEvent, AudioDeviceRecoveredEvent,
                                         TestMicResult added
  backend/services/conversation.py     — state property; _rebuild_recorder; _drain_recording
                                         helper; on_recording_cap_hit; on_ptt_start rebuild
                                         path; _handle_error lock assertion; prune_recordings
                                         hook in _save_recording; AudioCaptureError imports
  backend/main.py                      — _on_cap_hit notify lambda; recorder constructed with
                                         notify_cap_hit=; recording_cap_hit dispatcher branch
  backend/config/settings.py           — recordings_max_age_days=14, audio_open_timeout_seconds=2.0
  frontend/src/hooks/useWebSocket.ts   — recording_cap_hit, audio_device_recovered in VoiceEvent
  frontend/src/App.tsx                 — SettingsPanel import + render; recording_cap_hit and
                                         audio_device_recovered toast handlers
  docs/journal.md                      — Day 12 one-liner
```

---

## 8. Commit

```
f98a4c4 fix: audio device handling and edge cases (Day 12)
```
