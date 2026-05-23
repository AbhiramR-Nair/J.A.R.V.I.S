# Day 9 Plan — Speech-to-Text via Groq Whisper

**Period:** Day 9
**Predecessor:** Day 8 (PTT audio capture, dispatcher pattern, `recording_saved` event)
**Successor:** Day 10 (Piper TTS) — Day 11 then ties STT + LLM + TTS into the full voice loop
**Time budget:** 4 hours
**Branch / commit target:** `feat: groq whisper stt integration`

---

## 1. Day-9 Goal (one sentence)

When the user releases Alt+Space, the WAV that Day 8 just saved should be transcribed by
Groq Whisper-large-v3 within ~1.5 s and the transcript should appear in the React chat
panel — with a clean, human-readable error path if Groq is unreachable.

This is the **first cloud-call** in the voice loop. Everything Day 10–11 builds on assumes
this works cleanly, so prefer **boring and observable** over **clever**.

---

## 2. What Day 9 inherits from Day 8 (read this before touching code)

Day 8 left the dispatcher in a deliberately Day-9-friendly shape. Before writing anything,
re-read these two functions in `backend/main.py`:

- `_dispatch_events` — single queue consumer
- `_handle_event_side_effects(event)` — branches on `event.type`

The hand-off note from Day 8 §4 says it explicitly:

> Day 9 will need to intercept `recording_saved` inside the dispatcher to trigger STT, not
> just let it flow through to the UI. The cleanest Day 9 pattern: add an
> `elif etype == "recording_saved"` branch in `_handle_event_side_effects` that kicks off
> the Groq Whisper call, then broadcasts a `transcription_complete` event.

That is exactly the integration shape Day 9 will implement.

**What also already exists and Day 9 should reuse — not reinvent:**

- `app.state` pattern for long-lived subsystems (Day 7–8)
- `app.state.ready` flag — same gate applies to STT (don't transcribe before the service
  is constructed)
- `run_in_executor(None, ...)` pattern for blocking work (Day 8 used it for `InputStream.start/stop`)
- Loguru with request IDs flowing through related operations
- The `VoiceEvent` discriminated-union pattern in `frontend/src/hooks/useWebSocket.ts`
  (Day 8 extended it for `recording_saved`; Day 9 extends it again)

---

## 3. End-of-day data flow (target)

```
Alt+Space released
        │
        ▼
ptt_end event ──► dispatcher
                     │
                     ├─ stop_recording (Day 8, unchanged)
                     ├─ save WAV to data/recordings/{iso8601}.wav (Day 8, unchanged)
                     └─ emit recording_saved {path}
                                  │
                                  ▼
                          dispatcher (NEW Day 9 branch)
                                  │
                                  ├─ broadcast: transcribing {path}
                                  ├─ await stt_service.transcribe(path)
                                  │       │
                                  │       └─ POST audio to Groq Whisper
                                  │
                                  ├─ on success: broadcast transcription_complete {text, latency_ms}
                                  └─ on failure: broadcast transcription_failed {error}
                                                  │
                                                  ▼
                                          React: update chat panel
                                          ("transcribing…" ─► transcript appears, or
                                           friendly error toast)
```

The blue branch on the right is everything Day 9 adds. Nothing in `voice/audio.py`,
`desktop/hotkeys.py`, or the queue itself changes.

---

## 4. Task breakdown

Eight tasks, in dependency order. Each one is small enough to do, verify, and commit
mentally before moving on.

### 9.1 — Install and pin the Groq SDK

**What:** add `groq` to the backend requirements.

```powershell
.\.venv\Scripts\activate
pip install groq
pip freeze | findstr groq    # confirm the installed version
# then edit backend/requirements.txt to pin that exact version, same style as Day 8
```

**Why pin it:** CLAUDE.md §4 — *"verify versions before suggesting code. Gemini's Python
SDK in particular has shifted API shape multiple times."* The Groq SDK is younger and
ships breaking changes more often than google-generativeai. Pinning now means re-installs
on Day 10 / Day 11 won't silently break the transcription call.

