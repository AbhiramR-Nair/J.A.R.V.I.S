# Skill: Voice Pipeline (research-jarvis)

## When this applies

Read this skill before:

- Touching `backend/services/conversation.py` (the orchestrator)
- Adding or modifying any service in `backend/voice/` (`audio.py`, `stt.py`, `tts.py`)
- Adding a new WebSocket event type that the voice loop emits or consumes
- Adding a new entry point that wants to behave like PTT (Day 27 wake word; any future trigger)
- Adding tool calling inside the LLM stage (Day 20) — tool execution must respect MUTED
- Debugging audio capture, transcription, TTS, mute, or state transitions
- Changing any service contract (e.g. what `STTService.transcribe()` accepts)
- Adjusting the memory-injection or persistence logic inside a turn

Do NOT touch `services/conversation.py` without reading the §"The Lock pattern" section.
That pattern is what keeps mute responsive and the pipeline cancellable. Getting it wrong
is the most likely way to introduce a subtle deadlock or a feel-bad UX regression.

## One-line description

The voice pipeline turns an Alt+Space press into a spoken reply. Four subsystems —
`AudioRecorder`, `STTService`, the LLM router, and `TTSService` — are orchestrated by
a single `asyncio.Lock`-protected state machine in `backend/services/conversation.py`.
State changes drive the React UI through the existing WebSocket dispatcher.

## Pipeline at a glance

```
  Alt+Space (held)
        │
        ▼ pynput.Listener (OS thread)
        │
        ▼ loop.call_soon_threadsafe → asyncio.Queue
        │
  ┌─────────────────────────────────────────────────┐
  │ Event dispatcher (backend/main.py)              │
  │   _dispatch_events → _handle_event_side_effects │
  │   Routes to ConversationOrchestrator methods    │
  └─────────────────────────────────────────────────┘
        │
        ▼
  ┌─────────────────────────────────────────────────────┐
  │  ConversationOrchestrator                           │
  │  (backend/services/conversation.py)                 │
  │                                                     │
  │   on_ptt_start  ─► recorder.start_recording()       │
  │   on_ptt_end    ─► recorder.stop_recording()        │
  │                    create_task(_process_turn)       │
  │   on_mute_toggle ─► cancel inflight; flip state     │
  │                                                     │
  │   _run_pipeline:                                    │
  │     TRANSCRIBING → stt.transcribe(path)             │
  │       │ MUTED re-check                              │
  │     THINKING  → _build_context + llm.router.generate│
  │       │ MUTED re-check                              │
  │     SPEAKING  → _persist_turn + tts.speak()         │
  │       │ MUTED re-check                              │
  │     IDLE                                            │
  └─────────────────────────────────────────────────────┘
        │
        ▼  state_changed / assistant_message / speaking_* events
  ┌────────────────────────────────────────────────────┐
  │ WebSocket (ws_manager.broadcast)                   │
  │ → React useVoiceEvents reducer queue               │
  │ → voice state label + ChatPanel update             │
  └────────────────────────────────────────────────────┘
```

## The seven-state state machine

`VoiceState` is a string enum exported from `backend/models/voice.py`. The orchestrator
owns `self._state: VoiceState` and mutates it only inside `self._lock`.

```
                   ┌─────────────────────────────┐
                   │                             │
                   │            IDLE             │
                   │  (default; PTT accepted)    │
                   │                             │
                   └─────┬───────────────────────┘
                         │ ptt_start
                         ▼
                   ┌─────────────────────────────┐
                   │         LISTENING           │  recorder running
                   └─────┬───────────────────────┘
                         │ ptt_end (with audio)
                         ▼
                   ┌─────────────────────────────┐
                   │       TRANSCRIBING          │  Groq Whisper
                   └─────┬───────────────────────┘
                         │ stt OK
                         ▼
                   ┌─────────────────────────────┐
                   │         THINKING            │  memory + LLM
                   └─────┬───────────────────────┘
                         │ llm OK
                         ▼
                   ┌─────────────────────────────┐
                   │         SPEAKING            │  Piper TTS
                   └─────┬───────────────────────┘
                         │ tts done
                         ▼
                      (back to IDLE)

   Orthogonal absorbing states (enter from anywhere):

     MUTED  ← mute_toggle from any non-MUTED state.
              Blocks PTT entirely.
              Cancels self._inflight; stops recorder or TTS playback.
              mute_toggle from MUTED → IDLE.

     ERROR  ← any service failure (STT / LLM / TTS).
              3-second auto-recovery → IDLE (settings.error_recovery_seconds).
              mute_toggle during ERROR cancels auto-recovery and goes to MUTED.
```

