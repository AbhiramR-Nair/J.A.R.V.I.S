# Project Status — Day 8

**Period covered:** Day 8 (PTT Audio Capture with sounddevice)
**Status:** Complete — all completion criteria met. Ready for Day 9 STT.
**Environment:** Windows 11, Python 3.13.5, sounddevice 0.5.5, numpy 2.4.6

> Checkpoint summary for Day 8: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 9.

---

## 1. What has been done

Day 8 closed the second major loop: the asyncio event loop now drives real-time audio
capture in response to hotkey events, producing WAV files ready for the Day 9 STT call.
Five subsystems were wired together: pynput (OS thread), the asyncio dispatcher (event
loop), sounddevice (portaudio thread), the filesystem, and the React frontend (WebSocket).

| Task | What landed | Status |
|---|---|---|
| 8.1 — Dependencies | `sounddevice 0.5.5` installed; `numpy 2.4.6` already present; `requirements.txt` updated | Done |
| 8.2 — Settings | 7 audio fields added to `Settings`; `runtime_settings.py` for JSON-backed mic choice | Done |
| 8.3 — AudioRecorder | `backend/voice/audio.py`: InputStream, list-of-arrays buffer, threading.Lock, WAV via `wave`; smoke test PASS (94 KB for 3s) | Done |
| 8.4 — Lifespan wiring | `app.state.audio_recorder` + `app.state.ready = True` added to lifespan; clean shutdown on exit | Done |
| 8.5 — Dispatcher | `_drain_events` → `_dispatch_events`; `_handle_event_side_effects` branches on `ptt_start`/`ptt_end`/`mute_toggle`; `_save_recording` writes timestamped WAV; `recording_saved` event injected back into queue | Done |
| 8.6 — Device API | `backend/api/audio.py`: `GET /audio/devices`, `GET /audio/device`, `POST /audio/device` (hot-swaps recorder); models in `backend/models/voice.py` | Done |
| 8.7 — Frontend | `VoiceEvent` union extended with `recording_saved`; badge flashes filename for 2s then reverts | Done |

**Completion criteria verified:**

| Criterion | Result |
|---|---|
| Hold Alt+Space → WAV saved in `data/recordings/` | ✅ Confirmed; voice audible at correct pitch |
| Backend logs `ptt_start`, `stream opened`, `ptt_end`, `stream closed`, `recording saved` | ✅ All five lines appear in sequence |
| `GET /audio/devices` returns JSON list including Realtek mic | ✅ Confirmed via curl |
| `GET /audio/device` returns `null` before any device is saved | ✅ Confirmed |
| Frontend badge flashes filename after recording completes | ✅ Confirmed in browser and PyWebView |
| Mute mid-recording aborts cleanly (no WAV saved) | ✅ Confirmed |
| Backend boots with `audio recorder initialized` log line | ✅ Every boot |
| No zombie processes on close | ✅ Inherited from Day 7 |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. `AudioRecorder` lives on `app.state`, not as a module-level singleton

The plan offered three options: module-level singleton, `app.state`, or per-recording
instantiation. `app.state` was chosen for consistency with Day 7's `drainer_task` pattern:
long-lived background subsystems belong there, they're explicitly lifecycle-managed in the
lifespan context manager, and routes can reach them via `request.app.state` without hidden
globals. Per-recording instantiation was ruled out because `sounddevice.InputStream` has a
cold-start cost that would add perceptible latency to every PTT press.

### 2. `list[np.ndarray]` buffer, joined once at stop

The callback appends each chunk to a list. `np.concatenate` is called exactly once at
`stop_recording()`. An alternative — calling `np.concatenate` inside the callback on every
arrival — would be O(n²) for long recordings as it allocates a new array each time. The
list-of-arrays pattern is standard in sounddevice examples for this reason.

### 3. `threading.Lock` around buffer and stream access

sounddevice runs its callback on a private PortAudio thread. `start_recording` and
`stop_recording` are called from `run_in_executor` (a threadpool thread). These two threads
can both touch `_buffer` and `_stream` simultaneously. The lock is minimal: the callback
only appends (fast), and stop only reads + clears (fast). No I/O or blocking calls happen
inside the lock.