**Decision to make:** sync `Groq` client vs `AsyncGroq` client?
- (a) `AsyncGroq` — natural fit for FastAPI's async event loop; no executor needed
- (b) `Groq` (sync) — wrapped in `run_in_executor`, matching the Day-8 pattern for blocking calls
- Recommended: **(a) `AsyncGroq`** if available in the installed version. It avoids one
  unnecessary thread hop and the SDK's async path is well-supported. If `AsyncGroq` does not
  exist or behaves oddly with the pinned version, fall back to (b) — both work.

**Acceptance:** `python -c "from groq import AsyncGroq; print(AsyncGroq.__module__)"`
prints a module path with no error.

---

### 9.2 — Settings additions

**File:** `backend/config/settings.py`

Add five fields to the `Settings` class. Reason for each spelled out so Day-12 hardening
knows what's tweakable:

| Field | Type | Default | Rationale |
|---|---|---|---|
| `groq_api_key` | `SecretStr` | `...` (required) | Likely already added on Day 1. If not, add now. Required = no silent fallback. |
| `stt_model` | `str` | `"whisper-large-v3"` | Pinned name in settings so Day-12 can swap to `whisper-large-v3-turbo` without touching code. |
| `stt_language` | `str \| None` | `"en"` | Whisper auto-detects, but hinting `en` improves accuracy for the technical terms in §6. `None` = auto-detect. |
| `stt_temperature` | `float` | `0.0` | Deterministic transcription — important when you re-test the same WAV. |
| `stt_timeout_seconds` | `float` | `10.0` | Hard ceiling on the Groq call. A hung connection cannot be allowed to wedge the dispatcher. |

**No magic numbers** — every one of these gets read from `settings.<field>` inside
`STTService`, never hard-coded.

**Acceptance:** `python -c "from backend.config.settings import settings; print(settings.stt_model)"`
prints `whisper-large-v3` with no validation error.

---

### 9.3 — Build `STTService`