### Transition rules

| Trigger              | Allowed from           | Goes to        | Notes                                                |
|----------------------|------------------------|----------------|------------------------------------------------------|
| `ptt_start`          | `IDLE` only            | `LISTENING`    | Dropped in every other state (warn-log)              |
| `ptt_end`            | `LISTENING` only       | `TRANSCRIBING` | Empty audio → broadcasts `transcription_failed`      |
| stt success          | `TRANSCRIBING`         | `THINKING`     | Internal                                             |
| llm success          | `THINKING`             | `SPEAKING`     | Internal; `_persist_turn` runs *before* TTS          |
| tts done             | `SPEAKING`             | `IDLE`         | Internal                                             |
| `mute_toggle`        | any non-`MUTED`        | `MUTED`        | Cancels `self._inflight`; stops recorder/playback    |
| `mute_toggle`        | `MUTED`                | `IDLE`         | Auto-recovery from `ERROR` is cancelled here too     |
| any service failure  | `TRANSCRIBING`/`THINKING`/`SPEAKING` | `ERROR` | Auto-recovers to `IDLE` after 3s        |

The 5 race-condition guards (G1–G5) verified in Day 11 are the code-level expression of
these rules. If you change a transition, re-run the Day 11 grep check.

## The Lock pattern (critical — read before editing the orchestrator)

This is the single most important rule in the voice pipeline.

**Hold `self._lock` only for state mutation. Release it across every network call,
subprocess call, audio I/O, and `run_in_executor`. Re-acquire to mutate again, and
re-check `MUTED` on every re-acquire.**

```python
# RIGHT — the canonical pipeline pattern
async with self._lock:
    if self._state == VoiceState.MUTED:
        return
    await self._transition(VoiceState.TRANSCRIBING)
# ← lock released

result = await self._stt.transcribe(path)        # network round-trip, lock-free

async with self._lock:
    if self._state == VoiceState.MUTED:           # re-check — state may have flipped
        return
    await self._transition(VoiceState.THINKING)
# ← lock released
```

```python
# WRONG — holds lock across STT call (~1s). Mute hotkey blocks until STT returns.
async with self._lock:
    await self._transition(VoiceState.TRANSCRIBING)
    result = await self._stt.transcribe(path)
    await self._transition(VoiceState.THINKING)
```

### Why this matters

STT, LLM, and TTS calls each take 0.5–2 seconds. If the lock is held across any of
them, `on_mute_toggle` and `on_ptt_start` block waiting for the lock — which means the
mute button is dead for up to 5 seconds during a typical turn. The MUTED re-check on
every re-acquire is what makes mute feel instantaneous regardless of which stage the
pipeline is in.

### The one exception: `_handle_error` is called with the lock already held

`_handle_error` calls `_transition(ERROR)` internally, which requires the lock. To
avoid release/re-acquire windows during error broadcasting, the caller holds the lock
for the whole error path:

```python
async with self._lock:
    if self._state == VoiceState.MUTED:
        return
    await ws_manager.broadcast({"type": "transcription_failed", "error": msg})
    await self._handle_error(msg)   # transitions to ERROR; spawns _auto_recover
```

This is a deliberate constraint documented in `_handle_error`'s docstring. Don't move
`_handle_error` outside the lock without re-thinking the broadcasting sequence.

A development-time assertion (`assert self._lock.locked()` at the top of
`_handle_error`) is an open item from the Day 11 status doc — add it when convenient.

## The full PTT pipeline walkthrough

The pipeline lives in three methods. Walk this top-down before changing any of them.

### `on_ptt_start(self)`

1. Acquire `self._lock`.
2. Guard: if `self._state != VoiceState.IDLE`, warn-log "ptt_start in {state} — ignored"
   and return. This is G1 (concurrent PTT defense).
3. `await self._transition(VoiceState.LISTENING)` — broadcasts `state_changed`.
4. Release lock.
5. Call `self._recorder.start_recording()` via `run_in_executor` (PortAudio blocking
   C call; must run off the event loop).

### `on_ptt_end(self)`

