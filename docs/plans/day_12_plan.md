# Day 12 Plan — Audio Robustness

**Day in v2 plan:** Day 12 — Audio Robustness (Week 2)
**Status entering the day:** Day 11 complete (`v0.2.0-voice-loop`, commit `0722393`).
Full PTT pipeline works end-to-end; mute is solid; all 5 race-condition guards verified.
**Time budget:** 4–5 hours.
**End-of-day commit message:** `fix: audio device handling and edge cases`
**End-of-day tag (optional):** none — buffer days (13/14) follow.

> Day 12 hardens the audio layer for real-world Windows mess: mic disconnects, permission
> errors, the 30s cap, Bluetooth headset/headphones mode swaps. It also closes the four
> Day 11 carryover items that explicitly nominated Day 12 as their home. By the end of the
> day, unplugging a mic mid-utterance should produce a clean UI error and the app should
> recover; the "Test mic" button in settings should record 3 seconds and play it back; and
> the 30s auto-stop should no longer be silent.

---

## 0. Why this day matters (framing)

Day 11 closed the happy path. Day 12 is about the **sad paths** — the things that break
silently or crash the app when reality intrudes. These are the bugs that turn a
working-on-the-desk demo into an unreliable daily-driver, so they're worth a full day
even though no single fix is large.

The two biggest payoffs of the day:

1. **No silent failures left in the audio layer.** Today, the 30s cap fires and the user
   sees nothing; `POST /audio/device` accepts an invalid index without complaint and the
   error surfaces only on the next PTT. Both are exactly the kind of "why didn't it work?"
   bugs that erode confidence in the assistant.
2. **A self-test path.** A "Test mic" button means future-you can diagnose audio issues
   in under 10 seconds instead of doing a full PTT loop to find out the input device is
   gone. This pays off every time Bluetooth misbehaves.

The watch-out from the v2 plan is real: **Bluetooth devices on Windows switch between
"headset" mode (16 kHz, mic+speaker) and "headphones" mode (48 kHz, speaker only)**. The
recorder is built for 16 kHz mono and will not survive a mid-recording switch. Today's
goal there is not to prevent the switch — that's a Windows-level decision — but to fail
gracefully and recover.

---

## 1. Pre-flight (5 min, before any code)

Read these in order:

1. `docs/PROJECT_STATUS_DAY_11_.md` §6 "Open items" and §4 "Heads-up: downstream
   complications" — the four Day 12 carryovers and the auto-stop ordering note.
2. `.claude/skills/voice-pipeline/SKILL.md` — specifically the **Service contracts**
   section (`AudioRecorder` semantics) and the **30s max-duration recording silently
   loses the buffer** gotcha. Today touches both.
3. `backend/voice/audio.py` end-to-end — it is the file that changes most today.
4. `backend/services/conversation.py` `on_ptt_start` / `on_ptt_end` / `on_mute_toggle`
   — to understand what the orchestrator currently expects from the recorder.

Then:

```powershell
# Pull, branch, and run the Day 11 smoke from the status doc §5 steps 4–8.
# If happy path is broken on entry, stop and fix that first. Day 12 builds on it.
git checkout main; git pull
git checkout -b day-12-audio-robustness
```

Run the Day 11 smoke checks (status doc §5 steps 4, 6, 7) to confirm the baseline is
green before changing anything.

---

## 2. Task plan

Tasks are ordered by dependency, not by importance. The carryovers (12.1, 12.2) are
small and unblock the rest. The recorder hardening (12.3, 12.4) is the structural heart
of the day. Test-mic (12.5) and Bluetooth (12.6) ride on the hardened recorder.

| #    | Task                                                | Touches                                                   | Est.  | Priority |
|------|-----------------------------------------------------|-----------------------------------------------------------|-------|----------|
| 12.1 | Day 11 carryovers (assert + recordings cleanup)     | `services/conversation.py`, new `voice/cleanup.py`        | 30 m  | P0       |
| 12.2 | Validate `POST /audio/device` before swap           | `api/voice.py` (or wherever the endpoint lives), `voice/audio.py` | 30 m  | P0       |
| 12.3 | 30s auto-stop notifies the orchestrator             | `voice/audio.py`, `services/conversation.py`, `main.py` event queue | 60 m  | P0       |
| 12.4 | Mic disconnect mid-recording → graceful error       | `voice/audio.py` callback + `stop_recording`, `services/conversation.py` | 75 m  | P0       |
| 12.5 | "Test mic" button (backend endpoint + frontend)     | new `api/voice.py` endpoint, `frontend/src/components/SettingsPanel.tsx` | 60 m  | P1       |
| 12.6 | Bluetooth mode-switch + permission denied paths     | `voice/audio.py` open-time validation + recovery          | 45 m  | P1       |
| 12.7 | Loud input / clipping — verify graceful path        | no code; log audit                                        | 15 m  | P2       |
| 12.8 | Verification pass + journal + commit                | manual                                                    | 30 m  | P0       |