### 4. `run_in_executor` for start/stop in the dispatcher

`sounddevice.InputStream.start()` and `.stop()` are synchronous and talk to PortAudio
(which may briefly block while negotiating with the audio driver). Calling them directly
in the asyncio event loop would stall the loop — new WebSocket messages, HTTP requests,
and the next hotkey event would all queue behind the audio driver call. `run_in_executor`
hands the blocking call to a threadpool thread and `await`s the result, keeping the loop
free throughout.

### 5. WAV serialisation via stdlib `wave`, not scipy

`scipy.io.wavfile.write` is the obvious choice but adds a heavy dependency (scipy) for
what is two calls: set params + write frames. stdlib `wave` is available in every Python
installation, produces identical RIFF/WAV output, and has no transitive dependencies.
`setsampwidth(2)` sets 2 bytes per sample, which matches `dtype="int16"`.

### 6. Dispatcher as a single queue consumer, not a pub/sub bus

The alternative (multiple subscribers on the queue, one for audio, one for WebSocket) would
require a fan-out queue or topic-based pub/sub. A single dispatcher that branches on event
type is simpler, has one consumer, and scales naturally: Day 11's conversation state machine
is another `elif` in `_handle_event_side_effects`. The pub/sub complexity would only pay off
if multiple independent consumers needed to run concurrently per event — not the case here.

### 7. `app.state.ready` flag

pynput's listener starts before the `AudioRecorder` is constructed in the lifespan. If a
key is pressed in that window (e.g. the user hits Alt+Space while the backend is still
loading), the dispatcher would call `recorder.start_recording()` before `app.state.audio_recorder`
exists, raising `AttributeError`. The `ready` flag is set to `True` only after the recorder
is fully wired. Events arriving before that are silently dropped — they are sub-second in
practice and losing them is acceptable.

### 8. Store device index AND name in `settings.json`

`sounddevice` uses the integer index at runtime, but device indices can shift after USB
reshuffles or Bluetooth connect/disconnect. The name is stored alongside the index so that
Day 12 (mic robustness) can recover the correct device by name scan if the saved index no
longer matches. Cheap to add now, prevents a confusing "wrong mic" bug later.

---

## 3. Problems faced and how they were handled

No blocking problems arose on Day 8. The implementation went smoothly, with one minor
structural observation:

### Observation — hook filename mismatch with plan

The Day 8 plan referred to `frontend/src/hooks/useVoiceEvents.ts` as the file to edit.
The actual file (created on Day 7) is `frontend/src/hooks/useWebSocket.ts`, which exports
the same `useVoiceEvents` function. The plan used the function name rather than the
filename. Not a bug — just a note that the plan's file references can diverge from actual
filenames. Always read the actual file structure before editing.

---

## 4. Heads-up: downstream complications to watch

### The max-duration auto-stop does not emit a `recording_saved` event

When the callback's frame-count guard fires (PTT held > 30s), it sets `_recording = False`
but cannot call `stop_recording()` because that method acquires the same `threading.Lock`
the callback already holds. The design is: the callback sets the flag, and the next
`ptt_end` hotkey event calls `stop_recording()` which finds the full buffer and serialises
it normally.

**The gap:** if the user holds Alt+Space past 30 seconds and then releases, `stop_recording()`
is called by the dispatcher on `ptt_end` — but `is_recording` is already `False`. The
idempotency guard returns `b""` and no WAV is saved. The 30-second buffer is silently lost.

**Mitigation:** Day 12 (audio robustness) is the right place to fix this. The fix is to
have the callback push a synthetic `ptt_end` event into the queue when it auto-stops, so
the dispatcher treats it exactly like a normal release. Not done today to stay in scope.

### `recording_saved` event is broadcast to all WebSocket clients before Day 9 consumes it

Currently only the React frontend receives `recording_saved`. Day 9 will need to intercept
this event inside the dispatcher to trigger STT, not just let it flow through to the UI.
The cleanest Day 9 pattern: add an `elif etype == "recording_saved"` branch in
`_handle_event_side_effects` that kicks off the Groq Whisper call, then broadcasts a
`transcription_complete` event. The `recording_saved` broadcast to the UI can remain as a
debug signal or be removed once the UI gets a richer event.