1. Acquire `self._lock`.
2. Guard: if `self._state != VoiceState.LISTENING`, drop. This handles the
   "mute mid-recording → release PTT" path; `on_mute_toggle` already stopped the
   recorder and flipped state to MUTED, so `on_ptt_end` finds the wrong state and
   returns cleanly.
3. Call `self._recorder.stop_recording()` via `run_in_executor` → `wav_bytes`.
4. If `wav_bytes == b""`: broadcast `transcription_failed` with
   "I didn't hear anything.", call `_handle_error`, return.
5. Release lock.
6. `self._inflight = asyncio.create_task(self._process_turn(wav_bytes))`.
   **The reference assignment is required** — a bare `asyncio.create_task(...)` is
   subject to silent GC cancellation (see `project-architecture/SKILL.md` gotchas).
   `self._inflight` also lets `on_mute_toggle` cancel the in-progress turn.

### `_process_turn(self, wav_bytes)` / `_run_pipeline(self, wav_bytes)`

The wrapper exists so a top-level `try/except` can capture anything the pipeline
raises and route it through `_handle_error`. The pipeline itself:

1. **Save WAV** — `_save_recording(wav_bytes)` writes to `data/recordings/{iso8601}.wav`
   via `run_in_executor` and returns `Path`. Lives in the orchestrator (NOT in
   `main.py` any more — Day 11 moved it here because `STTService.transcribe()` takes
   `Path`, not bytes, and the file write is naturally bound to the turn's lifecycle).
2. **TRANSCRIBE** — re-acquire lock, MUTED re-check, transition to TRANSCRIBING.
   Release lock. `text = await self._stt.transcribe(path)`. ~800–1100ms.
3. **THINK** — re-acquire lock, MUTED re-check, transition to THINKING. Release lock.
   - `context = await self._build_context(text)` — recency + semantic memory.
   - `reply = await llm_router.generate(prompt_with_context)`.
4. **PERSIST + SPEAK** — re-acquire lock, MUTED re-check, transition to SPEAKING.
   Release lock.
   - `await self._persist_turn(text, reply)` — *before* TTS, see §"Memory integration".
   - Broadcast `assistant_message {role: "assistant", text: reply}`.
   - Broadcast `speaking_started`.
   - `await self._tts.speak(reply)` — blocks until playback completes (or
     `cancel_playback` interrupts).
   - Broadcast `speaking_ended`.
5. **DONE** — re-acquire lock, MUTED re-check, transition to IDLE.

Each "MUTED re-check" is a literal `if self._state == VoiceState.MUTED: return`. The
pipeline silently bows out; `on_mute_toggle` has already broadcast the MUTED state.

### Where things broadcast

| Event                       | Emitted by                          | Notes                                      |
|-----------------------------|-------------------------------------|--------------------------------------------|
| `state_changed`             | `_transition`                       | Every state mutation                       |
| `transcription_complete`    | After STT                           | `{text, latency_ms}`                       |
| `transcription_failed`      | STT exception or empty audio        | `{error}`                                  |
| `assistant_message`         | After LLM + persist, before TTS     | `{role, text}`                             |
| `speaking_started`          | Before `tts.speak()`                |                                            |
| `speaking_ended`            | After `tts.speak()` returns         |                                            |
| `speaking_failed`           | TTS exception                       | `{error}`                                  |
| `recording_saved`           | NOT broadcast — queue-internal only | See §"Gotchas"                             |

## Mute handling

`on_mute_toggle` is the only entry point that can act from any non-MUTED state.
Pattern: do the state work inside the lock, capture side-effect references, release
the lock, then run side effects.

```python
async def on_mute_toggle(self):
    inflight_to_cancel = None
    do_stop_recorder = False
    do_cancel_playback = False

    async with self._lock:
        if self._state == VoiceState.MUTED:
            await self._transition(VoiceState.IDLE)
            # _auto_recover (if any) is also cancelled here
            return

        # Coming from any other state.
        inflight_to_cancel = self._inflight
        if self._state == VoiceState.LISTENING:
            do_stop_recorder = True
        elif self._state == VoiceState.SPEAKING:
            do_cancel_playback = True
        # ERROR → MUTED is fine; nothing to clean up beyond cancelling auto-recover

        await self._transition(VoiceState.MUTED)
    # ← lock released

    if inflight_to_cancel is not None:
        inflight_to_cancel.cancel()
    if do_stop_recorder:
        await asyncio.get_running_loop().run_in_executor(
            None, self._recorder.stop_recording
        )
    if do_cancel_playback:
        await self._tts.cancel_playback()
```

### Why side effects run outside the lock

- `recorder.stop_recording()` is a PortAudio blocking call — `run_in_executor`, so
  there is an `await` and we cannot hold an asyncio lock across it.
- `tts.cancel_playback()` is async (wraps `sd.stop()` in an executor).
- `task.cancel()` is sync, but capturing `self._inflight` before releasing the lock
  is safe because `self._inflight` is only ever assigned in `on_ptt_end`, and
  `on_ptt_end`'s guard ensures it can't run concurrently with `on_mute_toggle`
  (the state is already MUTED by the time `on_ptt_end` reaches its guard).

### What gets cancelled

| From state     | Inflight task? | Recorder?           | Playback?       | Auto-recover?  |
|----------------|---------------|---------------------|-----------------|----------------|
| IDLE → MUTED   | (none)        | no                  | no              | no             |
| LISTENING      | (none yet)    | **stop**            | no              | no             |
| TRANSCRIBING   | **cancel**    | (already stopped)   | no              | no             |
| THINKING       | **cancel**    | no                  | no              | no             |
| SPEAKING       | **cancel**    | no                  | **cancel**      | no             |
| ERROR          | (none)        | no                  | no              | **cancel**     |

## Memory integration

Inside the THINKING stage, two memory-touching helpers run.

### `_build_context(user_text) -> str`

Returns a string assembled from two sources, deduplicated, capped at
`settings.context_char_cap`:

- Last N messages from the active project (`N = settings.recent_messages_limit`,
  default 4). Sourced from `sqlite_store.get_recent_project_messages()` — added in
  Day 11 specifically for this.
- Top-K semantically relevant memories (`K = settings.semantic_k`, default 3) from
  ChromaDB, scoped to the active project (cross-project isolation, see Day 6).

The two are merged into a single `[Relevant context from past conversations]` block
appended to the user's message before the LLM call — same shape Day 6 established.

### `_persist_turn(user_text, assistant_text)`

Runs *before* `tts.speak()` for a deliberate reason: if TTS fails, the user never
hears the response, but the exchange is already in memory. The LLM has done valid
work; the failure was in audio delivery. Saving before TTS also means
mute-during-speech never loses a turn.

What it does:

1. `sqlite_store.save_message(role="user", content=user_text, project_id=...)`.
2. `sqlite_store.save_message(role="assistant", content=assistant_text, ...)`.
3. `score = await importance.score(f"User: ...\nAssistant: ...")` — a second LLM
   call (the Day 6 scorer).
4. If `score >= settings.importance_threshold`:
   `await vector_store.add(text, project_id, metadata)` and update
   `memory.chroma_id`.

### Why importance scoring is wrapped in try/except

The scorer is a non-fatal LLM call. If Groq is rate-limited or Gemini is down, the
scorer raises, the helper catches it, logs a warning, and returns. SQLite rows are
already saved (queryable via SQL); only the ChromaDB entry is skipped (not
semantically searchable). Acceptable degradation for a personal daily-driver.

```python
try:
    score = await importance.score(...)
except Exception as e:
    logger.warning(f"importance scoring failed: {e}")
    return  # SQLite already saved; just skip Chroma
```

## Service contracts (don't break these)

Each service has an established contract that callers depend on. Changes here ripple
into the orchestrator, smoke tests, and (Day 12) audio robustness work.

### `AudioRecorder` (`backend/voice/audio.py`)

- `start_recording()` — sync; opens the `sounddevice.InputStream`. Must run via
  `run_in_executor`.
- `stop_recording() -> bytes` — sync; closes the stream, joins the buffer, returns
  WAV bytes (stdlib `wave`, 16kHz mono int16). **Returns `b""` if not recording or
  empty buffer.** Callers must handle the empty case explicitly (see G3 in Day 11).
- Lives on `app.state.audio_recorder`. Single instance per process. Reconstructed by
  `POST /audio/device`.
- Internal `threading.Lock` protects the buffer and stream from cross-thread access
  (the sounddevice callback runs on PortAudio's private thread).
- **30-second max-duration is a silent loss today** — the callback sets the recording
  flag to false but the WAV is not saved on the auto-stop path. Deferred to Day 12.

### `STTService` (`backend/voice/stt.py`)

- `transcribe(audio_path: Path) -> TranscriptionResult` — async; takes a **Path,
  not bytes**. Opens the file itself for streaming to Groq's API.
- This is why `_save_recording` lives in the orchestrator: the WAV must be written
  to disk before STT can read it.
- Persistent `AsyncGroq` client owned by the service. `close()` releases the HTTP
  connection pool. Construct once in lifespan, never per-call.
- Raises `STTError` for every failure mode (auth, network, timeout, empty audio).
  Callers catch only `STTError`; never see raw `GroqError` or `httpx.TimeoutException`.

### `TTSService` (`backend/voice/tts.py`)

- `speak(text: str) -> SynthesisResult` — async; runs Piper as a subprocess, plays
  through sounddevice via `run_in_executor`. Awaits playback completion.
- `cancel_playback()` — async; calls `sd.stop()` inside an executor. Added in Day 11
  for mute-during-speaking.
- `close()` — release any persistent state on shutdown.
- Raises `TTSError` for every failure mode. Same UI-safe pattern as `STTError`.
- Synth sample rate is hardcoded at `tts_sample_rate = 22050` in settings. **A voice
  swap to a 16kHz model will play at chipmunk pitch** with no error — read the
  `.onnx.json` sidecar before deploying a different voice (Day 17 polish).

### LLM router (`backend/llm/router.py`)

- `generate(prompt, model=None) -> LLMResponse` — already existed since Day 4.
- The orchestrator goes through the router, never calls `GeminiProvider` or
  `GroqLLMProvider` directly.

## Synthetic `request_id` for non-HTTP work

The Day 3 request-id middleware sets a ContextVar for HTTP requests. Voice paths
have no HTTP request, so they bind their own `request_id` via `logger.bind(...)` —
see `project-architecture/SKILL.md`. The conventions established in Week 2:

| Context                       | request_id source                                |
|-------------------------------|--------------------------------------------------|
| `_save_recording`             | `Path.stem` of the WAV (ISO8601 timestamp)       |
| STT call (Day 9 dispatcher)   | Same WAV-stem id, propagated                     |
| TTS standalone (`POST /speak`)| `tts-{N}ch` where N is the text length           |
| TTS in chat fire-and-forget   | `tts-{N}ch`                                      |
| Conversation pipeline         | Should bind a single per-turn id (open item)     |

The per-turn id binding for `_process_turn` is the cleanest improvement: a single
id covering save → STT → LLM → persist → TTS makes the whole turn one searchable
unit in `data/logs/jarvis.log`. Worth adding when next touching the orchestrator.

## Adding new functionality

### Adding a new state

1. Extend `VoiceState` enum in `backend/models/voice.py`.
2. Decide what triggers entry and exit. Add the transition rules to the table above.
3. Add the new state to `_transition`'s allowed-transitions check.
4. Wherever the pipeline re-acquires the lock and re-checks MUTED, decide whether
   the new state also needs to be checked. Most new states will not be absorbing
   like MUTED, so typically no extra re-checks are needed.
5. Update the diagram in this skill file.
6. If the state has side effects (timer, background task), follow the
   `self._inflight = asyncio.create_task(...)` pattern with a strong reference.

### Adding a new trigger that should behave like PTT (Day 27 wake word)

Wake word activation should look exactly like `on_ptt_start` from the orchestrator's
point of view: IDLE → LISTENING, then auto-end on silence rather than on `ptt_end`.

The clean approach:

- `wake_word.py` background task detects activation, pushes a `wake_word_start` event
  into the dispatcher queue.
- `_handle_event_side_effects` in `main.py` routes `wake_word_start` to
  `orchestrator.on_ptt_start()` (same method — wake word and PTT are interchangeable
  triggers).
- A silence-detection helper (amplitude-threshold on the recorder's running buffer)
  emits a synthetic `ptt_end` after N seconds of silence.

This keeps the orchestrator unchanged. The state machine doesn't care which hotkey
or audio cue triggered the turn.

### Adding tool calls inside THINKING (Day 20)

Tool execution will turn the THINKING stage into a loop: LLM → tool call → tool
result → LLM → … (max 5 iterations per Day 20 plan). Critical rules:

- The loop runs entirely outside the lock — only state transitions touch the lock.
- **MUTED re-check between each tool call**, not just at the start of THINKING.
  A long-running tool (PDF summarisation, web search) is exactly the case where the
  user may hit mute. The re-check pattern is identical to the existing ones.
- Tool failures should raise an exception that the orchestrator catches and routes
  through `_handle_error` — same shape as STT/LLM/TTS failures.

## Gotchas

- **30s max-duration recording silently loses the buffer.** The callback sets
  `_recording = False` but can't call `stop_recording()` (lock recursion). On
  `ptt_end`, `is_recording` is already false; the idempotency guard returns `b""`
  and no WAV is saved. Day 12 fix is a synthetic `ptt_end` queue injection from
  the callback.
- **Piper `--output_raw` is underscore not hyphen.** The Piper CLI uses both styles
  for different flags; `--output_raw` is the raw-PCM-to-stdout flag for the Windows
  amd64 build used here. Verified against the installed binary in Day 10.
- **Piper sample rate is hardcoded at 22050.** Lives in `settings.tts_sample_rate`
  and matches `en_US-lessac-medium.onnx.json`. A different voice (e.g.
  `en_US-libritts-high` at 16kHz) will play at the wrong pitch with **no error
  signal** — chipmunk audio is the only symptom. Read the `.onnx.json` sidecar at
  TTS service init when voice swapping becomes a feature (Day 17).
- **`sd.play(arr, dtype=...)` raises** when `arr` is already a typed numpy array.
  Just pass `sd.play(arr, samplerate=...)` and let sounddevice infer dtype.
- **`STTService.transcribe()` takes `Path`, not bytes.** The Groq SDK streams from
  an open file handle. Passing bytes silently fails with an opaque error. This is
  why `_save_recording` exists and lives in the orchestrator.
- **`recording_saved` is queue-internal — NOT broadcast to the UI.** Day 9 excluded
  it from the outer broadcast loop after React 18 batching dropped error toasts
  back-to-back with `recording_saved`. The STT branch in the dispatcher consumes it
  entirely. If you re-add a UI-visible "saved" signal (Day 17 debug view), pick a
  new event name; don't re-broadcast `recording_saved`.
- **MUTED re-check on every lock re-acquire** is what makes mute feel instant. If
  you add a new pipeline stage and forget the re-check, the user will hit mute and
  hear the next stage play out anyway. The bug looks like "mute doesn't work" but
  the real cause is a missing 2-line guard.
- **`_handle_error` requires the lock to already be held.** Calling it from outside
  the lock won't deadlock (it doesn't re-acquire), but state will not be properly
  serialised with the surrounding broadcast. Until the `assert self._lock.locked()`
  open item is in, this is enforced by docstring only.
- **Importance scoring is a second LLM call per turn.** Every chat turn makes two
  LLM calls: the answer + the scorer. Heavy testing days can hit Groq's per-minute
  rate limit. The scorer's failure path is silent (logged warning, ChromaDB skipped,
  SQLite still saved) — the symptom is "memory works for some turns but not others".
  `SELECT COUNT(*) FROM memory ORDER BY id DESC LIMIT 20;` is the diagnostic.
- **`POST /audio/device` does not validate the new device opens cleanly.** The
  endpoint rebuilds the recorder with the chosen index; the first failure surfaces
  on the next `ptt_start` as a PortAudio error in the logs. Deferred to Day 17 UI
  polish — but until then, mistyped device indices cause confusing "nothing happens
  on PTT" symptoms.
- **`asyncio.create_task(self._process_turn(...))` must store the task.**
  `self._inflight = ...` keeps the reference alive AND makes the task cancellable
  from `on_mute_toggle`. A bare `asyncio.create_task(...)` will be GC'd and the
  pipeline will stop mid-turn with no error.

## When to update this file

Update `voice-pipeline/SKILL.md` when:

- A new state is added to the state machine (very rare; Day 20 and Day 27 expand
  the pipeline but should NOT add new states — they hook in at existing transitions).
- The Lock pattern rules change.
- A service contract changes (e.g. `STTService.transcribe()` accepts bytes; `TTSService`
  gains streaming output).
- A new service joins the pipeline (e.g. a future filter or noise-suppression step).
- A new gotcha is discovered and confirmed during voice-loop work.
- A new WebSocket event is added to the orchestrator's emit set.

Do NOT update for:

- Bug fixes that don't change the patterns (e.g. tightening a guard, adjusting a
  timeout).
- UI changes that consume existing events (`App.tsx` rework, blob wiring).
- Adding new tools — those go through the tool-calling skill (Day 20).
- Latency tuning that doesn't change the structure of the pipeline.
