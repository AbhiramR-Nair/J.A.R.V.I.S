# Day 11 Plan — Full Voice Loop + Mute

**Date:** Day 11 of 30
**Week:** 2 (Voice Pipeline, PTT-only)
**Status going in:** Day 10 complete — recorder, STT, TTS all individually working and individually wired into `app.state`. The pieces exist; today connects them.
**Time budget:** 5 hours (per `Day_by_Day_Plan_v2.md`)
**Companion docs:** `Version_1_plan.md`, `Day_by_Day_Plan_v2.md`, `.claude/skills/project-architecture/SKILL.md`, `PROJECT_STATUS_DAY_10_.md`

---

## 0. Agenda (one paragraph)

Today's deliverable is a single Python service — `backend/services/conversation.py` — that owns the full voice loop as a formal state machine: **idle → listening → transcribing → thinking → speaking → idle**, plus `muted` and `error` states. Existing primitives (`recorder`, `stt_service`, `llm_router`, `tts_service`, memory stores) are dependencies it composes; today does not touch their internals. The orchestrator subscribes to PTT and mute events from the existing WebSocket channel, drives the recorder/STT/LLM/TTS in order, persists user *and* assistant turns to project-scoped memory, and broadcasts state transitions back to the frontend so the UI text label updates live. The blob does not exist yet — that is Week 3. Today, the UI just shows a string label that mirrors `current_state`. Three carryovers from prior days are folded in because adding new events without them breaks Day 11 itself: the React `useVoiceEvents` `lastEvent` pattern is replaced with a reducer queue, `speaking_failed` is broadcast (mirror of `transcription_failed`), and concurrent triggers are gated by the `speaking` state.

---

## 1. Pre-flight checks (verify Day 10 state is intact before touching anything)

Run before opening any editor. If any of these fail, fix that first — Day 11 assumes a clean Day 10 baseline.

- [ ] `git status` is clean; on `main` branch
- [ ] `python -m backend.desktop` boots; log shows three init lines in order: recorder → stt → tts
- [ ] `python -m backend.tests.test_tts_smoke` plays both SHORT and LONG cleanly
- [ ] `curl -X POST http://localhost:8000/speak -H "Content-Type: application/json" -d "{\"text\":\"Day eleven warm-up.\"}"` returns 200 and audio plays
- [ ] `curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"message\": \"Hi\"}"` returns text immediately; audio plays ~1s later
- [ ] Vite dev server (`npm run dev` in `frontend/`) connects to backend WS
- [ ] Holding Alt+Space (Day 8) still writes a WAV to `data/recordings/`

**If any step fails:** stop, fix, commit the fix on a separate branch. Do not start Day 11 work on a broken baseline.

---

## 2. Architectural decisions to settle BEFORE writing code

Per `CLAUDE.md` rule #2 — discuss with Claude Code before implementation. Each decision below has 2-3 options with trade-offs. Pick one for each, write it into `docs/journal.md` as a one-liner, then code.

### 2.1 Orchestrator lifecycle and location

**Options:**
- (a) **Singleton on `app.state.conversation`**, constructed in `lifespan` after `tts_service`, shut down LIFO (conversation → tts → stt → recorder)
- (b) Per-request orchestrator created on each WS connection
- (c) Free functions in `services/conversation.py` operating on a module-level state dict

**Recommendation:** (a). Matches the Day 10 pattern exactly (`app.state.recorder`, `app.state.stt_service`, `app.state.tts_service`). Single-user, single-conversation-at-a-time means singleton state is correct. (b) and (c) add complexity for zero gain in this product.

### 2.2 State machine implementation style

**Options:**
- (a) **Explicit `VoiceState` enum + methods on the orchestrator class** (`on_ptt_start`, `on_ptt_end`, `on_mute_toggle`, `on_error`) — each method checks current state and transitions
- (b) Reducer-style: `(state, event) -> (new_state, side_effects)` pure function, side effects executed by a dispatcher
- (c) `transitions` library (third-party state machine)

**Recommendation:** (a). Most readable, no new dependency, every transition is grep-able. Hard rules: each transition method holds an `asyncio.Lock` so state mutation is serialised; invalid transitions log a warning and return early instead of raising (we never want a stray hotkey to crash the loop). (b) is elegant but adds a layer the project doesn't earn yet. (c) is overkill for ~6 states.

### 2.3 Mute semantics — block-and-drop or block-and-queue?

**Options:**
- (a) **Block-and-drop**: while muted, all PTT events are logged and ignored. After unmute, user must press again
- (b) Block-and-queue: PTT events while muted are buffered, processed on unmute
- (c) Hard-stop: muting mid-recording immediately cancels recording, discards buffer, returns to `muted`

**Recommendation:** **(a) for new events + (c) for in-flight states**. If muted while idle, ignore future PTT until unmuted. If muted while listening/transcribing/thinking/speaking, immediately cancel the in-flight work (stop recording, abort TTS playback if mid-stream), discard intermediate buffers, and transition to `muted`. Block-and-queue (b) creates surprise: pressing PTT while muted should not produce audio 10 seconds later when the user unmutes — that is the opposite of a mute button.

### 2.4 Assistant message persistence