**Total:** ~5h15m at the upper bound. If running long, **cut from the bottom** — 12.6
becomes "permission denied only; Bluetooth deferred to Day 13 buffer," and 12.5's
amplitude meter becomes a static success/fail badge. Do not cut 12.1–12.4.

---

## 3. Tasks in detail

### Task 12.1 — Day 11 carryovers (30 min, P0)

Two small, unrelated leftovers from Day 11.

**12.1a — `assert self._lock.locked()` in `_handle_error`**

`_handle_error` requires the orchestrator's `asyncio.Lock` to be already held by the
caller (see voice-pipeline SKILL §"The Lock pattern" and Day 11 status §3 Problem 2).
There is no runtime enforcement today. Add a one-line assertion at the top of the
method so violations surface during development instead of as silent state races later.

- File: `backend/services/conversation.py`
- Add as the first line of `_handle_error`:
  ```python
  assert self._lock.locked(), "_handle_error must be called with self._lock held"
  ```
- Verify: existing flows still pass the Day 11 smoke. The assertion only fires if the
  contract is broken, which it isn't today.

**12.1b — `data/recordings/` cleanup**

At ~160 KB per 5-second utterance and 20 queries/day, the directory accumulates ~3 MB/day.
Not alarming but unbounded.

**Decision to make before writing code:** two reasonable strategies — discuss with
Claude Code before picking.

- **(a) Keep last N files** (e.g. 50). Simple: on every save, list dir, delete oldest
  past index N. Predictable disk footprint. Loses old recordings even if recent ones
  are huge.
