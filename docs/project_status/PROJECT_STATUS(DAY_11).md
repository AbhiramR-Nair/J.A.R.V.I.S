# Project Status — Day 11

**Period covered:** Day 11 (Full PTT Voice Loop + State Machine + Mute)
**Status:** Complete — Week 2 milestone hit. Tagged `v0.2.0-voice-loop`.
**Environment:** Windows 11, Python 3.13.5, FastAPI, React 18 + Vite, Groq Whisper-large-v3, Piper `en_US-lessac-medium`, Gemini 2.5 Flash

> Checkpoint summary for Day 11: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 12.

---

## 1. What has been done

Day 11 stitched the three separately-working pieces (AudioRecorder, STTService, TTSService)
into a single owned pipeline: `ConversationOrchestrator`. The assistant can now receive a
voice question via PTT, transcribe it, think, and speak a reply — end-to-end — with live
state labels in the UI and a working mute that cuts in cleanly at any stage.

| Task | What landed | Status |
|---|---|---|
| 11.1 — WS event contract | `StateChangedEvent`, `AssistantMessageEvent`, `SpeakingStarted/Ended/Failed` models in `backend/models/voice.py`; `VoiceState` string enum exported | Done |
| 11.2 — `useVoiceEvents` reducer refactor | `lastEvent` pattern replaced with `useReducer` FIFO queue; 5 new Day 11 event types added to `VoiceEvent` union; queue cleared on WS disconnect | Done |
| 11.3 — Orchestrator skeleton + lifespan | `backend/services/conversation.py` skeleton; `ConversationOrchestrator` wired into `main.py` lifespan; LIFO shutdown: conversation → tts → stt → recorder | Done |
| 11.4 — Route WS events to orchestrator | `_handle_event_side_effects` in `main.py` reduced from 50 lines of inline audio/STT logic to 3 routing branches; `_save_recording` and STT pipeline removed from main | Done |
| 11.5 — Happy path pipeline | `on_ptt_start`, `on_ptt_end`, `_process_turn` / `_run_pipeline` implemented; full PTT → STT → LLM → TTS → IDLE loop with per-step MUTED re-checks; `_save_recording` moved into orchestrator; `TTSService.cancel_playback()` added | Done |
| 11.6 — Mute toggle | `on_mute_toggle` handles all 6 states; side effects (recorder stop, playback cancel, task cancel) run outside the lock after state is already MUTED | Done |
| 11.7 — Memory integration | `_build_context` (last-4 recency + top-3 semantic, deduplicated, char-capped); `_persist_turn` (SQLite always + ChromaDB if importance ≥ 4.0); `sqlite_store.get_recent_project_messages()` added | Done |
| 11.8 — Race-condition guards | All 5 guards (G1–G5) verified present in code; automated grep check passed 10/10 | Done |
| 11.9 — Frontend state label | `<div>{voiceState}</div>` added below status badge; fed by `state_changed` events from orchestrator | Done |
| 11.10 — Verification + commit | Guard smoke tests passed; G4 auto-recovery timed at exactly 3s; journal updated; commit `0722393`; tag `v0.2.0-voice-loop` | Done |

**Week 2 milestone verified:**
> "I can hold Alt+Space, ask 'what's the capital of France?', and hear a spoken answer within 4 seconds."

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. asyncio.Lock — held only for state mutation, released across I/O

The most important architectural decision of Day 11. All three service calls (STT, LLM, TTS)
are network or subprocess round-trips taking 0.5–2 seconds each. If the lock were held across
any of them, `on_mute_toggle` and `on_ptt_start` would block for the entire duration of the call
before they could read `self._state` — the mute button would feel dead.

The pattern used throughout `_run_pipeline`:
```python
async with self._lock:       # ← acquire
    check_state()
    await self._transition(new_state)
# ← lock released here
result = await expensive_io()    # STT, LLM, or TTS — runs without lock
async with self._lock:       # ← re-acquire to mutate again
    if self._state == VoiceState.MUTED:  # re-check (state may have changed during I/O)
        return
    await self._transition(next_state)
```