**Options:**
- (a) Save assistant message in the same code path as user message, score importance with the same `importance.py` LLM call
- (b) **Save assistant message with importance inherited from the user message** that triggered it (paired turn scoring)
- (c) Skip importance scoring on assistant turns; save all of them at a fixed `importance=5`

**Recommendation:** (b). Conversational turns are paired — if the user asked "what was T315I's fold-shift?" and the assistant gave a specific number, both belong in memory together. Doubling the LLM scoring call (a) doubles cost and latency. Fixed-importance (c) bloats ChromaDB with throwaway turns. Save user and assistant message together as a single conversation turn; one importance score applies to both.

### 2.5 Multi-turn context — how does the assistant remember the previous turn?

**Options:**
- (a) Last N raw messages from SQLite (e.g. last 6 turns) prepended to the LLM prompt
- (b) Semantic search of the active project's memory (top 3 by relevance)
- (c) **Both: last 4 raw messages for short-term continuity + top 3 semantic for long-term recall**, deduplicated

**Recommendation:** (c). Pure recency (a) breaks on "what did we conclude about T315I yesterday?" Pure semantic (b) breaks on "what did I just say?" (the just-said message is the strongest semantic match, defeating the purpose). The hybrid is what most production assistants do. Cap total injected context at ~1500 tokens to leave room for the system prompt and tool schemas (Day 20).

### 2.6 LLM call: streaming or full-response?

**Options:**
- (a) **Full response, then TTS** — wait for Gemini to finish, then synthesise the whole reply
- (b) Streaming Gemini → chunked TTS at sentence boundaries → faster time-to-first-audio

**Recommendation:** (a) for Day 11. Streaming is a Week 3+ latency optimisation. Piper synthesis is ~1s for typical replies, Gemini Flash is ~1-1.5s for short answers — total speak-time onset is ~2.5s after STT, which meets the under-5s end-to-end target. Streaming adds ~150 lines of buffer management for ~1s of perceived latency win, not worth the complexity today.

---

## 3. Task breakdown

Ordering is deliberate: contract first (so the frontend can be wired in parallel), reducer fix next (it blocks the rest), orchestrator skeleton, then transitions one by one. Each task ends with a runnable check.

### 11.1 — Define the WebSocket event contract (30 min)

**What:** Codify what events flow over `/ws/voice` after Day 11. No code yet — just a written spec in `backend/models/voice.py` as `Literal` types and Pydantic models so both backend and frontend can lean on it.

**Events going forward (cumulative — existing events are unchanged):**

| Direction | Event name | Payload | Origin | Days |
|---|---|---|---|---|
| frontend → backend | `ptt_start` | `{}` | pynput | Day 7 |
| frontend → backend | `ptt_end` | `{}` | pynput | Day 7 |
| frontend → backend | `mute_toggle` | `{}` | pynput | Day 7 |
| backend → frontend | `transcription_complete` | `{text: str}` | STT done | Day 9 |
| backend → frontend | `transcription_failed` | `{reason: str}` | STT error | Day 9 |
| backend → frontend | **`state_changed`** | `{state: VoiceState, prev_state: VoiceState}` | every transition | **Day 11 — new** |
| backend → frontend | **`assistant_message`** | `{text: str, turn_id: str}` | LLM done | **Day 11 — new** |
| backend → frontend | **`speaking_started`** | `{turn_id: str}` | TTS playback begin | **Day 11 — new** |
| backend → frontend | **`speaking_ended`** | `{turn_id: str}` | TTS playback end | **Day 11 — new** |
| backend → frontend | **`speaking_failed`** | `{reason: str, turn_id: str}` | TTS error | **Day 11 — new** |

**Why `state_changed` plus the granular events:** `state_changed` is enough to drive the UI label. The granular events exist for the blob (Week 3, will animate on `speaking_started` → `speaking_ended`) and for debugging (easier to grep one event than reconstruct from state diffs).

**Check:** `backend/models/voice.py` compiles, `Literal["idle", "listening", "transcribing", "thinking", "speaking", "muted", "error"]` is exported.

### 11.2 — Frontend `useVoiceEvents` reducer refactor (Day 9 carryover — 45 min)

**Why this is Day 11, not Day 9 leftover:** Day 11 adds `state_changed` + `speaking_started` + `speaking_ended` + `speaking_failed` + `assistant_message` — five new event types. The current `lastEvent: VoiceEvent | null` pattern (Day 9) drops any event that arrives before React re-renders. Five new event types means five new collision opportunities per turn. This must be fixed *before* the orchestrator broadcasts events, or symptoms will be misattributed.

**What:**
- Replace `lastEvent` with `useReducer` queue: `events: VoiceEvent[]`
- Reducer actions: `event_received` (push), `event_consumed` (shift), `clear` (reset on disconnect)
- Components consume by reading `events[0]` and dispatching `event_consumed` in an effect
- Cap queue at 50; drop oldest if exceeded (logs warning)

**Check:** Open dev tools, send `state_changed` twice in <100ms via a temporary backend test endpoint, confirm both render.

### 11.3 — `ConversationOrchestrator` skeleton (45 min)

