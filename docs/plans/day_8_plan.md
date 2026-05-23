# Day 8 Plan — Audio Capture via Push-to-Talk

**Reference:** `Day_by_Day_Plan_v2.md` § Day 8, `Version_1_plan.md` § Week 2, `.claude/skills/project-architecture/SKILL.md`
**Predecessor:** Day 7 (PyWebView shell + global hotkeys + WS fan-out) — complete
**Successor:** Day 9 (STT via Groq — consumes the WAV bytes this day produces)
**Time budget:** 4 hours (per plan); realistic ceiling 5–6 hours if device selection UX is built today
**Commit target:** `feat: ptt audio capture with sounddevice`

---

## 1. Agenda — what Day 8 is and isn't

**Is:** Holding Alt+Space anywhere on Windows produces a 16 kHz mono WAV file on disk that plays back correctly. Releasing Alt+Space stops the recording and emits a `recording_saved` event with the file path. The active mic device is configurable and persists across restarts.

**Isn't:**
- Not STT — that's Day 9. Today the WAV just gets saved to `data/recordings/` and logged.
- Not silence trimming, VAD, or amplitude analysis — that's Day 16.
- Not mic disconnect robustness or Bluetooth mode switching — that's Day 12. Today: log gracefully on common errors, don't crash.
- Not a settings panel UI — that's Day 17. Today: a single dropdown / endpoint is enough.

**Why this scope:** Day 7 proved we can route OS-thread events into the asyncio loop via the queue pattern. Day 8 closes the second loop: the asyncio loop spawning real-time audio capture and writing its output back into the same world. After today, the next 3 days are pure cloud-API plumbing (STT → LLM → TTS).

**Single-sentence test for end of Day 8:** "Hold Alt+Space, say 'one two three', release — a WAV file appears under `data/recordings/`, opens in Windows Media Player, and my voice is clearly audible at normal pitch."

---

## 2. Pre-flight checklist (5 min)

Run before writing any code:

- [ ] On `main` branch, working tree clean. `git status` empty.
- [ ] Day 7 verification still passes (`python -m backend.desktop` → window opens, Alt+Space updates badge, Ctrl+Alt+J toggles mute, ✕ exits cleanly).
- [ ] `pip list | findstr sounddevice` → empty (we'll install fresh today).
- [ ] `data/recordings/` directory exists or will be created. Add `data/recordings/*.wav` to `.gitignore` if not already covered by `data/` blanket ignore.
- [ ] Confirm mic works outside our app: open Windows Voice Recorder, record 3 seconds, play back. If this fails, fix Windows before fighting Python.

**If anything above fails, stop and fix it.** Day 8 builds on a healthy Day 7.

---

## 3. Design decisions to lock before writing code

> Per CLAUDE.md rule #2: surface trade-offs first, decide, *then* implement. Resolve these four before asking Claude Code to write `audio.py`.

### D1 — Where does the `AudioRecorder` instance live?

| Option | Pro | Con |
|---|---|---|
| **A. Module-level singleton in `voice/audio.py`** | Easy to import anywhere; mirrors `_loop` in `hotkeys.py` | Hidden global state; harder to mock for tests |
| **B. On `app.state.audio_recorder`** | Consistent with Day 7's `drainer_task` pattern; explicit lifespan | Slightly more wiring; routes need `request.app.state` |
| C. Created per-recording | Clean isolation | Sounddevice stream init has cold-start cost; not ideal for snappy PTT |

**Recommendation: B.** Day 7 established `app.state` as the place for long-lived background subsystems. The recorder is one. Keeps the pattern uniform.

### D2 — How do hotkey events reach the recorder?

Today the drainer only does one thing: `ws_manager.broadcast(event)`. After today it needs to *also* trigger audio. Two ways:

| Option | Shape |
|---|---|
| **A. Drainer becomes a dispatcher** | Drainer reads queue → branches on event type → calls `audio.start()`/`audio.stop()` *and* `ws_manager.broadcast()` |
| B. Audio module subscribes to the queue directly | Two consumers on one queue — needs a fan-out queue or pub/sub |

**Recommendation: A.** Simpler, one consumer per queue, and the dispatcher pattern scales naturally to Day 11 (conversation state machine) and Day 26 (timers). Rename `_drain_events` → `_dispatch_events` in `main.py`.

### D3 — Audio format

Locked by downstream consumers:
- Groq Whisper accepts WAV, MP3, M4A, FLAC. Cheapest path: WAV.
- openWakeWord (Day 27, if we get there) requires **16 kHz mono int16**.
- Whisper internally resamples to 16 kHz anyway.

**Decision:** 16 kHz, mono, signed 16-bit PCM, written as standard RIFF/WAV. No room for variation.

### D4 — Device selection persistence

| Option | Where it lives |
|---|---|
| A. `.env` | Conflates secrets with runtime config — bad |
| **B. `data/settings.json`** | Mutable, gitignored, owned by app |
| C. New SQLite `user_settings` table | Overkill for one int |

**Recommendation: B.** Plan already specifies `settings.json`. Schema for now is tiny:
```json
{ "input_device_index": 12, "input_device_name": "Microphone (Realtek)" }
```
Store both index *and* name. Index is what sounddevice needs; name is what we display *and* what we use to recover if the index shifts after a USB reshuffle (Day 12 problem, but cheap to lay groundwork now).

### D5 — Side decision: the `app.state.ready` flag (carryover from Day 7)

Day 7 status flagged this as an open item. Day 8 introduces a new subsystem (audio) that must not initialize before the backend's lifespan startup completes. Add the flag today:

```python
# in main.py lifespan, after queue + dispatcher are wired:
app.state.ready = True
```

The audio recorder's `start()` checks `app.state.ready` before opening a stream. Cheap, prevents a future race when Day 11's conversation orchestrator is layered in.

---

## 4. Task breakdown

Each task lists files touched, the deliverable, and how to verify before moving on. Do them in order — later tasks depend on earlier ones.

### Task 8.1 — Install dependencies (10 min)

```powershell
.\.venv\Scripts\Activate.ps1
pip install sounddevice numpy
pip freeze > backend/requirements.txt
```

Verify in Python REPL:
```python
import sounddevice as sd
print(sd.query_devices())   # should print a table of input/output devices
```

**Done when:** `requirements.txt` updated, REPL prints your mic in the list, commit not yet — bundle with later code.

**Watch out for:** On Windows, `sounddevice` ships with a portaudio DLL — no separate install needed. If you get `OSError: PortAudio library not found`, you're on the wrong wheel; reinstall.

---

### Task 8.2 — Settings: add audio config (30 min)

**File: `backend/config/settings.py`** (extend, minimal diff)

Add to the existing `Settings` class:
```python
audio_sample_rate: int = 16000
audio_channels: int = 1
audio_dtype: str = "int16"
audio_chunk_ms: int = 50            # input callback granularity
recording_max_seconds: int = 30     # PTT timeout (Day 12 will refine)
recordings_dir: Path = Path("data/recordings")
runtime_settings_path: Path = Path("data/settings.json")
```

**New file: `backend/config/runtime_settings.py`**

Small JSON-backed module for *user-mutable* settings (device choice). Separate from `settings.py` which holds compile-time config.

```python
# Loads / saves data/settings.json. Provides get_input_device() and set_input_device().
# Falls back to system default if file missing or device index invalid.
```

**Why split this from settings.py:** Pydantic Settings is for read-mostly config loaded once at boot. Runtime settings (which mic the user picked yesterday) need to be written *back* to disk during a session. Mixing them confuses lifetimes.

**Done when:** REPL can call `from backend.config.runtime_settings import get_input_device; print(get_input_device())` and it returns `None` (no file yet) without crashing.

---

### Task 8.3 — `AudioRecorder` class (90 min — the meat)

**New file: `backend/voice/audio.py`**

Spec to give Claude Code (after you've written the docstring/signature yourself per CLAUDE.md rule):

```python
class AudioRecorder:
    """
    Push-to-talk audio recorder using sounddevice.InputStream.

    Lifecycle:
      - __init__ stores config, does NOT open a stream
      - start_recording() opens stream, begins appending chunks to in-memory buffer
      - stop_recording() closes stream, returns WAV bytes; clears buffer
      - is_recording property
      - safe to call stop_recording() when not recording (no-op, logs warning)

    Threading:
      - sounddevice uses its own callback thread; our callback only appends to a
        list under a lock — no I/O, no async, no blocking
      - start/stop are sync methods called from the asyncio dispatcher via
        loop.run_in_executor (sounddevice is not async-aware)
    """
    def __init__(self, sample_rate: int, channels: int, dtype: str,
                 device_index: int | None, max_seconds: int): ...
    def start_recording(self) -> None: ...
    def stop_recording(self) -> bytes: ...   # returns WAV bytes
    @property
    def is_recording(self) -> bool: ...
```

Key implementation points (have Claude Code write the body, but verify these are present):

1. **Buffer = `list[numpy.ndarray]`**, accumulated per callback, joined at stop. Avoids per-callback `np.concatenate` (O(n²)).
2. **Lock around buffer access** — `threading.Lock`. Callback thread vs. asyncio thread.
3. **`InputStream` with `callback=self._on_audio`**, opened with `samplerate=16000, channels=1, dtype="int16"`.
4. **WAV serialization** — use stdlib `wave` module, not scipy. `wave.open(BytesIO, "wb")` → `setnchannels`, `setsampwidth(2)`, `setframerate(16000)`, `writeframes(buffer.tobytes())`.
5. **Max duration guard** — if buffer length exceeds `max_seconds * sample_rate`, auto-stop and emit a warning event. Don't let a stuck PTT eat memory.
6. **Graceful close** — `stop_recording` should be idempotent. Calling it twice = second call returns `b""` with a log warning.
7. **Comment block per CLAUDE.md** above the callback, the lock pattern, and the WAV serialization — these are the bits the user will re-read in 3 weeks.

**Done when:** A standalone smoke test in `backend/tests/test_audio_smoke.py` runs:

```python
# Manually: import AudioRecorder, start_recording(), time.sleep(3), stop_recording()
# Assert: returned bytes start with b"RIFF" and are >50KB for 3 seconds at 16kHz mono int16
# Write to data/recordings/smoke.wav, play it manually
```

This test is NOT pytest-automated — per CLAUDE.md, lightweight smoke tests only. Just a script you run once.

---

### Task 8.4 — Wire to lifespan (`main.py`) (30 min)

**Edit `backend/main.py`** — minimal diff, additive only.

In the lifespan async context manager, after the existing queue + drainer setup:

```python
from backend.voice.audio import AudioRecorder
from backend.config.runtime_settings import get_input_device

# Build the recorder with the user's saved device (or None = system default)
device = get_input_device()
app.state.audio_recorder = AudioRecorder(
    sample_rate=settings.audio_sample_rate,
    channels=settings.audio_channels,
    dtype=settings.audio_dtype,
    device_index=device["index"] if device else None,
    max_seconds=settings.recording_max_seconds,
)
app.state.ready = True   # NEW — Day 7 carryover
```

On shutdown side of the lifespan, if `app.state.audio_recorder.is_recording`: call `stop_recording()` and discard.

**Done when:** Backend boots, log line `audio recorder initialized (device=default, 16000 Hz mono int16)` appears.

---

### Task 8.5 — Rename drainer → dispatcher, branch on event type (45 min)

**Edit the dispatcher in `main.py` (formerly `_drain_events`):**

```python
async def _dispatch_events(app, queue, ws_manager):
    """
    Single consumer of the hotkey event queue. For each event:
      1. Side-effects (audio start/stop, future: timers, conversation state)
      2. Broadcast to all WebSocket clients
    """
    while True:
        event = await queue.get()
        try:
            await _handle_event_side_effects(app, event)
        except Exception as e:
            logger.exception(f"dispatcher side-effect failed: {e}")
            # still broadcast; UI shouldn't go silent because audio choked
        await ws_manager.broadcast(event)
```

**`_handle_event_side_effects` skeleton:**

```python
async def _handle_event_side_effects(app, event):
    if not getattr(app.state, "ready", False):
        return   # drop events before backend is fully up

    recorder = app.state.audio_recorder
    etype = event.get("type")

    if etype == "ptt_start":
        # Run in executor — sounddevice is sync and would block the loop
        await asyncio.get_running_loop().run_in_executor(
            None, recorder.start_recording
        )

    elif etype == "ptt_end":
        wav_bytes = await asyncio.get_running_loop().run_in_executor(
            None, recorder.stop_recording
        )
        if wav_bytes:
            path = await _save_recording(wav_bytes)
            # Inject a new event so the UI knows where the file landed
            await queue.put({"type": "recording_saved", "path": str(path)})

    elif etype == "mute_toggle":
        # If mute is on, abort any in-flight recording
        if recorder.is_recording:
            await asyncio.get_running_loop().run_in_executor(
                None, recorder.stop_recording
            )
        # (the broadcast still happens; React still flips its muted state)
```

**`_save_recording`** writes to `data/recordings/{ISO8601}.wav` and returns the Path. Async wrapper around `Path.write_bytes` is fine; the file is small.

**Done when:**
- Hold Alt+Space 2 seconds, release → backend log shows:
  - `hotkey → ptt_start`
  - `audio recorder: stream opened`
  - `hotkey → ptt_end`
  - `audio recorder: stream closed, captured N frames`
  - `recording saved: data/recordings/20XX-XX-XXT...wav`
- A WAV file exists, plays back, is your voice, no clicks/pops.

---

### Task 8.6 — Mic device endpoint (30 min)

**New: `backend/api/audio.py`** (then register the router in `main.py`)

```python
@router.get("/audio/devices")
async def list_devices() -> list[DeviceInfo]:
    """Return all input-capable devices. Used by the future settings panel."""

@router.get("/audio/device")
async def get_device() -> DeviceInfo | None: ...

@router.post("/audio/device")
async def set_device(payload: SetDevicePayload) -> DeviceInfo:
    """
    Persist the user's choice. Rebuild app.state.audio_recorder with the new index.
    Must NOT be called while recording — return 409 if so.
    """
```

Pydantic models (`DeviceInfo`, `SetDevicePayload`) go in `backend/models/voice.py` (existing file from Day 3).

**`sd.query_devices()` returns a list of dicts with `name`, `max_input_channels`, `default_samplerate`, `index`.** Filter to `max_input_channels > 0`.

**Done when:**
- `curl http://localhost:8000/audio/devices` returns a JSON list with at least your built-in mic.
- `curl -X POST http://localhost:8000/audio/device -d '{"index": N}'` saves to `settings.json` and the next PTT recording uses that mic.

**Skip the React UI for this today.** A `curl`-driven setting is enough to satisfy "can be changed in settings." Day 17 builds the panel.

---

### Task 8.7 — Frontend: log the `recording_saved` event (15 min)

**Edit `frontend/src/hooks/useVoiceEvents.ts`** — minimal diff.

The hook already handles `ptt_start`, `ptt_end`, `mute_toggle`. Add handling for `recording_saved`:
- Push to a small debug log array (or just `console.log` for today)
- Optionally: show last recording filename in the status badge for 2 seconds, then revert

No new component. Day 17 is when the chat panel grows up.

**Reminder from Day 7 status:** HMR is broken inside PyWebView. After this edit, close the window and relaunch `python -m backend.desktop`. During iteration, test in a regular browser tab against `http://localhost:5173` where HMR works.

---

## 5. Verification protocol (15 min — do this before commit)

Run all of these. If any fail, fix before committing.

```powershell
# 1. Clean start
# Make sure port 8000 is free:
netstat -ano | findstr :8000     # should be empty
# If not:
Stop-Process -Id <PID>

# 2. Start Vite + backend
cd frontend; npm run dev
# new terminal:
python -m backend.desktop

# 3. Wait for "WS /ws/voice connected" in data/logs/jarvis.log

# 4. PTT happy path
#    Hold Alt+Space ~3 seconds, say "test one two three", release
#    Expected: WAV in data/recordings/, plays back, your voice clear, no clipping

# 5. PTT abort path
#    Press Ctrl+Alt+J (mute) WHILE holding Alt+Space
#    Expected: recording aborted, no WAV saved, badge shows muted

# 6. Tap-only path
#    Tap Alt+Space briefly (no speech)
#    Expected: tiny WAV (<5 KB) OR no WAV — both acceptable, must not crash

# 7. Max-duration path
#    Hold Alt+Space and walk away for 35 seconds
#    Expected: recording auto-stops at ~30s, log shows warning, WAV saved at 30s length

# 8. Device API
curl http://localhost:8000/audio/devices
curl http://localhost:8000/audio/device

# 9. Clean shutdown
#    Click ✕ on the overlay
#    Confirm Task Manager: no leftover python.exe, no leftover node.exe (Vite dev)
```

**If something looks off:**
- WAV has wrong pitch (chipmunk or slow) → sample rate mismatch, check `wave.setframerate`
- WAV is silent → mic muted in Windows, or device index points to an output, or permissions
- Clicks at start/end → harmless for now; Day 12 may add a 50 ms fade

---

## 6. Files touched (sanity check before commit)

```
NEW:
  backend/voice/audio.py
  backend/config/runtime_settings.py
  backend/api/audio.py
  backend/tests/test_audio_smoke.py
  data/recordings/.gitkeep         (creates the directory)

EDIT (minimal diffs):
  backend/config/settings.py       (+8 lines of audio config)
  backend/main.py                  (lifespan additions; rename drainer → dispatcher)
  backend/models/voice.py          (DeviceInfo, SetDevicePayload models)
  backend/requirements.txt         (sounddevice, numpy)
  frontend/src/hooks/useVoiceEvents.ts  (handle recording_saved)
  .gitignore                       (confirm data/recordings/*.wav excluded)
```

If this diff grows beyond ~250 lines added across all files, something has expanded. Stop and ask before continuing.

---

## 7. Commit message

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
- Frontend: useVoiceEvents handles recording_saved (debug log only for now)
```

Plus the daily journal line in `docs/journal.md`:

```
Day 8 — PTT audio capture: AudioRecorder with sounddevice; rename drainer
to dispatcher to fan side-effects + broadcast; data/settings.json for device
choice; first WAV files landing in data/recordings/. Ready for Day 9 STT.
```

---

## 8. Risks & gotchas specific to today

| Risk | Likelihood | Mitigation |
|---|---|---|
| Windows mic permission silently denied | Medium | `sounddevice` raises `PortAudioError` — wrap stream open in try/except, log clearly. Check Windows Settings → Privacy → Microphone if confused. |
| Wrong dtype produces garbage WAV | Medium | Numpy defaults to float32. Explicitly pass `dtype="int16"` to `InputStream`. Verify first WAV before adding features. |
| Bluetooth headset switches sample rate | Low today, real for Day 12 | If you record with AirPods/buds today and it sounds weird, switch to wired mic. Don't fix this today. |
| Sounddevice callback exception silently swallowed | Medium | Wrap callback body in try/except, log via loguru. Callbacks that throw get silently disabled by portaudio. |
| WAV file written before stream fully drained | Low | `InputStream.stop()` then `close()` before reading the buffer. Sounddevice docs are explicit; follow them. |
| `app.state.ready` flag forgotten in shutdown | Low | Set it to `False` in lifespan shutdown side too. |
| Running `python -m backend.desktop` while old process still holds port 8000 | High (it's happened twice already) | Kill port 8000 first. Add a note to README. |

---

## 9. What NOT to do today (scope guard)

The plan rewards saying no. Items to refuse if they tempt you:

- ❌ **Don't add VAD or silence trimming.** Day 11/16 territory.
- ❌ **Don't pipe audio to STT.** Day 9.
- ❌ **Don't build a settings panel UI.** Day 17. `curl` is fine for Day 8.
- ❌ **Don't add a "test mic" button.** Day 12. Smoke-test by listening to the WAV.
- ❌ **Don't refactor the dispatcher to a generic event bus with topics.** YAGNI until Day 26.
- ❌ **Don't add format support for MP3/M4A/FLAC.** WAV only.
- ❌ **Don't optimize buffer concatenation.** The list-of-arrays pattern is fine for 30-second recordings. Premature.
- ❌ **Don't write pytest tests.** CLAUDE.md says smoke tests only. One manual smoke script in `backend/tests/` is the bar.

---

## 10. End-of-day checklist

- [ ] All 9 verification steps pass
- [ ] Commit pushed to `main`
- [ ] `docs/journal.md` updated (one line)
- [ ] You can explain (out loud, to yourself): (a) why audio runs in `run_in_executor` instead of natively async; (b) what the dispatcher does that the old drainer didn't; (c) why we save device index *and* name in settings.json
- [ ] At least one line of code today was typed by hand, not pasted
- [ ] Skim Day 9 in `Day_by_Day_Plan_v2.md` so tomorrow morning starts at full speed

If all checked: tag the commit, close VS Code, walk away. Good day.