The MUTED re-check on every re-acquire is what makes the mute button feel instantaneous
regardless of which stage of the pipeline the turn is currently at.

### 2. `_process_turn` wraps the pipeline as a Task, not an inline await

`on_ptt_end` spawns `asyncio.create_task(_process_turn(wav_bytes))` and returns immediately.
If it awaited inline, the WS dispatcher would be blocked for the entire STT→LLM→TTS duration
(~4s), unable to receive any further events including the mute toggle. The Task runs
concurrently on the same event loop and can be cancelled at any await point.

The task reference is stored on `self._inflight` rather than as a bare expression — a bare
`asyncio.create_task(...)` can be silently garbage-collected before it completes.

### 3. `_handle_error` is called with the lock already held, not acquiring its own

`_handle_error` calls `_transition` which also requires the lock to be held. Rather than
releasing and re-acquiring (which would create a window for a race), the caller acquires the
lock once for the whole error path: check MUTED guard → broadcast error-specific event →
call `_handle_error` → which calls `_transition(ERROR)` → which spawns `_auto_recover`.
This is a deliberate design constraint documented in the method's docstring.

Callers that get this wrong (calling `_handle_error` without the lock) will deadlock on the
`_transition` call's attempt to broadcast (which is an async call the lock also doesn't guard,
so actually it won't deadlock — but the state won't be properly serialised). Worth being
careful about in future error-handling additions.

### 4. Mute side effects run *after* lock release, capture references *before* release

`on_mute_toggle` does the state transition inside the lock, captures `inflight_to_cancel`
and `do_stop_recorder` / `do_cancel_playback` flags, then releases the lock and runs side
effects. This pattern is required because:

- `recorder.stop_recording()` is a blocking PortAudio call that must run via `run_in_executor`
  (i.e. it has an `await` — can't hold an asyncio lock across it without purpose)
- `tts.cancel_playback()` is async (wraps `sd.stop()` in an executor)
- `task.cancel()` is sync but modifying `_inflight` outside the lock is safe here because we
  captured the reference before release and `_inflight` is only ever set in `on_ptt_end`
  (which can't run concurrently — it would find state MUTED and drop immediately)

### 5. `_save_recording` moved into the orchestrator

`STTService.transcribe()` takes a `Path`, not bytes (it opens the file with `audio_path.open("rb")`
to stream to Groq's API). The original `_save_recording` helper lived in `main.py` and was
removed in Task 11.4 when the dispatcher was gutted. Rather than modifying the STT service
interface (which would ripple into `test_tts_smoke.py` and the Day 12 audio robustness
tests), `_save_recording` was re-created as a private method on the orchestrator. It writes
via `run_in_executor` to keep the event loop free during the file write.

### 6. `_persist_turn` runs before TTS, not after

The turn is saved to SQLite and importance-scored *before* `tts.speak()` is called. This
means if TTS fails, the user never hears the response — but the exchange is already in
memory. This is intentional: the LLM has already done the work and produced a valid answer;
the failure was in audio delivery, not in content. Saving before TTS also means a mute-during-
speaking won't cause the turn to be lost from memory.

### 7. Importance scoring is a second LLM call — deliberately non-fatal

`importance.score()` calls the LLM router with a scoring prompt. If this fails (Groq quota,
network blip), `_persist_turn` catches the exception, logs a warning, and continues. The
SQLite rows are already saved; only the ChromaDB entry is skipped. This means during API
outages, memory still accumulates in SQLite (queryable via SQL), just not in the vector store
(not semantically searchable). An acceptable degradation for a personal daily-driver.

### 8. `useVoiceEvents` reducer queue — why the old pattern breaks at Day 11

Before Day 11, the hook used `useState<VoiceEvent | null>` with `setLast(ev)`. React 18's
automatic batching means two state updates within the same microtask tick are merged —
the second wins. With five new event types emitted in rapid succession
(`state_changed` + `transcription_complete` + `state_changed` again + `assistant_message`
+ `speaking_started` all within ~100ms), 4 of the 5 would have been silently dropped.

The `useReducer` queue pushes events to a FIFO array. A `useEffect` depending on `events`
fires when the array changes, processes `events[0]`, dispatches `event_consumed` (shifts
head), which changes `events`, which re-fires the effect for the next event. One event per
render cycle — no drops, regardless of how fast they arrive.

---

## 3. Problems faced and how they were handled

### Problem 1 — STTService interface mismatch discovered mid-implementation

**What happened:** Task 11.4 removed `_save_recording` from `main.py` as part of gutting
the old dispatcher. Only while implementing `_run_pipeline` in Task 11.5 was it discovered
that `STTService.transcribe()` takes a `Path`, not `bytes` — the WAV bytes from
`recorder.stop_recording()` cannot be passed directly to the STT service.

**Root cause:** The Day 10 STT integration (Day 9) saved WAV files via `_save_recording` in
`main.py` and passed the path to the service. When the dispatcher was simplified in Task
11.4, `_save_recording` was removed under the assumption that the orchestrator would call
STT directly with bytes. That assumption was wrong.

**Fix:** `_save_recording` was recreated as a private method on `ConversationOrchestrator`
with the same semantics: write WAV bytes to `data/recordings/` via `run_in_executor`, return
the `Path`. No changes to `STTService` — the interface was correct; only the caller changed.

**Why not change `STTService.transcribe()` to accept bytes instead?** Groq's Python SDK
uses the `(filename, file_obj, content_type)` tuple pattern — a file-like object, not raw
bytes. Making STT accept bytes would require creating a `BytesIO` wrapper internally, which
is functionally equivalent but hides the intent. The Path interface is also friendlier for
debugging (the WAV file is inspectable on disk).

### Problem 2 — `_handle_error` lock contract not immediately obvious

**What happened:** During implementation, the initial skeleton had `_handle_error` acquire
its own lock (`async with self._lock:`). This would deadlock: callers hold the lock when
they call `_handle_error`, and `asyncio.Lock` is non-reentrant — a second `async with`
on the same lock from the same coroutine blocks forever.

**Fix:** `_handle_error` was designed to be called with the lock already held. The contract
is documented in the method's docstring. Callers (error branches in `_run_pipeline`)
always call it inside an `async with self._lock:` block. The `_auto_recover` task acquires
its own lock separately (it runs later, after the current lock is released).

**Downstream risk:** Any future method that calls `_handle_error` *without* holding the lock
will not deadlock (since the fix removed the inner acquire), but `_transition` — which
`_handle_error` calls — will mutate state without the lock, creating a race. The convention
must be maintained. It would be worth adding an assertion `assert self._lock.locked()` at
the top of `_handle_error` to make violations visible immediately during development.

### Problem 3 — No hard problem surfaced, but one subtle ordering gotcha worth recording

**What happened:** During testing of the guard smoke tests, `G4` (ERROR auto-recovery) was
validated by manually entering ERROR state and waiting 3.2 seconds. The log timestamps
confirmed recovery fired at exactly 3.021 seconds — `asyncio.sleep(3)` is accurate enough.

**Latent issue noticed but not triggered:** `asyncio.create_task(_auto_recover())` is called
inside `_handle_error` while the lock is held. `_auto_recover` begins with
`await asyncio.sleep(3)` before it acquires the lock. The task is scheduled but won't run
until the current lock holder releases. This is correct — but if `close()` is called during
the 3-second sleep (e.g. backend shuts down mid-error), the recovery task will be cancelled
by `close()`. The `close()` method now cancels `_recovery_task` explicitly for this reason.

---

## 4. Heads-up: downstream complications to watch

### `data/recordings/` grows without bound

Every PTT press saves a WAV file to `data/recordings/`. At 16 kHz mono int16, a 5-second
utterance is ~160 KB. At 20 queries/day that's ~3.2 MB/day, ~96 MB/month — not alarming,
but unattended it accumulates indefinitely.

**Implication:** Day 12 (audio robustness) is a good time to add a simple cleanup: keep
only the last N recordings (e.g. 50), or delete files older than 7 days. Until then, manual
`Remove-Item data\recordings\*.wav` is enough.

### Importance scoring adds a background LLM call after every turn

`_persist_turn` calls `importance.score()` after the voice response has already been spoken.
This makes a second Gemini/Groq API call per turn (the scorer uses the LLM router). The call
is fire-and-await — it blocks `_run_pipeline` between "Done → IDLE" and the actual IDLE
transition is already made before `_persist_turn` is called... wait, actually:
`_persist_turn` is called before TTS (before the SPEAKING transition). So the importance
scoring happens while the user is already hearing the response. But it's still an extra
LLM call that could exhaust Gemini's free tier faster than expected.

**Implication:** Monitor `data/jarvis.db` cost_log. If importance scoring is eating into
the 1500 req/day Gemini Flash quota, consider batching the scoring (run it every 5 turns
rather than every turn), or lowering `importance_threshold` so fewer turns reach the
ChromaDB write (the LLM call is always made regardless of threshold — the threshold only
controls whether ChromaDB is written).

### The `_build_context` recency window pulls across all conversations in a project

`get_recent_project_messages(project_id, limit=4)` fetches the last 4 messages across *all*
conversations for the active project, not just the current session conversation. On the
first query of a new session, the "recent" messages will be from the previous session. For
most research use cases this is desirable (continuity). But if the user switches topics
mid-session or uses the project for something completely different, the old context may be
noise rather than signal.

**Implication:** This is acceptable v1 behaviour. If context pollution becomes noticeable
("why does it keep mentioning ABL1 when I'm asking about something else?"), the fix is to
filter `get_recent_project_messages` by today's date, or weight recency by age. Defer to
Day 21 (project memory tools) when this can be addressed properly.

### String-equality deduplication in `_build_context` misses paraphrases

The dedup step in `_build_context` compares `m["content"]` exact-match against ChromaDB
result text. A ChromaDB hit that's a paraphrase or reformatting of a recent message will
not be deduplicated — it will appear in both sections. In practice this means the LLM
might see a message once as "user: bench press 3 sets of 8" and again as "User: bench press
3 sets of 8\nAssistant: Logged to your fitness project." (the combined ChromaDB string).

**Implication:** Minor noise, unlikely to affect answer quality. Fix in Day 21 if needed:
strip role prefixes before comparing, or do a startswith check against the combined string.

### Muting during SPEAKING: two signals race to stop audio

When the user mutes while speaking, two things happen:
1. `tts.cancel_playback()` calls `sd.stop()` — audio stops immediately
2. `_inflight.cancel()` injects `CancelledError` into the task at its next await

Because `tts.cancel_playback()` is awaited first (before `task.cancel()`), `sd.stop()` fires
before the task cancellation. `_play_sync` returns, the executor completes, `tts.speak()`
returns normally, and `_run_pipeline` hits the "Done" lock re-check (`if state == MUTED:
return`) — it sees MUTED and exits cleanly. Then `task.cancel()` fires on an already-done
task, which is a no-op. The outcome is correct.

The risk: if `_inflight.cancel()` fires *before* `sd.stop()` completes (unlikely given they
run sequentially after lock release, but theoretically possible on a slow executor thread),
the task sees `CancelledError` at `await tts.speak()`. `speak()` is currently awaiting
`run_in_executor`. Python's asyncio cannot forcibly cancel a running executor thread —
`run_in_executor` returns the `CancelledError` only after the executor thread finishes. This
means audio plays to completion before the cancellation propagates. The mute button appears
to have no effect for the duration of the current synthesis.

**Implication:** In practice this race doesn't matter because `cancel_playback()` (which runs
first) will have already called `sd.stop()` before `task.cancel()` even fires. But if this
ordering assumption ever breaks (e.g. the executor is overwhelmed and `sd.stop()` is slow),
muting during SPEAKING may feel sluggish. Fix if observed: call `task.cancel()` before
`cancel_playback()`, then `cancel_playback()` ensures the executor finishes fast.

### `_handle_error` lock contract can be silently violated

As described in Problem 2 above: `_handle_error` must be called with `self._lock` held.
There is no runtime enforcement of this. Future error paths added to `on_mute_toggle` or
`on_ptt_start` (which don't currently call `_handle_error`) could call it while not holding
the lock, causing unsynchronised state mutation.

**Implication:** Add `assert self._lock.locked()` as the first line of `_handle_error` as
a development-mode guard. Remove it (or wrap in `if __debug__:`) before any performance-
sensitive benchmarking. Until then, the convention is: `_handle_error` is only called from
inside `async with self._lock:` blocks in `_run_pipeline`.

### Day 9/10 carryovers still open

None of these were touched by Day 11:
- `POST /audio/device` does not validate that the new device can actually be opened
- 30s PTT auto-stop in `AudioRecorder._on_audio` sets `_recording = False` but does not
  cancel the `_inflight` task — the orchestrator's `on_ptt_end` will fire as usual and the
  (full) buffer will be transcribed. But the user won't know the auto-stop fired. Should
  broadcast a `transcription_failed`-style warning or at minimum a log-visible event.
- `tts_sample_rate` is hardcoded at 22050 Hz; a voice swap without updating settings
  produces chipmunk audio with no error signal

---

## 5. How to verify Day 11

```powershell
# 1. Clean start
netstat -ano | findstr :8000
# Stop-Process -Id <PID> if anything shows

# 2. Launch
cd frontend; npm run dev
# new terminal:
python -m backend.desktop

# 3. Confirm FOUR init lines in this exact order:
#    audio recorder initialized
#    stt service initialized: model=whisper-large-v3
#    tts service initialised: voice=en_US-lessac-medium.onnx
#    conversation orchestrator initialized — state=idle

# 4. Happy path
#    Hold Alt+Space, say "what's the capital of France?", release
#    Expected:
#      - UI state label cycles: idle → listening → transcribing → thinking → speaking → idle
#      - Hear spoken answer within ~5s of releasing Alt+Space
#      - assistant_message event visible in browser dev tools WS inspector

# 5. Multi-turn recency
#    PTT: "I'm working on the ABL1 kinase project."
#    Wait for spoken response.
#    PTT: "What did I just say I was working on?"
#    Expected: response mentions ABL1 / kinase

# 6. Mute from IDLE
#    Ctrl+Alt+J → label shows "Muted"
#    Try PTT → nothing happens, label stays "Muted"
#    Ctrl+Alt+J → label returns to "idle"
#    PTT → works again

# 7. Mute mid-recording (LISTENING → MUTED)
#    Hold Alt+Space, then while holding: Ctrl+Alt+J
#    Release Alt+Space
#    Expected: state went LISTENING → MUTED; releasing PTT did nothing

# 8. Mute mid-speaking (SPEAKING → MUTED)
#    PTT: "tell me a long fact about kinase inhibitor resistance"
#    Wait for audio to start playing
#    Mid-sentence: Ctrl+Alt+J
#    Expected: audio cuts off immediately; label → Muted

# 9. ERROR auto-recovery (break and restore Groq key)
#    Edit .env: add 'x' to start of GROQ_API_KEY
#    Restart backend
#    PTT a sentence
#    Expected: transcription_failed event; label → error; 3s later → idle
#    Restore .env, restart

# 10. Concurrent PTT defense
#    PTT a question; while response is playing, press Alt+Space again
#    Expected: second PTT dropped (log warns "ptt_start in speaking — ignored")
#              first response completes normally

# 11. Clean shutdown
#    Close PyWebView window
#    Confirm log shows LIFO shutdown:
#      conversation orchestrator closed
#      tts service closed
#      stt service closed
#      (recorder stopped if was recording)
```

---

## 6. Open items before Day 12

- [ ] Add `assert self._lock.locked()` to `_handle_error` as a development-mode guard
- [ ] `data/recordings/` cleanup — keep last N or delete older than 7 days (Day 12 or buffer day)
- [ ] 30s PTT auto-stop does not notify the orchestrator — `AudioRecorder._on_audio` sets
      `_recording = False` silently; on_ptt_end picks up the full buffer correctly but the
      user gets no feedback that the cap fired
- [ ] Day 10 carryover — `tts_sample_rate` is hardcoded; read from `.onnx.json` sidecar
      when voice swapping becomes a feature (Day 17 settings panel)
- [ ] Day 8/9 carryover — `POST /audio/device` does not validate the new device opens cleanly
- [ ] The `statusLabel` in `App.tsx` now has redundant old-style derivation (muted, transcribing
      flags) alongside the new `voiceState` from `state_changed`. Clean up once Day 15 blob
      replaces the status badge entirely — not worth touching before then

---

## 7. Files changed this day

```
NEW:
  backend/services/conversation.py    — ConversationOrchestrator (state machine,
                                        full pipeline, mute, memory integration)
  docs/plans/day_11_plan.md           — day plan (committed alongside code)

EDIT:
  backend/models/voice.py             (+VoiceState enum; +StateChangedEvent,
                                       AssistantMessageEvent, SpeakingStarted/
                                       Ended/Failed event models)
  backend/voice/tts.py                (+cancel_playback() async method)
  backend/config/settings.py          (+error_recovery_seconds, recent_messages_limit,
                                       semantic_k, context_char_cap)
  backend/main.py                     (removed Path/datetime/STTError imports;
                                       removed _save_recording; replaced
                                       _handle_event_side_effects with 3-line router;
                                       added ConversationOrchestrator to lifespan)
  backend/memory/sqlite_store.py      (+get_recent_project_messages())
  frontend/src/hooks/useWebSocket.ts  (lastEvent → useReducer queue; 5 new event
                                       types; queue cleared on disconnect)
  frontend/src/App.tsx                (consumption pattern updated to queue;
                                       voiceState state added; handles state_changed/
                                       assistant_message/speaking_failed; voice state
                                       label div added)
  docs/journal.md                     (+Day 11 one-liner)
```

---

## 8. Commit

```
0722393 feat: full ptt voice loop with state machine and mute

- ConversationOrchestrator in backend/services/conversation.py: seven-state
  machine (IDLE/LISTENING/TRANSCRIBING/THINKING/SPEAKING/MUTED/ERROR),
  asyncio.Lock serialising all state mutations, in-flight task tracking for mute
- Full PTT pipeline: ptt_start -> STT -> LLM -> TTS -> IDLE with per-step
  MUTED re-checks and graceful error transitions; all turns persisted to SQLite
- Memory: _build_context (last-4 recency + top-3 semantic, deduped, char-capped),
  _persist_turn (SQLite always; ChromaDB if importance score >= threshold)
- Mute toggle: cancels inflight task, stops recorder or TTS playback via sd.stop()
  depending on current state; ERROR -> MUTED cancels auto-recovery
- TTSService.cancel_playback() added (sd.stop in executor)
- WS event contract: StateChangedEvent, AssistantMessageEvent, SpeakingStarted/
  Ended/Failed models added to backend/models/voice.py; VoiceState enum exported
- main.py: dispatcher reduced to thin routing layer; _save_recording and inline
  STT pipeline removed; ConversationOrchestrator wired into lifespan (LIFO shutdown)
- Frontend useVoiceEvents: lastEvent -> useReducer queue; 5 new event types added;
  queue cleared on WS disconnect; App.tsx handles state_changed/assistant_message/
  speaking_failed; voiceState label added
- sqlite_store.get_recent_project_messages() added for recency context
- Settings: error_recovery_seconds, recent_messages_limit, semantic_k, context_char_cap
```