**File:** `backend/voice/stt.py` (new file, sibling of Day-8's `audio.py`)

**Public surface:**

```python
# Module docstring should say: "Cloud STT via Groq Whisper-large-v3. No local fallback in v1
# per Version_1_plan.md — on failure, dispatcher emits transcription_failed."

class TranscriptionResult(BaseModel):
    """Pydantic model so it serialises straight onto the WebSocket payload."""
    text: str
    latency_ms: float
    model: str
    language: str | None

class STTError(Exception):
    """Anything that prevents a transcription. Carries a human-readable message
    suitable for sending to the UI verbatim."""

class STTService:
    def __init__(
        self,
        api_key: str,
        model: str,
        language: str | None,
        temperature: float,
        timeout_seconds: float,
    ) -> None: ...

    async def transcribe(self, audio_path: Path) -> TranscriptionResult: ...

    async def close(self) -> None: ...   # for lifespan shutdown
```

**Decisions to make (suggest, don't just write — present these to me before implementing):**

1. **Path vs bytes as input?** The dispatcher already knows the path (Day 8 puts it in
   the `recording_saved` event payload). Two options:
   - (a) `transcribe(audio_path: Path)` — STTService opens the file. Simpler.
   - (b) `transcribe(audio_bytes: bytes, filename: str)` — caller reads. More testable.

   *Lean:* (a) for now. Day-9 only has one caller (the dispatcher). Switching to (b) later
   is a 5-line refactor. Tests can use a real WAV file from `backend/tests/fixtures/`.

2. **`response_format`?** Groq's transcription endpoint accepts `text`, `json`, or
   `verbose_json`.
   - `text` — minimal, just the string. Cheapest.
   - `json` — `{text: str}`. Same data plus a stable shape.
   - `verbose_json` — adds per-segment timestamps and detected language. Useful later for
     diarisation or word-level UI, but Day 9 doesn't need it.

   *Lean:* `json`. Stable shape, no extra cost, plays nicely with the `TranscriptionResult` model.

3. **Latency logging — `time.perf_counter()` or `time.monotonic()`?** Either works.
   `perf_counter` has higher resolution; pick one and stay consistent across the
   codebase. Day 8 didn't measure latency yet, so Day 9 sets the precedent for the rest of
   the voice pipeline.

**Implementation skeleton with explanation comments (the CLAUDE.md §1 style):**

```python
# Construct the AsyncGroq client once in __init__ and reuse across calls.
# Each AsyncGroq client owns an httpx.AsyncClient under the hood; building a new one
# per transcription would force a new TLS handshake every PTT release and add ~100ms.
self._client = AsyncGroq(api_key=api_key, timeout=timeout_seconds)

async def transcribe(self, audio_path: Path) -> TranscriptionResult:
    # Perf counter for latency; logged regardless of success/failure so we can
    # tell "Groq is slow" apart from "Groq is failing" when triaging.
    start = perf_counter()
    try:
        with audio_path.open("rb") as fh:
            # Groq's SDK expects a (filename, file_obj, content_type) tuple for `file`.
            # Filename is just metadata; content matters.
            response = await self._client.audio.transcriptions.create(
                file=(audio_path.name, fh, "audio/wav"),
                model=self._model,
                response_format="json",
                language=self._language,
                temperature=self._temperature,
            )
    except (GroqError, httpx.HTTPError, asyncio.TimeoutError) as exc:
        latency_ms = (perf_counter() - start) * 1000
        logger.warning("stt failed after {:.0f}ms: {}", latency_ms, exc)
        # Re-raise as STTError so the dispatcher branch has one exception type to catch
        # and one canonical user-facing message to surface.
        raise STTError("I couldn't hear that — try again.") from exc

    latency_ms = (perf_counter() - start) * 1000
    text = response.text.strip()
    logger.info("stt ok: '{}' ({:.0f}ms, model={})", text[:60], latency_ms, self._model)
    return TranscriptionResult(
        text=text, latency_ms=latency_ms,
        model=self._model, language=self._language,
    )
```

**Important — verify the actual SDK call shape against the installed version.** The
exception class might be `GroqError`, `APIError`, `groq.APIConnectionError`, etc.
depending on which version pip installed. CLAUDE.md §4 applies: open the installed
`groq` package source (`.venv/Lib/site-packages/groq/__init__.py`) before writing the
import, and update the `except` clause to match.

**Acceptance:** Day-8's smoke-test fixture (`backend/tests/test_audio_smoke.py`) saved
a real WAV. Reuse it in a Day-9 smoke test:

```python
# backend/tests/test_stt_smoke.py — run as: python -m backend.tests.test_stt_smoke
# Records 3s of audio (reusing Day 8 logic), then calls STT.
# Expected output: prints transcript + latency, exits 0.
```

---

### 9.4 — Wire `STTService` into the lifespan

**File:** `backend/main.py` (lifespan context manager)

Day 7 introduced the lifespan pattern; Day 8 added `app.state.audio_recorder` to it. Day 9
adds `app.state.stt_service` next to it.

```python
# In lifespan, after audio_recorder is constructed:
stt = STTService(
    api_key=settings.groq_api_key.get_secret_value(),
    model=settings.stt_model,
    language=settings.stt_language,
    temperature=settings.stt_temperature,
    timeout_seconds=settings.stt_timeout_seconds,
)
app.state.stt_service = stt
# app.state.ready stays where it was — set True only after BOTH recorder and STT
# are constructed, so that an early PTT press cannot trigger transcription with
# a half-built service. This is the same gate Day 8 added.
```

On shutdown: `await app.state.stt_service.close()` before the recorder shuts down. Order
doesn't really matter (they're independent) but keep "STT first, then recorder" as a habit
so the order in shutdown mirrors LIFO of construction.

**Acceptance:** boot the backend and confirm two log lines appear in sequence:

```
audio recorder initialized
stt service initialized: model=whisper-large-v3
```

---

### 9.5 — New dispatcher branch (the Day-8 hand-off point)

**File:** `backend/main.py`, function `_handle_event_side_effects`

Add the new branch **after** the existing `ptt_end` branch and **before** `mute_toggle`
(keeps the branches in roughly chronological order of when they fire in a session).

```python
elif etype == "recording_saved":
    # The audio is already on disk. Tell the UI we're transcribing so the badge
    # doesn't sit silent for 1+ seconds while Groq works, then dispatch the call.
    path = Path(event.payload["path"])
    await broadcast_voice_event({"type": "transcribing", "path": str(path)})

    try:
        result = await app.state.stt_service.transcribe(path)
    except STTError as exc:
        # STTError messages are pre-sanitised for UI display (see 9.3).
        await broadcast_voice_event({
            "type": "transcription_failed",
            "error": str(exc),
        })
        return

    await broadcast_voice_event({
        "type": "transcription_complete",
        "text": result.text,
        "latency_ms": result.latency_ms,
    })
```

**Decisions to make:**

1. **Keep `recording_saved` flowing to the UI, or drop it?** Day 8's status §4 flagged this.
   - (a) Keep it as a debug signal (current behaviour). UI still flashes the filename briefly.
   - (b) Stop broadcasting it; replace entirely with `transcribing`.
   - *Lean:* (a) for Day 9. The filename badge is useful while debugging cloud-call
     flakiness, and removing it is one line whenever the Day-17 polish pass happens.

2. **Should `transcribing` carry the audio duration?** Cheap to compute (Day 8's
   `AudioRecorder` could expose it on the `recording_saved` payload), and gives the UI a
   spinner with an upper bound. Optional for Day 9 — defer to Day 12 polish if not
   trivially free.

3. **Sequential vs concurrent transcriptions?** Right now the dispatcher is a single
   consumer, so it's already sequential. Good. If you later parallelise PTT (you can't,
   it's mono mic) or wake-word, revisit. For Day 9: no `asyncio.create_task`, no
   concurrency, no race conditions to think about.

**Acceptance:** PTT a sentence; backend logs in order:

```
ptt_start
stream opened
ptt_end
stream closed
recording saved: data/recordings/2026...wav
transcribing: data/recordings/2026...wav
stt ok: 'your sentence here' (820ms, model=whisper-large-v3)
ws broadcast: transcription_complete
```

---

### 9.6 — Frontend: extend `VoiceEvent` union and render transcripts

**Files:**
- `frontend/src/hooks/useWebSocket.ts` — extend the union (same pattern Day 8 used)
- `frontend/src/App.tsx` (or a new `ChatPanel.tsx`)

**Extend the discriminated union with three new variants:**

```ts
type VoiceEvent =
  | { type: "ptt_start" }
  | { type: "ptt_end" }
  | { type: "mute_toggle"; muted: boolean }
  | { type: "recording_saved"; path: string }
  // new for Day 9:
  | { type: "transcribing"; path: string }
  | { type: "transcription_complete"; text: string; latency_ms: number }
  | { type: "transcription_failed"; error: string };
```

**UI behaviour:**

- `transcribing` → status badge says "Transcribing…" (replacing the filename flash)
- `transcription_complete` → append `{ role: "user", text }` to a chat-message list; badge
  reverts to idle. Also log latency to console for now (Day 13's polish day can surface
  it nicer).
- `transcription_failed` → small red toast or banner with the error string. Auto-fade
  after 3 seconds.

**Decisions to make:**

1. **Inline in `App.tsx` or new `ChatPanel.tsx`?** The architecture skill (§Folder structure)
   already lists `frontend/src/components/ChatPanel.tsx`. Day 9 is the natural time to
   create it — even if it's just a `<ul>` of messages today, Day 11 will add assistant
   messages and Day 23 will add PDF-summary cards. Better to introduce the component now
   and grow it than refactor later.

2. **Persist transcripts across reloads?** No — backend SQLite (Day 5) is where messages
   live. Day 9's UI list can be in-memory React state; persistence comes when `/chat`
   starts saving real messages (Day 11). Don't build localStorage caching.

**Acceptance:** PTT, say "ABL1 kinase inhibitor binding affinity at T315I"; transcript
appears as a chat bubble within ~2 s. Disconnect from internet, PTT again; red error
banner appears, no crash, app still responsive.

---

### 9.7 — Error path verification

This is its own task, not an afterthought. CLAUDE.md §7 lists graceful error handling as a
hard rule; the only way to know it works is to actively break things.

Three failure modes to actively reproduce and confirm each surfaces a clean UI message:

| Failure | How to reproduce | Expected UI |
|---|---|---|
| Bad API key | Temporarily edit `.env`, set `GROQ_API_KEY=invalid`, restart backend | "I couldn't hear that — try again." |
| Network down | Disable Wi-Fi between PTT release and Groq response | Same message, plus log line `stt failed after 10000ms: ...` (the timeout) |
| Empty audio | Hold-and-release Alt+Space in <100 ms (silence) | Groq returns empty `text`. Decide: show empty transcript? show "I didn't hear anything"? *Lean:* "I didn't hear anything" when `text` is empty, surfaced as a `transcription_failed` event with that as the error message. |

Don't ship without doing all three. The empty-audio case in particular is easy to forget.

---

### 9.8 — Update `docs/journal.md`

One line, end of day. Same convention as previous days:

```
Day 9 — Groq Whisper STT wired into dispatcher via transcription_complete event.
        Latency ~800ms on 3s utterance. ABL1/T315I/RNA-seq transcribed correctly.
```

---

## 5. Completion criteria (mirror Day-9 entry in `Day_by_Day_Plan_v2.md`)

- [ ] Say a sentence with technical terms (e.g. "ABL1 kinase inhibitor binding at T315I
      shows 40-fold shift") → accurate transcript in chat panel
- [ ] Groq latency consistently < 1.5 s for ~5-second utterance (log-confirmed across at
      least 3 utterances)
- [ ] Network error → clean "couldn't hear you" banner, no Python traceback in logs, app
      still responsive afterwards
- [ ] Bad API key → same clean error path
- [ ] Empty audio (mis-press) → "I didn't hear anything", no crash
- [ ] Backend boots with `audio recorder initialized` AND `stt service initialized` log
      lines, in that order
- [ ] `app.state.ready` is gated on BOTH subsystems being constructed
- [ ] `recording_saved` still flows to the UI (debug signal, intentional)
- [ ] All five new events (`transcribing`, `transcription_complete`,
      `transcription_failed`) added to `VoiceEvent` union and handled in the React side

---

## 6. Manual verification script

```powershell
# 1. Clean start (same as Day 8)
netstat -ano | findstr :8000

# 2. Launch
cd frontend; npm run dev
# new terminal:
python -m backend.desktop

# 3. Wait for both log lines:
#       audio recorder initialized
#       stt service initialized: model=whisper-large-v3

# 4. Happy path — short utterance
#    Hold Alt+Space ~2s, say "hello world", release
#    Expected: chat bubble "hello world" within ~1.5s, latency log < 1500ms

# 5. Happy path — technical terms
#    Hold Alt+Space ~5s, say:
#    "ABL1 kinase, T315I mutation, RNA-seq pipeline, twenty-five micromolar"
#    Expected: all technical terms transcribed correctly (T315I as "T315I",
#    not "T 315 I" — Whisper-large-v3 handles this; if it doesn't, file a Day-12 fix note)

# 6. Empty-audio path
#    Tap-and-release Alt+Space fast (<100ms)
#    Expected: red banner "I didn't hear anything"

# 7. Network-failure path
#    Disable Wi-Fi, PTT a sentence
#    Expected: banner appears after ~10s (the timeout), exact message
#    "I couldn't hear that — try again."

# 8. Recovery
#    Re-enable Wi-Fi, PTT again
#    Expected: transcribes normally. No restart needed.

# 9. Clean shutdown
#    Click ✕ on the window. Confirm no leftover python.exe, same as Day 8.
```

If any of the nine steps fail, **don't proceed to Day 10**. Voice-loop quality is the
single most-protected item per Version_1_plan.md §"Drop-Cut Order" and the Week-2 latency
budget. Day 11 assumes STT just works.

---

## 7. Watch-outs (the gotchas I want to remember when I'm in flow)

1. **Verify the Groq SDK API against the installed version before writing imports.** The
   SDK has shifted; `from groq import AsyncGroq` works in recent versions but the
   exception classes (`GroqError`, `APIError`, `APIConnectionError`) move around. Open
   `.venv/Lib/site-packages/groq/` and read the actual symbols.

2. **WAV header matters.** Day 8 uses stdlib `wave` to write proper RIFF headers — good.
   If Day 9 ever passes raw `bytes` to Groq, that's the bug-of-the-day waiting to happen.
   For Day 9, we pass a file path and let Groq read the file; nothing to worry about. Just
   don't refactor to `transcribe(bytes)` without re-checking that the bytes include the
   header.

3. **Groq's free tier rate-limits per minute, not per day.** Aggressive testing (e.g.
   pressing PTT 30 times in a minute while debugging) can hit `429 Too Many Requests`.
   `STTError` will surface this cleanly as "I couldn't hear that" but the logs should show
   the real cause. Don't be surprised by it.

4. **`asyncio.TimeoutError` is a different exception from `httpx.TimeoutException`.** Both
   can fire depending on whether the timeout is enforced by the SDK config or by the
   underlying transport. Catch both in `STTService.transcribe`.

5. **Whisper transliterates aggressively.** "T315I" is fine, but "ABL1" sometimes comes
   out as "ABL one" or "able one" in non-en mode. Setting `language="en"` materially
   helps. If transcription quality of identifiers is unacceptable, Day 12 can try
   `prompt="ABL1 T315I RNA-seq kinase"` (a Whisper hint) before swapping models.

6. **Latency logging — log it on failure paths too.** When Groq is slow vs broken, you
   need both numbers to triage. The skeleton in §9.3 does this; don't strip it later.

7. **Loguru `bind` for request IDs.** Day 3 introduced request IDs in middleware. STT
   isn't called from an HTTP request, it's called from the dispatcher. Bind a synthetic
   ID (e.g. the WAV filename's iso8601 stamp) into the logger context for the duration of
   the transcribe call — makes the log trail readable when transcriptions overlap.

8. **PyWebView dev-mode reload.** Vite HMR can sometimes confuse the WebSocket state when
   you edit `useWebSocket.ts`. If the UI stops receiving events after an edit, close and
   relaunch the PyWebView window — don't waste 20 minutes debugging an HMR ghost.

---

## 8. Out of scope (deliberately deferred)

Do not do any of these on Day 9 even if tempted. They have their own days.

- **Auto-stop on silence in the audio recording** — Day 12 (audio robustness).
- **Fallback to a different STT provider on Groq failure** — explicitly cut from v1 per
  Version_1_plan.md §"If Groq STT has issues."
- **Streaming transcription** — Groq supports it but Day 9 uses synchronous-style file
  upload. Streaming is overkill for utterances under 30 seconds.
- **Saving the transcript to SQLite** — that's Day 11's `services/conversation.py` job,
  where messages get an LLM response and both halves get persisted together.
- **Showing latency in the UI** — Day 13 polish. Console-log only for Day 9.
- **Custom prompt-engineering Whisper for technical vocab** — only if §6 step 5 reveals
  unacceptable accuracy. Default first, optimise later.

---

## 9. Hand-off to Day 10 (Piper TTS)

Day 10 is the symmetric problem: turn text into speech. By end of Day 9 the dispatcher
will end with a `transcription_complete` event carrying user text. Day 10 will:

1. Add a stub `chat_response` event that — for now — just echoes the user's text back
2. Build `TTSService` and wire it into a new `elif etype == "chat_response"` branch
3. Speak the echo via Piper

So on Day 9, **leave one TODO comment** in `_handle_event_side_effects` after the
`transcription_complete` branch:

```python
# TODO Day 10: add elif etype == "chat_response" branch — TTS speaks the text.
# TODO Day 11: replace echo with real LLM call via services/conversation.py.
```

That comment is the breadcrumb the next session follows.

---

## 10. Files expected to change

```
NEW:
  backend/voice/stt.py
  backend/tests/test_stt_smoke.py
  frontend/src/components/ChatPanel.tsx          (optional but recommended — see 9.6)

EDIT:
  backend/config/settings.py                     (+5 STT fields)
  backend/main.py                                 (lifespan: stt_service init/close;
                                                   dispatcher: recording_saved branch)
  backend/requirements.txt                        (+groq pinned)
  frontend/src/hooks/useWebSocket.ts              (+3 events in VoiceEvent union)
  frontend/src/App.tsx                            (mount ChatPanel; handle 3 new events)
  docs/journal.md                                 (+1 line)
```

---

## 11. Commit message (target)

```
feat: groq whisper stt integration

- Add STTService in backend/voice/stt.py: AsyncGroq client, whisper-large-v3,
  per-call latency logging, single STTError type for sanitised UI messages
- Extend Settings with stt_model / stt_language / stt_temperature / stt_timeout
- Wire stt_service into lifespan; gate app.state.ready on both recorder and stt
- New dispatcher branch: recording_saved triggers transcribe → broadcasts
  transcription_complete{text, latency_ms} or transcription_failed{error}
- Frontend: extend VoiceEvent union with transcribing /
  transcription_complete / transcription_failed; introduce ChatPanel to render
  user-side transcript bubbles
- Manual verified: happy path, technical-term accuracy, empty audio, bad key,
  network down. Median latency ~800ms on 3s utterance.
```