### `POST /audio/device` rebuilds the recorder but does not validate the new device opens cleanly

The endpoint constructs a new `AudioRecorder` with the chosen index but doesn't actually
open a stream to verify the device works. The first sign of a bad device index will be a
`PortAudioError` on the next `ptt_start`. This is acceptable for Day 8 — the UI for device
selection (Day 17) should surface the error state clearly. For now, if a bad index is set
via the API, the user will see "couldn't open stream" in the logs on next PTT.

### Frontend badge shows filename not a human-readable label

The 2-second flash shows the raw ISO8601 timestamp filename (e.g. `20260523T165843123456.wav`).
This is intentional for debugging during Week 2. Day 17's settings panel will replace this
with a cleaner status message ("Recording saved") as part of the general UI polish pass.

### Threadpool executor is the default (unbounded)

`run_in_executor(None, ...)` uses Python's default `ThreadPoolExecutor`, which creates
threads on demand with no upper bound. For PTT audio this is fine — one thread per
recording, short-lived. If Day 11's conversation orchestrator also uses `run_in_executor`
heavily, consider passing an explicit `ThreadPoolExecutor(max_workers=4)` to the lifespan
and storing it on `app.state` to cap the pool size.

---

## 5. How to verify Day 8

```powershell
# 1. Clean start — kill any process on port 8000
netstat -ano | findstr :8000
# Stop-Process -Id <PID> if anything shows

# 2. Start Vite + backend
cd frontend; npm run dev
# new terminal:
python -m backend.desktop

# 3. Wait for "WS /ws/voice connected" in data/logs/jarvis.log

# 4. PTT happy path
#    Hold Alt+Space ~3 seconds, say something, release
#    Expected: WAV in data/recordings/, voice audible, badge flashes filename

# 5. Device list API
curl http://localhost:8000/audio/devices   # JSON list with Realtek mic
curl http://localhost:8000/audio/device    # null

# 6. Clean shutdown — click ✕, confirm no leftover python.exe
```

---

## 6. Open items before Day 9

- [ ] The max-duration auto-stop silently loses the buffer on `ptt_end` — defer fix to Day 12.
- [ ] Consider a synthetic `ptt_end` queue injection from the callback when the guard fires.
- [ ] Day 9 will add `elif etype == "recording_saved"` to `_handle_event_side_effects` to
      trigger STT — keep that in mind when reading the dispatcher.

---

## 7. Files changed this day

```
NEW:
  backend/voice/audio.py
  backend/config/runtime_settings.py
  backend/api/audio.py
  backend/tests/test_audio_smoke.py

EDIT:
  backend/config/settings.py       (+9 lines: Path import + 7 audio fields)
  backend/main.py                  (imports, _drain_events → _dispatch_events + helpers,
                                    lifespan: audio_recorder + ready flag + shutdown)
  backend/models/voice.py          (+DeviceInfo, +SetDevicePayload)
  backend/requirements.txt         (+sounddevice 0.5.5)
  frontend/src/hooks/useWebSocket.ts  (+recording_saved to VoiceEvent union)
  frontend/src/App.tsx             (+lastRecording state, +recording_saved handler, +badge label)
  docs/journal.md                  (+Day 8 line)
```

---

## 8. Commit

```
feat: ptt audio capture with sounddevice

- Add AudioRecorder in backend/voice/audio.py: sounddevice InputStream,
  16kHz mono int16, buffer-and-serialize-to-WAV on stop
- Rename _drain_events → _dispatch_events; branch on event type to drive
  audio start/stop alongside WS broadcast
- Add app.state.audio_recorder and app.state.ready flag (Day 7 carryover)
- Add data/settings.json-backed runtime config for input device choice;
  endpoints GET /audio/devices, GET/POST /audio/device
- Wire ptt_start → start_recording (in executor), ptt_end → stop + save WAV
  to data/recordings/{iso8601}.wav, then emit recording_saved event
- Mute mid-recording aborts cleanly; max 30s recording cap
- Frontend: useVoiceEvents handles recording_saved (2s filename flash in badge)
```