**What:** New file `backend/services/conversation.py`. No logic yet, just the shape.

```python
# Owns the voice loop state machine. Singleton on app.state.
# All public methods are async and acquire self._lock before mutating state.
# Side effects (start_recording, stt.transcribe, llm.generate, tts.speak)
# are dispatched as asyncio.Tasks tracked on self._inflight so mute-toggle
# can cancel them.

class VoiceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MUTED = "muted"
    ERROR = "error"

class ConversationOrchestrator:
    def __init__(
        self,
        recorder: AudioRecorder,
        stt: STTService,
        llm: LLMRouter,
        tts: TTSService,
        memory: SQLiteStore,
        vector: VectorStore,
        broadcast: Callable[[dict], Awaitable[None]],
        settings: Settings,
    ): ...

    async def on_ptt_start(self) -> None: ...
    async def on_ptt_end(self) -> None: ...
    async def on_mute_toggle(self) -> None: ...
    async def _transition(self, new_state: VoiceState) -> None: ...
    async def _handle_error(self, msg: str) -> None: ...
    async def close(self) -> None: ...   # cancel inflight, return to idle
```

**Wire into `lifespan`:** construct after `tts_service`, store on `app.state.conversation`, close before tts on shutdown (LIFO).

**Check:** Backend boots with four init lines now: recorder → stt → tts → conversation. Shutdown reverses cleanly.

### 11.4 — Route hotkey WS events into the orchestrator (30 min)

**What:** In `backend/api/voice.py` (the `/ws/voice` handler), the `ptt_start` / `ptt_end` / `mute_toggle` message types currently flow to whatever Day 7-9 wired them to. Today, route them through `app.state.conversation.on_ptt_start()` etc. Old direct calls to `recorder.start_recording()` etc. get removed from the WS handler — the orchestrator owns those now.

**Minimal diff principle:** do not refactor unrelated WS message handling. Touch only the three event branches.

**Check:** Hold Alt+Space, see `state_changed: idle → listening` arrive in WS dev tools. Release, see `listening → transcribing → thinking → speaking → idle`.

### 11.5 — Implement the happy path: PTT → STT → LLM → TTS (90 min)

**This is the meat of the day.** Implement each transition method. Suggested order:

**11.5.a — `idle → listening`** (on `ptt_start`)
- Guard: if not `IDLE`, log warning, return
- Acquire lock, call `recorder.start_recording()`, set state to `LISTENING`, broadcast
- Note: recorder.start_recording is sync-fast — do not run in executor; only `stop_recording` returns bytes