- **(b) Delete files older than X days** (e.g. 7). Matches mental model ("last week's
  audio is gone"). Disk footprint depends on activity. A single heavy day can pile up.

Recommendation: **(a) keep last 50** — bounded disk, no time-based reasoning to debug.
Implement as a tiny helper, not a scheduled task. Run on every successful save.

- New file: `backend/voice/cleanup.py` with one function
  `prune_recordings(directory: Path, keep: int = 50) -> int` returning the number
  deleted. Type-hinted, async-friendly (uses `run_in_executor` from the caller since
  it does sync file I/O).
- Wire into `ConversationOrchestrator._save_recording` after the successful write:
  call `prune_recordings(...)` via `run_in_executor`. Failure to prune is non-fatal
  (log warning, continue).
- `keep` goes to `settings.recordings_keep_last` defaulting to 50 — no magic numbers
  (CLAUDE.md hard rule).

**Why a separate module rather than inline:** keeps `conversation.py` focused on
orchestration. The helper is reusable from any future "developer tools" endpoint.

---

### Task 12.2 — `POST /audio/device` validates the new device (30 min, P0)

The endpoint today swaps the recorder to the chosen device index without confirming the
device can actually be opened. The first failure surfaces on the next `ptt_start` as
"nothing happens" (see Day 11 status §4 "Day 9/10 carryovers").

**Approach:**

1. Add a `_test_open(device_index: int) -> None` static-method-or-classmethod on
   `AudioRecorder`. Opens a `sounddevice.InputStream` with the target index and the
   current sample rate, reads zero frames, closes immediately. Raises on failure.
2. In the `POST /audio/device` route handler: call `AudioRecorder._test_open(new_index)`
   first; on success, swap `app.state.audio_recorder`. On failure, return HTTP 400 with
   the PortAudio error message in a Pydantic `ErrorResponse` model (don't dump raw
   `PortAudioError` stringification — wrap it).
3. The old recorder must be cleanly closed before the new one is assigned — order:
   test-open new → close old → assign new. If close-old fails, log warning and
   continue with the new one (recorder is replaceable, swallowing is OK here).

**Why test-open instead of try-and-revert:** revert-on-failure means there's a window
where the recorder is the new one (broken) and PTT could be pressed. Test-open keeps
the old recorder live until we know the new one works.

**Pydantic models to use/create:**

- Request: existing `DeviceChangeRequest` (or similar; check file)
- Response: existing `OkResponse` on success
- Error: existing `ErrorResponse` with `detail: str` — return via FastAPI
  `HTTPException(status_code=400, detail=...)`

No magic numbers; the timeout for test-open is `settings.audio_open_timeout_seconds`
(new, default 2.0).

---

### Task 12.3 — 30s auto-stop notifies the orchestrator (60 min, P0)

**The bug today:** `AudioRecorder._on_audio` (the sounddevice callback running on
PortAudio's private thread) sets `self._recording = False` when 30s is hit, but does
not call `stop_recording()` (it can't — the threading.Lock is held by the callback
itself; would self-deadlock). The orchestrator has no signal that the cap fired. When
the user releases Alt+Space later, `on_ptt_end` runs normally and the full buffer is
transcribed. But if the user holds for 60s, the 30s after the cap is silently
discarded and they get no warning.

**The fix has two parts and needs an architectural decision first.**

**Decision to make before writing code:** how does the callback notify the orchestrator?
Three options to discuss:

- **(a) Inject a synthetic `ptt_end` into the dispatcher queue** from the callback via
  `loop.call_soon_threadsafe(queue.put_nowait, ev)`. The orchestrator handles it as a
  normal `ptt_end`. The downside: the user is still holding Alt+Space, so when they
  release it pynput fires a *real* `ptt_end` that the orchestrator will receive in a
  state other than LISTENING (now TRANSCRIBING or beyond) and drop via the existing G2
  guard. Clean.
- **(b) Add a new `recording_cap_hit` event** the callback fires; orchestrator hears it,
  treats it like `ptt_end`, AND broadcasts a UI warning ("Recording capped at 30s").
  More explicit, slightly more code, gives the user the feedback they should have.
- **(c) Have the orchestrator poll `recorder.is_recording` from a watchdog task.**
  Worst of the three — adds a background task, more state, more places for races.

Recommendation: **(b) — a dedicated `recording_cap_hit` event.** The UI message is the
whole point of fixing this; option (a) is silent in a different way. The Day 11
dispatcher pattern handles this trivially: new event in `backend/models/voice.py`, new
branch in `_handle_event_side_effects` in `main.py` calling
`orchestrator.on_recording_cap_hit()`, which is essentially `on_ptt_end()` plus a
broadcast.

**Implementation outline:**

1. `backend/models/voice.py`: add `RecordingCapHitEvent` Pydantic model and add to
   `VoiceEvent` union.
2. `backend/voice/audio.py` callback: when 30s threshold crosses, instead of just
   flipping `_recording = False`, also enqueue the event into the orchestrator's input
   queue. The recorder doesn't know about the orchestrator directly — pass a
   `notify_callable: Callable[[], None] | None = None` into `AudioRecorder.__init__`
   that the callback invokes (thread-safe via `loop.call_soon_threadsafe` in the
   caller — the recorder stays loop-agnostic).
3. `backend/services/conversation.py`: `on_recording_cap_hit()` — same body as
   `on_ptt_end` but broadcasts a `recording_cap_hit` event to the WS before kicking off
   `_process_turn`.
4. `backend/main.py` lifespan: when constructing the recorder, pass a notify lambda
   that does `loop.call_soon_threadsafe(event_queue.put_nowait, RecordingCapHitEvent())`.
5. Frontend: `useVoiceEvents` already has the queue pattern (Day 11 task 11.2); add
   `recording_cap_hit` to the event union and surface a toast/inline warning in
   `App.tsx` — "Recording stopped at 30s limit."

**Why `notify_callable` rather than importing the orchestrator into the recorder:**
keeps the layering clean. `voice/` should not depend on `services/`. The recorder
emits; the orchestrator listens.

**Watch out for:** the existing `on_ptt_end` does `recorder.stop_recording()` to drain
the buffer. The new path must do the same. Suggest factoring out a small private
method on the orchestrator (`_consume_recording_and_dispatch_turn`) that both
`on_ptt_end` and `on_recording_cap_hit` call, to avoid drift.

---

### Task 12.4 — Mic disconnect mid-recording → graceful error (75 min, P0)

The trickiest task of the day. When a USB or Bluetooth mic disconnects while
`InputStream.read()` is active, sounddevice raises a `PortAudioError` from the
callback's perspective, or — depending on driver — silently produces zeros until the
stream is closed.

**Two failure modes to handle:**

- **Hard disconnect** (USB unplug): sounddevice's callback raises. Today, the
  exception is caught by sounddevice and the stream goes into an error state. Our
  callback continues to be called with zero buffers. Subsequent `stop_recording()`
  returns the partial buffer (probably useful).
- **Soft disconnect** (Bluetooth profile switch): the stream may continue but with
  zeros, or the sample rate underneath us changes. Detection requires either reading
  the stream's `cpu_load` / status flags or just trusting that the user notices their
  recording is bad.

**The realistic scope for today:**

1. Wrap the callback body in a try/except. Any exception → store as
   `self._callback_error: Exception | None` on the recorder; do not re-raise (the
   PortAudio thread can't usefully handle it).
2. `stop_recording()` checks `self._callback_error` after closing the stream. If set,
   raise a new `AudioCaptureError` (define in `voice/audio.py` alongside the recorder
   class — same pattern as `STTError` and `TTSError`).
3. `ConversationOrchestrator._consume_recording_and_dispatch_turn` (the factored helper
   from 12.3) catches `AudioCaptureError`, calls `_handle_error` with a user-facing
   message ("Microphone disconnected — please reconnect and try again"), state goes to
   ERROR, auto-recovers in 3s as usual.
4. **Recovery attempt:** after the recorder raises, the recorder is dead. On the next
   `ptt_start`, the orchestrator should detect that recording fails to start (a
   `start_recording()` exception) and try to rebuild the recorder against the default
   device. Implement as:
   - `AudioRecorder.start_recording` raises `AudioCaptureError` on stream-open failure
     (rather than the current silent fail).
   - `on_ptt_start` catches it; calls `self._rebuild_recorder()` which does
     `AudioRecorder()` against the default device (index=None); retries
     `start_recording` once; if that also fails, transitions to ERROR with a message.
5. Broadcast a new event `audio_device_recovered` on successful rebuild so the UI can
   surface "Switched to default microphone."

**Why catch-and-rebuild instead of trying to recover the same device:** Windows
re-enumerates devices on reconnect; the original index may no longer point to the same
device. Default-device fallback is the simplest correct behaviour.

**Watch out for:**

- The rebuilt recorder must use the current `tts_sample_rate` and channel config from
  settings — don't hardcode 16000 in the rebuild path.
- If the recorder error happens during `on_ptt_end` (most common — read fails on
  drain), we're already inside the lock. `_handle_error` is called per its contract.
- The `_inflight` task does not exist at this point (we haven't created it yet — error
  is during the buffer drain). Don't cancel a task that doesn't exist.

**Test path (manual):**

- PTT a sentence (working baseline).
- Hold PTT, mid-utterance unplug the USB mic. Release PTT.
- Expected: UI shows "Microphone disconnected" briefly, recovers to idle after 3s.
- Plug the mic back in; PTT works again (against whatever Windows now considers default).

---

### Task 12.5 — "Test mic" button (60 min, P1)

A 3-second record-and-playback diagnostic. Lives in the settings panel.

**Backend:**

- New route in the voice API (next to the device-list endpoint): `POST /audio/test-mic`
- Behaviour: temporarily acquire the recorder (or build a fresh transient one — see
  decision below), record 3 seconds, play back through default output via the same
  sounddevice pipeline TTS uses.
- Returns a `TestMicResult` with `success: bool`, `peak_amplitude: float`,
  `duration_ms: int`, `error: str | None`.

**Decision to make:** does the test-mic share the main recorder or use a transient one?

- **Shared:** simplest; one device, one recorder. But: cannot run if the user is in
  the middle of a PTT or if the recorder is mid-rebuild.
- **Transient:** open a fresh `InputStream` for 3 seconds, then close. Safe even
  during PTT (different stream). But: opens the device twice if PTT is happening
  simultaneously, which PortAudio may reject.

Recommendation: **shared, but reject the request if `orchestrator.state` is anything
except IDLE or MUTED**. Return HTTP 409 with "Cannot test mic while a conversation is
active." Simpler to reason about. The user won't be testing the mic while talking to
the assistant anyway.

**Frontend:**

- `frontend/src/components/SettingsPanel.tsx`: add a "Test mic" button below the
  device dropdown.
- On click: POST to `/audio/test-mic`. Show a 3-second progress indicator
  ("Recording…"). On response, show a result card:
  - Green check + "Detected audio (peak amplitude X)" if success and peak > some
    threshold (0.05 reasonable for normal speech).
  - Yellow warning + "Silent recording — check your mic" if peak below threshold.
  - Red X + the error message on failure.
- The amplitude meter idea from the v2 plan (live bars) is **out of scope today** —
  it would need WebSocket streaming during the test, which is Day 16 work. A
  post-test peak number is plenty for diagnostics.

**Why this is P1 not P0:** it's a quality-of-life feature, not a bug fix. If 12.4
takes longer than estimated, drop the amplitude threshold colour-coding and just
display a "success/failure" badge.

---

### Task 12.6 — Bluetooth mode-switch + permission denied (45 min, P1)

Two related edge cases, handled together because they share the same code path:
`AudioRecorder.__init__` or `start_recording` failing on device open.

**Bluetooth mode-switch context:** Windows toggles a BT device between
"Hands-Free AG Audio" (headset, 16 kHz, mic enabled) and "Stereo" (headphones, 48 kHz,
no mic) automatically based on application usage. When the assistant requests the
mic, Windows may switch to headset mode — but if another app is using headphones
mode, the switch is denied and we get a PortAudio open error.

**Permission denied** is similar: Windows can deny microphone access (Privacy
settings) and PortAudio surfaces this as a generic open error.

**Implementation:**

1. `AudioRecorder.__init__` already constructs but does not open the stream until
   `start_recording`. In `start_recording`:
   - Wrap the `InputStream(...)` construction in try/except `PortAudioError`.
   - Inspect the error message for keywords (`"Access is denied"`,
     `"Device unavailable"`, `"Unanticipated host error"`).
   - Raise `AudioCaptureError` with a user-facing message:
     - `"Microphone access denied. Open Windows Settings → Privacy → Microphone."`
     - `"Microphone is in use by another application."`
     - `"Microphone unavailable — try reconnecting your headset."` (catch-all)
2. The recovery path in 12.4 already routes through `_handle_error` → user sees the
   message.

**Why string-matching the error:** PortAudio's Python bindings don't expose error
codes cleanly. String inspection is brittle but the only available option. Keep the
patterns in `voice/audio.py` as a `_PORTAUDIO_ERROR_HINTS: dict[str, str]` constant
so they're easy to extend when new variants are found.

**Defer if running long:** keep only the catch-all "Microphone unavailable" message;
skip the keyword classification. Note in `docs/journal.md` that fine-grained error
messages are a Day 13 buffer task.

---

### Task 12.7 — Loud input / clipping verification (15 min, P2)

The v2 plan calls for "very loud input (clipping) → don't crash, let STT fail
gracefully." This is almost certainly already true — the recorder doesn't process
audio, it just records int16 samples; clipping produces saturated samples but no
exception. STT receives the saturated WAV and either transcribes garbled text or
returns its usual error envelope.

**Verification (no code expected):**

1. Speak very loudly into the mic for a PTT recording.
2. Expected: PTT completes; STT returns either garbled text or an error; orchestrator
   either responds to the garbled query or goes through the normal error path.
3. **Do not** crash; **do not** hang.

If the test reveals a crash, escalate to a real task and address. Otherwise, document
in `docs/journal.md` as verified-and-fine.

---

### Task 12.8 — Verification + journal + commit (30 min, P0)

The Day 12 manual verification script. Run all of these on the dev box.

```powershell
# Baseline still works (regression check)
# Hold Alt+Space, say "what's the capital of France?", release.
# Expected: spoken "Paris" within ~5s. (Day 11 happy path unchanged.)

# 12.1a — assertion fires only when violated
# Cannot easily trigger from outside; rely on baseline still passing as evidence the
# assertion isn't firing falsely.

# 12.1b — recordings cleanup
# Do 51 PTT presses (or temporarily lower `recordings_keep_last` to 5 and do 6 presses).
# After the (N+1)th press, count files in data/recordings/.
# Expected: exactly N files; oldest is gone.

# 12.2 — invalid device index rejected
# POST /audio/device with body { "device_index": 999 }
# Expected: HTTP 400; the existing recorder still works on next PTT.

# 12.3 — 30s auto-stop notification
# Hold Alt+Space for 35 seconds, say something brief at the start, hold silently for the rest.
# Expected: at ~30s, UI shows "Recording stopped at 30s limit"; transcript appears for what was said;
#           a response is spoken normally.

# 12.4 — mic disconnect mid-recording (USB mic preferred)
# Hold Alt+Space, mid-utterance unplug the USB mic. Release Alt+Space.
# Expected: UI shows "Microphone disconnected"; state goes to ERROR; auto-recovers in 3s.
# Plug mic back in; PTT works against the now-default device.

# 12.5 — Test mic button
# Open settings panel → click "Test mic". Speak normally for 3s.
# Expected: green check + a peak amplitude value > 0.05.
# Click "Test mic" again, this time silently.
# Expected: yellow warning + low peak amplitude.

# 12.6 — permission denied (if testable)
# Windows Settings → Privacy → Microphone → toggle off; restart backend; try PTT.
# Expected: UI message about permission. Toggle back on before continuing.

# 12.7 — loud input
# Yell into the mic for a 3-second PTT.
# Expected: no crash. Garbled transcript or STT error handled normally.

# 12.8 — Bluetooth (if BT mic available)
# Connect a BT headset; switch active output to BT headphones (mode swap); try PTT.
# Expected: graceful error; rebuild attempts default; either succeeds or shows clear message.
```

Capture any failures in `docs/journal.md` along with the one-line Day 12 summary.

**Commit (logical chunks, not one giant commit — CLAUDE.md rule):**

Suggested commit breakdown:

1. `chore: add _handle_error lock-held assertion and recordings cleanup` (12.1)
2. `fix: validate microphone device before swapping in POST /audio/device` (12.2)
3. `feat: 30s recording cap notifies orchestrator and UI` (12.3)
4. `feat: graceful recovery from microphone disconnect mid-recording` (12.4)
5. `feat: test mic button in settings panel` (12.5)
6. `feat: classify portaudio open errors with user-facing messages` (12.6)
7. `docs: day 12 audio robustness verification notes` (12.8 journal + this plan)

Or one squashed commit `fix: audio device handling and edge cases` if preferred — but
the breakdown above makes future bisects easier if something regresses on Day 15+.

---

## 4. Architectural decisions to discuss before coding

Per CLAUDE.md §"Suggest, don't just write" — the following are non-trivial choices in
this plan. Ask Claude Code which to take before writing code, and record the choice
inline as a comment in the relevant file.

1. **Recordings cleanup strategy** (12.1b): keep-last-N vs delete-older-than-X-days.
   Recommendation in plan: keep-last-N. Confirm before implementing.
2. **30s cap notification mechanism** (12.3): synthetic `ptt_end` vs dedicated
   `recording_cap_hit` event vs watchdog poll. Recommendation: dedicated event.
3. **Test mic recorder scope** (12.5): shared with main recorder (with state guard)
   vs transient instance. Recommendation: shared with IDLE/MUTED guard.
4. **Bluetooth/permission error classification depth** (12.6): keyword-classified
   messages vs single catch-all. Recommendation: classified; degrade to catch-all if
   running long.

For each, the recommendation is the safer/simpler choice that matches the codebase's
existing patterns. Diverge only with a documented reason.

---

## 5. Files expected to change

```
NEW:
  backend/voice/cleanup.py              — prune_recordings helper (12.1b)

EDIT:
  backend/services/conversation.py      — _handle_error assertion (12.1a);
                                          _consume_recording_and_dispatch_turn
                                          helper factored out (12.3);
                                          on_recording_cap_hit (12.3);
                                          catch AudioCaptureError in start_recording
                                          path (12.4); rebuild-recorder fallback (12.4);
                                          prune_recordings hook in _save_recording (12.1b)
  backend/voice/audio.py                — AudioCaptureError class (12.4);
                                          callback try/except + _callback_error (12.4);
                                          start_recording raises on open failure (12.4);
                                          notify_callable plumbing for 30s cap (12.3);
                                          _test_open classmethod (12.2);
                                          PortAudio error classification (12.6)
  backend/api/voice.py                  — POST /audio/device validates via _test_open (12.2);
                                          POST /audio/test-mic endpoint (12.5)
  backend/models/voice.py               — RecordingCapHitEvent (12.3);
                                          AudioDeviceRecoveredEvent (12.4);
                                          TestMicResult (12.5);
                                          add to VoiceEvent union
  backend/main.py                       — notify_callable wiring at recorder construction (12.3);
                                          dispatcher branch for recording_cap_hit (12.3)
  backend/config/settings.py            — recordings_keep_last (default 50),
                                          audio_open_timeout_seconds (default 2.0)
  frontend/src/hooks/useWebSocket.ts    — recording_cap_hit, audio_device_recovered
                                          added to VoiceEvent union (12.3, 12.4)
  frontend/src/App.tsx                  — toast/inline handling for recording_cap_hit
                                          and audio_device_recovered (12.3, 12.4)
  frontend/src/components/SettingsPanel.tsx
                                        — Test mic button + result display (12.5)
  docs/journal.md                       — Day 12 one-liner + verification notes
  docs/plans/day_12_plan.md             — this file (committed alongside code)
```

Approximate diff size: ~250–350 lines added, ~30 lines modified. Within the "minimal
diffs" guidance from CLAUDE.md — most changes are additive.

---

## 6. Skill file updates (end of day)

`.claude/skills/voice-pipeline/SKILL.md` needs these updates after Day 12 lands:

1. **§"Service contracts"** — `AudioRecorder.start_recording` and
   `stop_recording` now raise `AudioCaptureError`. Add the class to the contracts
   section; note that callers must catch it. Drop the "30s max-duration is a silent
   loss today" sentence; replace with the new `recording_cap_hit` event behaviour.
2. **§"The seven-state state machine"** — no change. New events fit existing
   transitions (cap hit acts like ptt_end; disconnect acts like a service failure).
3. **§"Where things broadcast"** — add `recording_cap_hit` and
   `audio_device_recovered` to the event table.
4. **§"Gotchas"** — replace the 30s gotcha with the corrected behaviour. Add a new
   gotcha for Bluetooth mode switching causing PortAudio open failures.

Update `.claude/skills/project-architecture/SKILL.md` **only** if a new top-level
folder or locked-stack item changes (neither does today). It is unlikely to need
edits.

---

## 7. Completion criteria (the v2 plan's checks plus Day 12 specifics)

From `Day_by_Day_Plan_v2.md` §Day 12:

- [ ] Unplug mic mid-conversation → error shown, app doesn't crash
- [ ] Works with at least 2 different mic devices (built-in + USB/Bluetooth)
- [ ] Test mic button works
- [ ] 30s silent recording doesn't lock up app

Adding Day 11 carryover criteria:

- [ ] `_handle_error` has the `assert self._lock.locked()` guard
- [ ] `data/recordings/` is capped (default 50)
- [ ] `POST /audio/device` rejects invalid indices with a clear error
- [ ] 30s auto-stop produces a visible UI notification

Plus the regression check:

- [ ] Day 11 happy path still works end-to-end (PTT → STT → LLM → TTS → IDLE in ~5s)

If all eleven boxes are ticked, Day 12 is done.

---

## 8. Drop-cut order if running short

If only 3 hours are available today rather than the 5 estimated:

1. **Keep:** 12.1, 12.4, 12.3, 12.8 (regression-prone fixes + verification).
2. **Defer to Day 13/14 buffer:** 12.5 (test mic button), 12.6 (Bluetooth/permission
   message classification), 12.2 (device validation).
3. **Always cut last:** 12.4 — mic disconnect handling is the single most likely
   real-world failure on a daily-driver.

The buffer days are exactly the fallback for this. Tag any deferred work in the
journal so it isn't lost.

---

## 9. End-of-day journal entry template

Drop this in `docs/journal.md` after the verification pass:

```
**Day 12** — Audio robustness. Closed all four Day 11 audio carryovers; mic
disconnect now recovers via default-device rebuild within 3s; 30s cap fires a
visible UI notification; Test mic button in settings. PortAudio open errors are
classified into permission/in-use/unavailable user-facing messages. Verified on
[built-in mic / USB mic / BT headset — fill in actually tested]. Next: buffer day 13.
```

---

## 10. Notes to self for the next session

- After Day 12, the recorder is robust enough that Day 13/14 buffer can either polish
  latency (per the v2 plan) or get a head start on Day 15 (SVG/CSS blob). Decide
  based on whether the median end-to-end latency from Day 11 is already under 4s — if
  yes, jump ahead.
- The `recording_cap_hit` event landing today is the first new WS event type added
  since the Day 11 Task 11.2 reducer refactor — good moment to confirm the queue
  pattern still scales when more event types arrive in Week 4 with tool-calling.
- If Bluetooth handling proves flaky in real use over Days 13–17, consider a Day 17
  settings toggle to "Force default mic" that disables device-switching entirely. Out
  of scope today; capture as a buffer-day idea only if observed.