**11.5.b — `listening → transcribing → thinking`** (on `ptt_end`)
- Guard: if not `LISTENING`, log warning, return
- Stop recording, get `audio_bytes`
- Transition to `TRANSCRIBING`, broadcast
- Spawn an asyncio.Task for the rest (don't block ptt_end caller):
  - Call `stt.transcribe(audio_bytes)` → text
  - On STTError → broadcast `transcription_failed`, transition to `ERROR`, schedule 3s auto-recovery to IDLE
  - On success → broadcast `transcription_complete`, transition to `THINKING`
  - Pull conversation context (see 11.7)
  - Call `llm.generate(prompt=user_text, context=...)` → assistant_text
  - On LLM error → broadcast error event, ERROR state
  - Persist user + assistant turn to SQLite and (after importance score ≥ threshold) ChromaDB — see 11.7
  - Broadcast `assistant_message`
  - Transition to `SPEAKING`, call `tts.speak(assistant_text)`, broadcast `speaking_started`
  - On TTSError → broadcast `speaking_failed`, ERROR state, 3s auto-recover
  - On success → broadcast `speaking_ended`, transition to `IDLE`

**11.5.c — Track the in-flight task** so mute can cancel:
```python
self._inflight = asyncio.create_task(self._process_turn(audio_bytes))
self._inflight.add_done_callback(self._on_turn_complete)
```

**Critical:** the lock should NOT be held during the long-running stt/llm/tts calls. Acquire it only for state mutation; release for I/O. Pattern:
```python
async with self._lock:
    if self._state != VoiceState.LISTENING:
        return
    audio = await self._recorder.stop_recording()
    await self._transition(VoiceState.TRANSCRIBING)
# lock released — now do the slow stuff
text = await self._stt.transcribe(audio)
async with self._lock:
    if self._state == VoiceState.MUTED:
        return  # mute toggled mid-transcribe; drop the result
    await self._transition(VoiceState.THINKING)
# ...
```

**Check:** Hold Alt+Space, say "what's the capital of France?", release. Within ~5s, hear "Paris". UI label cycles: idle → listening → transcribing → thinking → speaking → idle.

### 11.6 — Mute toggle (45 min)

**Behavior (from decision 2.3):**

- `IDLE → MUTED`: simple state change, broadcast
- `LISTENING → MUTED`: `recorder.stop_recording()`, discard bytes, cancel any pending task, broadcast
- `TRANSCRIBING / THINKING → MUTED`: cancel `self._inflight` task (CancelledError handler in `_process_turn` swallows it), broadcast
- `SPEAKING → MUTED`: stop TTS playback mid-stream (call `sd.stop()` via executor; needs adding a `tts.cancel_playback()` method — small addition to `TTSService`), broadcast
- `MUTED → IDLE`: simple state change on second hotkey press

**Re-entrancy:** if mute hotkey fires while a transition is mid-flight, the lock serialises it. The cancelled task's cleanup runs after the mute transition completes.

**Visual:** UI label says "Muted" — Day 11 has no blob yet, just text.

**Check:** Start PTT, release, wait until "thinking" appears, hit Ctrl+Alt+J. State goes to MUTED. No audio plays. Hit Ctrl+Alt+J again — back to IDLE. Try PTT — works again.

### 11.7 — Memory: assistant turn save + multi-turn context (60 min)

**Save (decision 2.4):**

- Add `SQLiteStore.save_turn(project_id, user_text, assistant_text, request_id) -> turn_id`
- After LLM returns, call `save_turn(...)` — single transaction, two `messages` rows linked by `turn_id`
- Score importance ONCE on the combined turn: `importance.score(f"User: {u}\nAssistant: {a}")` → if ≥ 4, write *both* messages to ChromaDB as a single document (or two with shared metadata — pick whichever ChromaDB schema is already using)

**Retrieve (decision 2.5 — hybrid recency + semantic):**

- New method `_build_context(project_id, user_query) -> str`:
  - `recent = sqlite.get_recent_messages(project_id, limit=4)` (last 2 turns)
  - `relevant = vector.search(user_query, project_id, k=3)`
  - Deduplicate (a recent message may also be a top semantic hit — drop the duplicate from `relevant`)
  - Format as `"[recent]\n- ...\n[relevant memory]\n- ..."`
  - Token-cap at ~1500 (use a simple `len(text) // 4` heuristic for now; real tokenizer is Day 20+)

**System prompt update:** add a line: "You have access to recent conversation and relevant past notes from the user's active project. Use them when they help; ignore them when they don't."

**Check:**
1. PTT: "I'm working on the ABL1 kinase project." → response acknowledges
2. PTT: "What did I just say I was working on?" → response says ABL1 / kinase (recency works)
3. (Optional, requires Day 5/6 already populated) PTT: "What was the T315I fold-shift?" → if previously stored, recall works (semantic works)

### 11.8 — Race-condition guards & invariants (30 min)

**Guards to write explicitly, with a comment block above each per `CLAUDE.md` rule #1:**

- **G1**: every public method checks `self._state` against the allowed set after acquiring the lock; logs warning + returns on mismatch
- **G2**: `_process_turn` wraps its body in `try/except asyncio.CancelledError` — on cancel, do not transition (mute already did), do not broadcast
- **G3**: `_transition(new_state)` is the *only* place that mutates `self._state` and broadcasts; no other method writes to it
- **G4**: `ERROR` state auto-recovers to `IDLE` after 3s via `asyncio.create_task(self._auto_recover())` scheduled inside `_handle_error`
- **G5**: `close()` (called from lifespan shutdown) cancels `self._inflight` if any and awaits it with `return_exceptions=True`

**Invariants asserted in tests (informal — no pytest suite, just smoke):**

- At most one `_inflight` task at any time
- `state_changed` events arrive in causally-ordered pairs (every `listening` is followed by either `transcribing` or `idle` — never another `listening`)
- After any mute, the next non-mute event is from IDLE or MUTED, never mid-flight

### 11.9 — Frontend state label (15 min)

**Minimal:** add a single `<div>` to whatever the main App.tsx renders today that reads `voiceState` from the reducer and displays it as a string. Not a component, not styled — just `<div className="text-xs opacity-60">{voiceState}</div>` somewhere visible. The blob is Week 3; today is functional verification only.

**One niceness:** show `Muted` (capitalised) rather than the enum string for the muted state, since that one will be user-facing if anything goes wrong.

### 11.10 — Manual verification + journal + commit (30 min)

Run the full verification script (section 10). Fix anything that fails. One-line entry in `docs/journal.md`. Commit message: `feat: full ptt voice loop with state machine and mute`.

Tag this commit `v0.2.0-voice-loop` if all 10 verification cases pass — this hits the Week 2 milestone.

---

## 4. State machine — formal specification

```
States: { IDLE, LISTENING, TRANSCRIBING, THINKING, SPEAKING, MUTED, ERROR }

Events from frontend:  ptt_start, ptt_end, mute_toggle
Events from internals: stt_done, stt_failed, llm_done, llm_failed,
                       tts_done, tts_failed, mute_cancelled, auto_recover

Transitions (event → allowed in states → new state):
  ptt_start     in {IDLE}                                   → LISTENING        [side: recorder.start]
  ptt_end       in {LISTENING}                              → TRANSCRIBING     [side: recorder.stop, spawn _process_turn]
  stt_done      in {TRANSCRIBING}                           → THINKING         [side: broadcast transcription_complete, llm.generate]
  stt_failed    in {TRANSCRIBING}                           → ERROR            [side: broadcast transcription_failed]
  llm_done      in {THINKING}                               → SPEAKING         [side: broadcast assistant_message, tts.speak]
  llm_failed    in {THINKING}                               → ERROR            [side: broadcast error]
  tts_done      in {SPEAKING}                               → IDLE             [side: broadcast speaking_ended]
  tts_failed    in {SPEAKING}                               → ERROR            [side: broadcast speaking_failed]
  mute_toggle   in {IDLE}                                   → MUTED            [side: -]
  mute_toggle   in {LISTENING}                              → MUTED            [side: recorder.stop, discard]
  mute_toggle   in {TRANSCRIBING, THINKING}                 → MUTED            [side: cancel _inflight]
  mute_toggle   in {SPEAKING}                               → MUTED            [side: tts.cancel_playback]
  mute_toggle   in {MUTED}                                  → IDLE             [side: -]
  mute_toggle   in {ERROR}                                  → MUTED            [side: cancel auto_recover]
  auto_recover  in {ERROR}                                  → IDLE             [side: -]

Disallowed transitions log a warning and return without state change.
The transition function holds self._lock; side effects run after lock release where blocking.
```

**A picture (ASCII for the journal):**

```
                    ptt_start            ptt_end
       ┌──────────────────────► LISTENING ─────────► TRANSCRIBING
       │                              │                    │
       │  mute_toggle                 │ mute               │ stt_done
       │  (when IDLE)                 ▼                    ▼
     IDLE ◄────────────────────── MUTED ◄───── THINKING
       ▲                              ▲           │
       │  tts_done                    │ mute      │ llm_done
       │                              │           ▼
   SPEAKING ◄──────────────── (any state) ─── (mute) ──── SPEAKING
       │                                                  │ tts_failed
       │ tts_failed / llm_failed / stt_failed             ▼
       └─────────────────────────► ERROR ──── 3s ────► IDLE
                                    ▲
                                    └── mute_toggle ──► MUTED
```

---

## 5. Mute behavior — full specification

| Currently in | Hotkey pressed | What happens | What user perceives |
|---|---|---|---|
| IDLE | Ctrl+Alt+J | state → MUTED; broadcast | label says "Muted" |
| LISTENING | Ctrl+Alt+J | recorder.stop_recording() called, bytes discarded; state → MUTED | recording aborts silently |
| TRANSCRIBING | Ctrl+Alt+J | `_inflight.cancel()`; STT call cancelled (httpx supports it); state → MUTED | label flips from "transcribing" to "Muted" |
| THINKING | Ctrl+Alt+J | `_inflight.cancel()`; LLM call cancelled; state → MUTED | label flips from "thinking" to "Muted" |
| SPEAKING | Ctrl+Alt+J | `tts.cancel_playback()` (new — calls `sd.stop()` in executor); state → MUTED | audio cuts off mid-word |
| MUTED | Ctrl+Alt+J | state → IDLE | back to normal |
| ERROR | Ctrl+Alt+J | auto-recover task cancelled; state → MUTED | stays muted instead of recovering to idle |

**Edge:** if Ctrl+Alt+J is pressed twice within ~100ms (debouncing isn't pynput's job), the lock serialises — first press goes MUTED, second goes back to IDLE, net result is one full mute-unmute cycle that the user perceives as nothing. Acceptable.

**TTS cancel implementation:** add a single method to `TTSService`:
```python
# Stops any in-flight playback. Safe to call even when nothing is playing.
# Runs in executor because sd.stop() is a PortAudio blocking call.
async def cancel_playback(self) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, sd.stop)
```
No state on the TTS service — `sd.stop()` is global to the sounddevice module. This is a minimal addition; the existing `_play_sync` will return from `sd.wait()` when stop fires.

---

## 6. Memory integration design

### Schema (no changes to Day 5 schema needed)

Existing `messages` table from Day 5 already has `project_id`, `role`, `content`, `created_at`, `conversation_id` (or equivalent). Day 11 adds a `turn_id` column if not already present — used to pair user/assistant rows from the same exchange.

If the column doesn't exist yet:
```sql
ALTER TABLE messages ADD COLUMN turn_id TEXT;
CREATE INDEX IF NOT EXISTS idx_messages_turn_id ON messages(turn_id);
```

### Save path

```python
# Called from _process_turn after LLM returns, before TTS starts.
# One DB transaction, two rows, shared turn_id (uuid4 hex prefix).
async def _persist_turn(
    self,
    project_id: str,
    user_text: str,
    assistant_text: str,
    request_id: str,
) -> str:
    turn_id = uuid.uuid4().hex[:12]
    await self._memory.save_turn(
        project_id=project_id,
        turn_id=turn_id,
        user_text=user_text,
        assistant_text=assistant_text,
    )
    importance = await self._importance.score(
        f"User: {user_text}\nAssistant: {assistant_text}"
    )
    if importance >= self._settings.importance_threshold:
        await self._vector.add(
            text=f"User: {user_text}\nAssistant: {assistant_text}",
            project_id=project_id,
            metadata={"turn_id": turn_id, "importance": importance},
        )
    return turn_id
```

### Retrieve path

```python
# Called from _process_turn after STT done, before LLM call.
async def _build_context(self, project_id: str, user_query: str) -> str:
    recent = await self._memory.get_recent_messages(project_id, limit=4)
    relevant = await self._vector.search(user_query, project_id, k=3)
    # Dedup: a recent message that's also a top semantic hit appears twice
    recent_ids = {m.id for m in recent}
    relevant = [r for r in relevant if r.id not in recent_ids]
    # Format with simple section headers; LLM understands the structure
    blocks = []
    if recent:
        blocks.append("Recent conversation:\n" + "\n".join(
            f"  {m.role}: {m.content}" for m in recent
        ))
    if relevant:
        blocks.append("Relevant past notes:\n" + "\n".join(
            f"  - {r.text}" for r in relevant
        ))
    context = "\n\n".join(blocks)
    # Crude token cap; replace with tiktoken later
    if len(context) > 6000:
        context = context[:6000] + "\n...[truncated]"
    return context
```

---

## 7. Race conditions and safety invariants

Listed explicitly per `Day_by_Day_Plan_v2.md`'s explicit warning: "Race conditions — make sure TTS finishes before going to idle, and that muting mid-recording cleans up properly. Ask Claude to add explicit state guards."

| Race | Symptom if unhandled | Guard |
|---|---|---|
| Double `ptt_start` (key auto-repeat on Windows) | Two recordings start, second overwrites first | G1: only allowed in IDLE; second is logged + dropped |
| `ptt_end` arrives without preceding `ptt_start` | `recorder.stop_recording()` called on idle recorder | G1: only allowed in LISTENING |
| Mute while transcribing, then result arrives | THINKING state entered post-mute | Lock re-check on re-acquire (the `if state == MUTED: return` after I/O) |
| TTS finishes after mute toggled | SPEAKING → IDLE transition fires from MUTED | Same lock re-check pattern |
| Two `_process_turn` tasks running (impossible if lock works, but defense-in-depth) | Overlapping STT/LLM/TTS calls | `_inflight` is a single attribute; `_process_turn` checks `self._inflight is None or self._inflight.done()` before spawning |
| Backend shutdown while SPEAKING | Executor thread killed mid-write to PortAudio, possible segfault | `close()` cancels `_inflight` and awaits with `return_exceptions=True`; tts cleanup happens after |
| pynput thread emits event during lifespan startup (race on `app.state.conversation` existing) | AttributeError | Wrap WS routing in `if hasattr(app.state, "conversation"):`; pre-startup events are dropped |

---

## 8. Files to create or modify

```
NEW:
  backend/services/conversation.py    — ConversationOrchestrator + VoiceState enum
  docs/plans/day_11_plan.md           — this file

EDIT (minimal diffs):
  backend/models/voice.py             — add StateChangedEvent, AssistantMessageEvent,
                                        SpeakingStarted/Ended/Failed event models;
                                        export VoiceState Literal
  backend/voice/tts.py                — add async cancel_playback() method (~6 lines)
  backend/main.py                     — lifespan: construct conversation after tts,
                                        store on app.state.conversation,
                                        close before tts on shutdown (LIFO)
  backend/api/voice.py                — route ptt_start / ptt_end / mute_toggle
                                        WS messages to app.state.conversation
  backend/memory/sqlite_store.py      — add save_turn(), get_recent_messages();
                                        add turn_id column via migration
                                        (or use existing if already there — check)
  backend/database/schema.sql         — add turn_id column + index if not present
  backend/config/settings.py          — add importance_threshold (if not present),
                                        recent_messages_limit=4, semantic_k=3,
                                        context_char_cap=6000, error_recovery_seconds=3
  frontend/src/hooks/useVoiceEvents.ts — reducer queue refactor (Day 9 carryover)
  frontend/src/App.tsx                — add <div>{voiceState}</div> somewhere visible
  docs/journal.md                     — one line for Day 11
```

**Files explicitly NOT touched today:**
- `backend/llm/*` — orchestrator calls the existing router, no changes
- `backend/voice/audio.py`, `backend/voice/stt.py` — used as-is, no changes
- `frontend/src/blob/*` — does not exist yet; Week 3
- Anything in `backend/tools/` — Week 4

---

## 9. Manual verification script

Run all of these end-to-end before committing. Roll forward as `docs/demo_script.md` candidates for Day 28.

```powershell
# 1. Clean start
netstat -ano | findstr :8000
# Stop-Process -Id <PID> if anything shows
cd frontend; npm run dev
# new terminal:
python -m backend.desktop

# 2. Confirm FOUR init lines in this exact order:
#       audio recorder initialized
#       stt service initialized
#       tts service initialised
#       conversation orchestrator initialized — state=idle

# 3. Happy path: simple question
#    Hold Alt+Space, say "what's the capital of France?", release.
#    Expected:
#      - UI label cycles: idle → listening → transcribing → thinking → speaking → idle
#      - Hear "Paris" within 5 seconds of release
#      - assistant_message event visible in WS dev tools

# 4. Multi-turn context (recency)
#    PTT: "I'm working on the ABL1 kinase project."
#    Wait for spoken response.
#    PTT: "What did I just say I was working on?"
#    Expected: response mentions ABL1 / kinase

# 5. Mute from IDLE
#    Ctrl+Alt+J → label says "Muted"
#    Try PTT → label stays Muted, no recording starts
#    Ctrl+Alt+J → label back to "idle"
#    PTT works again

# 6. Mute mid-recording (LISTENING → MUTED)
#    Hold Alt+Space
#    While holding, hit Ctrl+Alt+J
#    Release Alt+Space
#    Expected: state went LISTENING → MUTED; no transcription occurred;
#              releasing PTT did nothing (already muted)

# 7. Mute mid-speaking (SPEAKING → MUTED)
#    PTT: "tell me a long fact about kinases"
#    Wait for audio to start
#    Mid-sentence, hit Ctrl+Alt+J
#    Expected: audio cuts off; label → Muted

# 8. STT failure handling
#    Edit .env, temporarily break GROQ_API_KEY (e.g. add 'x' at start)
#    Restart backend
#    PTT a sentence
#    Expected: transcription_failed event; ERROR state; label shows error msg;
#              3s later auto-recovers to IDLE
#    Fix .env, restart

# 9. TTS failure handling
#    Edit settings to point piper_binary_path at a nonexistent path
#    Restart backend
#    PTT: "hello"
#    Expected: STT works, LLM works, then speaking_failed event;
#              ERROR state; auto-recover to IDLE
#    Restore settings

# 10. Concurrent press defense
#    PTT a question; before audio response finishes, PTT again
#    Expected: second PTT is dropped (logged warning "ptt_start in SPEAKING")
#              first response completes normally

# 11. Memory persistence across restart
#    PTT: "Log that T315I shows forty-fold resistance."
#    Wait for response
#    Close backend, restart
#    PTT: "What did I tell you about T315I?"
#    Expected: response mentions forty-fold / resistance (semantic recall)

# 12. Clean shutdown
#    Close PyWebView window
#    Confirm: no orphan python.exe; logs show LIFO shutdown
#             conversation → tts → stt → recorder
```

---

## 10. Completion criteria (from `Day_by_Day_Plan_v2.md`)

| Criterion | Verification step | Pass? |
|---|---|---|
| Hold Alt+Space, ask "capital of France?", release → hear "Paris" within 5 seconds | Test 3 above | [ ] |
| State label in UI changes through listening → transcribing → thinking → speaking → idle | Test 3 above, watch UI | [ ] |
| Mute hotkey works; conversation blocked while muted | Tests 5, 6, 7 above | [ ] |
| Multi-turn works: follow-up question, context remembered | Test 4 above | [ ] |
| TTS finishes before going to idle (no premature state change) | Test 3; SPEAKING never skipped | [ ] |
| Muting mid-recording cleans up properly | Test 6 above | [ ] |
| Explicit state guards for invalid transitions (warnings logged, no crash) | Test 10 above | [ ] |
| Backend boots with four init lines in order, clean LIFO shutdown | Test 12 above | [ ] |
| Assistant messages saved to memory same as user messages | Test 11 above; check `data/jarvis.db` | [ ] |

**Beyond plan minimum — extra checks that should pass:**

| Extra criterion | Verification | Pass? |
|---|---|---|
| `speaking_failed` event broadcasts on TTS failure (mirror of Day 9 `transcription_failed`) | Test 9 above | [ ] |
| ERROR auto-recovers to IDLE after 3s | Tests 8, 9 above; observe state transition | [ ] |
| Concurrent PTT during speaking is dropped, not queued | Test 10 above | [ ] |
| Frontend `useVoiceEvents` no longer drops back-to-back events | Send `transcription_complete` and `assistant_message` 50ms apart, both render | [ ] |

---

## 11. Gotchas — things to watch for

Carried forward from `PROJECT_STATUS_DAY_10_.md` section 4 and earlier days' SKILL files. Re-read before each problem the day surfaces.

- **Pydantic v2 `model_config` is model-wide**, not per-field. Use `Field(...)` for per-field constraints. (Day 10 Problem 4.)
- **FastAPI trailing-slash 307** silently empties curl responses. Use `-L` or drop the slash. (Day 10 Problem 3.)
- **`sd.play(array, dtype="int16")` errors** when array is already int16. Don't pass dtype when the numpy array's dtype already encodes it. (Day 10 Problem 1.)
- **`sd.stop()` is global** to the sounddevice module — calling it from `tts.cancel_playback()` will stop any sound, not just TTS. Today this is fine (only TTS uses sd.play); flag if mic monitoring ever uses it concurrently.
- **`asyncio.create_task` references must be retained** or the task can be garbage-collected mid-run. Always assign to `self._inflight` or similar; never `asyncio.create_task(...)` as a bare expression.
- **CancelledError must propagate** out of `_process_turn` so that awaiters see the cancel — wrap cleanup in `try/finally`, not `try/except`.
- **The lock should NOT be held across `await stt.transcribe(...)` etc.** — see 11.5.c. Acquire for state mutation only; release for I/O. Re-acquire to mutate again, re-check state.
- **Gemini context window** is huge (1M tokens) but quality degrades past ~200k. Day 11's context cap of ~1500 chars is fine; don't blow this up later.
- **ChromaDB project isolation** is enforced by the `where={"project_id": ...}` filter on every query — `vector.search(...)` must always pass it. If `_build_context` ever forgets to pass `project_id`, semantic recall will leak across projects silently. Add an assertion.
- **`tts_sample_rate` is hardcoded** (Day 10 carryover). Out of scope today; only matters if the voice file changes.
- **Mute via `sd.stop()`** is instant in terms of audio but the executor thread that was waiting on `sd.wait()` returns immediately — make sure `_play_sync` returns cleanly without raising when stop fires mid-play.

---

## 12. Out of scope (explicit non-goals — do not let scope creep in)

These are tempting on Day 11 but belong to later days. If something here starts feeling necessary, stop and ask whether it really is or whether it's avoidance of the harder thing.

- **Blob animations** — Week 3. Today the UI is a string label.
- **Audio reactivity / amplitude broadcast** — Day 16.
- **Wake word** — Day 27 (optional). Today is PTT-only.
- **Streaming LLM → chunked TTS** — Day 12+ optimisation if time.
- **Window snap-to-corner, settings panel** — Day 17.
- **Tool calling, function calling** — Day 20. The LLM call today is plain text generation only.
- **Persistent conversation_id across restarts** — the current `messages` schema already supports it via Day 5's tables; do not redesign today.
- **Custom system prompt UI / per-project prompts** — Month 2.
- **Real tokenizer for context cap** — Day 20 when tool schemas need accurate counting.
- **PDF drop handling** — Days 22-24.
- **Web search tool** — Day 25.
- **`PyWebView`'s file-drop event** — Day 22 setup.

---

## 13. Time budget breakdown

| Phase | Estimate |
|---|---|
| 0 — Pre-flight checks | 10 min |
| 1 — Architectural decisions (with Claude Code discussion) | 30 min |
| 11.1 — WS event contract | 30 min |
| 11.2 — useVoiceEvents reducer refactor | 45 min |
| 11.3 — Orchestrator skeleton + lifespan wiring | 45 min |
| 11.4 — Route WS events to orchestrator | 30 min |
| 11.5 — Happy path implementation | 90 min |
| 11.6 — Mute toggle (incl. tts.cancel_playback) | 45 min |
| 11.7 — Memory: save_turn + _build_context | 60 min |
| 11.8 — Race-condition guards | 30 min |
| 11.9 — Frontend state label | 15 min |
| 11.10 — Verification + journal + commit | 30 min |
| **Total** | **~7.5 hours** |

This exceeds the plan's 5-hour budget by ~50%. Two options:

- **(a) Accept the overrun.** Day 11 is the most architecturally important day of Week 2 — it's the day the pieces stop being demos and become a product. Spending an extra 2-3 hours here pays for itself across the rest of Week 2 and all of Weeks 3-4. Day 13 and 14 are buffer days for exactly this.
- **(b) Defer 11.7 (memory integration) to Day 13/14.** The voice loop runs without it — the assistant just won't remember context. Completion criterion "multi-turn works" would fail, but the *loop* would work, which is the more critical of the two.

**Recommendation:** (a). Eat into Day 13 buffer rather than ship an incomplete Day 11. The multi-turn requirement is the difference between a voice demo and a voice assistant.

---

## 14. Drop-cut order (if behind by hour 6)

In priority order, drop from bottom up:

1. Happy path (PTT → STT → LLM → TTS) ← **protect at all costs**
2. State machine with guards
3. WS event broadcast (`state_changed` minimum)
4. Mute basic (IDLE ↔ MUTED only)
5. Assistant message save to SQLite
6. Mute mid-state cancellation (LISTENING/TRANSCRIBING/THINKING/SPEAKING → MUTED)
7. Multi-turn context (`_build_context`)
8. ERROR auto-recovery
9. ChromaDB semantic save (importance scored)
10. Frontend reducer refactor — *if dropped, document as Day 11 carryover; will hurt by Week 3*
11. Frontend state label — *can be inspected in WS dev tools instead*

Items 1-4 are the genuine Day 11 deliverable. Items 5-9 are completion criteria but degrade gracefully if partial. Items 10-11 are polish.

If still behind at hour 8: stop, commit what works behind a feature flag (`settings.use_orchestrator=False` falls back to direct WS handling), tag `v0.2.0-voice-loop-partial`, finish in Day 13. **Do not** push broken code on Sunday night to feel done.

---

## 15. Definition-of-done checklist

Day 11 is done when ALL of the following are true:

- [ ] `git log --oneline -1` shows `feat: full ptt voice loop with state machine and mute`
- [ ] All 9 plan-defined completion criteria (section 10) marked pass
- [ ] At least 8 of 12 manual verification scripts (section 9) pass
- [ ] `docs/journal.md` has a Day 11 line
- [ ] No `print()` statements remain in committed code (only `logger.*`)
- [ ] No commented-out code blocks in `services/conversation.py`
- [ ] You can sketch the state machine on paper without looking at the code
- [ ] You can explain why the lock is released across I/O calls
- [ ] You can explain why `useVoiceEvents` needed the reducer change

Last criterion is non-negotiable per `CLAUDE.md`: never accept code you can't explain.

---

**End of Day 11 plan.** Read once before starting. Re-read section 2 (decisions) and section 4 (state machine) before opening Claude Code. Re-read section 11 (gotchas) whenever something breaks.
