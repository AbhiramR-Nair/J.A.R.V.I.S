# Project Status — Day 9

**Period covered:** Day 9 (Speech-to-Text via Groq Whisper)
**Status:** Complete — all completion criteria met. Ready for Day 10 TTS.
**Environment:** Windows 11, Python 3.13.5, groq 1.2.0, sounddevice 0.5.5

> Checkpoint summary for Day 9: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 10.

---

## 1. What has been done

Day 9 completed the first cloud call in the voice loop. The WAV file that Day 8 saves is
now transcribed by Groq Whisper-large-v3 and the resulting text appears as a chat bubble
in the React frontend, all driven through the existing dispatcher queue. Seven subsystems
were touched: the Groq SDK, backend settings, the STTService, the FastAPI lifespan, the
dispatcher, the WebSocket event union, and the React component tree.

| Task | What landed | Status |
|---|---|---|
| 9.1 — Dependencies | `groq==1.2.0` installed; pinned in `backend/requirements.txt` (alphabetical, between `google*` and `grpcio`) | Done |
| 9.2 — Settings | 4 STT fields added to `Settings`: `stt_model`, `stt_language`, `stt_temperature`, `stt_timeout_seconds`; `groq_api_key` was already present from Day 1 | Done |
| 9.3 — STTService | `backend/voice/stt.py`: `TranscriptionResult` (Pydantic), `STTError` (UI-safe exception), `STTService` (one persistent `AsyncGroq` client, per-call latency logging, empty-audio guard); `backend/tests/test_stt_smoke.py` added | Done |
| 9.4 — Lifespan wiring | `app.state.stt_service` constructed after `audio_recorder`; `app.state.ready` gated on both; `stt_service.close()` called first on shutdown | Done |
| 9.5 — Dispatcher branch | `elif etype == "recording_saved"` in `_handle_event_side_effects`: broadcasts `transcribing` → calls STT → broadcasts `transcription_complete` or `transcription_failed`; `recording_saved` excluded from outer broadcast loop | Done |
| 9.6 — Frontend | `VoiceEvent` union extended with 3 new types; `ChatPanel.tsx` created (user bubbles, ready for Day 11 assistant bubbles); `App.tsx` wired: "transcribing…" badge, chat message append, red error toast with 3s auto-fade | Done |
| 9.7 — Error path verification | All 3 failure modes tested and confirmed: bad API key, network down (WiFi off before PTT), empty audio mis-press | Done |
| 9.8 — Journal | `docs/journal.md` updated with Day 9 one-liner | Done |

**Completion criteria verified:**

| Criterion | Result |
|---|---|
| Technical terms transcribed accurately ("ABL1 kinase T315I mutation RNA-seq") | ✓ All terms correct; `language="en"` setting helps |
| Groq latency consistently < 1.5s for ~5s utterance (3 runs) | ✓ Observed ~800–1100ms |
| Bad API key → clean toast, no traceback in logs | ✓ "I couldn't hear that — try again." |
| Network down → same toast after ~10s timeout | ✓ Confirmed; log shows `stt failed after 10000ms` |
| Empty audio mis-press → "I didn't hear anything." toast | ✓ Confirmed after fix (see §3) |
| Backend boots with both `audio recorder initialized` AND `stt service initialized` log lines | ✓ In that order |
| `app.state.ready` gated on both subsystems | ✓ |
| All 5 new events in `VoiceEvent` union and handled in React | ✓ |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. One persistent `AsyncGroq` client, constructed in `__init__`

`AsyncGroq` owns an `httpx.AsyncClient` under the hood. Building a new client per
transcription forces a new TLS handshake each time (~100ms overhead on a warm connection).
For a voice assistant where every millisecond counts, this is unacceptable. The client is
constructed once in `STTService.__init__` and reused for every call. The lifespan calls
`stt_service.close()` on shutdown to release the underlying connection pool cleanly.

### 2. `AsyncGroq` over sync `Groq` + executor

The Groq SDK ships both a sync `Groq` and an async `AsyncGroq` client. Using the async
client removes one unnecessary thread-hop (no `run_in_executor`), which keeps the asyncio
event loop free during the Groq HTTP call. Day 8 used `run_in_executor` for `sounddevice`
because PortAudio's start/stop are genuinely blocking C calls with no async equivalent.
The Groq SDK's async path is native `await`, so no executor is needed.

### 3. `GroqError` as the single catch + belt-and-suspenders `httpx.TimeoutException`

Inspecting the installed SDK's MRO confirmed that `GroqError` is the base class for all
SDK exceptions: `AuthenticationError`, `RateLimitError`, `APIConnectionError`, and
`APITimeoutError` all inherit from it. A single `except GroqError` covers everything the
SDK can throw. `httpx.TimeoutException` is caught alongside it as a belt-and-suspenders
catch for any transport-level timeout that might leak past the SDK's own wrapping.

### 4. `response_format="json"` over `"text"`

Groq's transcription endpoint also accepts `"text"` (returns a plain string) and
`"verbose_json"` (adds per-segment timestamps). `"json"` returns `{text: str}` — a stable
shape that maps cleanly onto `TranscriptionResult` without needing to parse free text.
`"verbose_json"` is deferred to a future day if word-level timestamps become useful.

### 5. `STTError` as a single UI-safe exception type

Rather than letting `GroqError`, `httpx.TimeoutException`, and `asyncio.TimeoutError`
propagate with their raw SDK messages (which contain internal details inappropriate for
UI display), all failure paths re-raise as `STTError` with a human-readable message. The
dispatcher catches only `STTError` and sends its message verbatim to the frontend. This
means the UI can never accidentally show a raw stack trace or an API key fragment in an
error message.

### 6. Latency logged on both success and failure paths

Logging latency only on success means that when Groq is slow (e.g. approaching rate
limit), the slow path is invisible in the logs — you can only see that calls are failing,
not *how slowly* they fail before timing out. Both branches log `latency_ms` so "Groq is
slow" and "Groq is failing" are distinguishable when triaging.

### 7. Loguru `bind` for synthetic request IDs in the dispatcher

HTTP requests already carry a UUID from the Day 3 middleware. The STT call is triggered
from the dispatcher (no HTTP request), so it has no natural request ID. Binding the WAV
filename's ISO8601 timestamp (`audio_path.stem`) as `req_id` groups all log lines for a
single transcription (start, Groq call, result, any error) together in the log file. This
makes it easy to see each PTT session as a coherent unit when tailing the log.

### 8. `ChatPanel.tsx` created as its own component on Day 9

The architecture skill listed `ChatPanel.tsx` as a planned component. Day 9 created it
with user-only bubbles rather than inlining the list in `App.tsx`, because Day 11 will add
assistant messages and Day 23 will add PDF-summary cards — both require the same list
component. A minor upfront investment avoids a refactor later.

---

## 3. Problems faced and how they were handled

### Problem 1 — React 18 automatic batching silently dropped the `transcription_failed` toast

**What happened:** the error toast (tested with a bad API key) was not appearing in the
frontend, even though the backend logs confirmed `stt failed` and the `transcription_failed`
broadcast was being called.

**Root cause:** after the STT branch broadcast `transcription_failed`, the outer
`_dispatch_events` loop immediately called `await ws_manager.broadcast(event)` where
`event` was the original `recording_saved` dict. Both messages arrived at the browser
WebSocket within microseconds. React 18's automatic batching saw two `setLast(ev)` calls
before the browser could yield to the render cycle, batched them into a single state
update using the last value (`recording_saved`), and fired `useEffect([lastEvent])` only
once — with `lastEvent = recording_saved`. The `transcription_failed` handler (which sets
`errorToast`) was never executed.

**Fix:** excluded `recording_saved` from the outer broadcast in `_dispatch_events`:

```python
if event.get("type") != "recording_saved":
    await ws_manager.broadcast(event)
```

`recording_saved` is a queue-internal event consumed entirely by the STT branch
(`transcribing` → `transcription_complete` / `transcription_failed`). It does not need to
reach the UI separately. The `transcribing` event already signals to the UI that a
recording was picked up.

### Problem 2 — Empty audio mis-press gave no feedback

**What happened:** a fast tap-and-release of Alt+Space produced no UI response — no toast,
no badge change. The app appeared to silently ignore the press.

**Root cause:** `stop_recording()` returns `b""` when recording never started (or the
buffer was empty). The `ptt_end` handler had `if wav_bytes: ...` with no `else` branch,
so empty audio was silently swallowed.

**Fix:** added an explicit `else` branch in the `ptt_end` dispatcher block:

```python
else:
    await ws_manager.broadcast({
        "type": "transcription_failed",
        "error": "I didn't hear anything."
    })
```

This fires immediately (no Groq call) and surfaces the same red toast as other error
paths, so the user gets consistent feedback regardless of which failure mode occurred.

---

## 4. Heads-up: downstream complications to watch

### `lastEvent` pattern in the frontend is fragile for rapid event sequences

The current `useVoiceEvents` hook returns the latest `VoiceEvent` and `App.tsx` reacts
to it via `useEffect([lastEvent])`. React 18 automatic batching means that if two events
arrive before the browser yields to the render cycle, only the last one is processed. Day
9 hit this exactly (Problem 1 above). The fix was to prevent back-to-back broadcasts from
the backend, but the underlying fragility in the frontend remains.

**Implication:** any future backend change that broadcasts two events in rapid succession
(e.g. Day 11's `llm_thinking` → `llm_response` pair, or Day 16's amplitude events) risks
the same silent-drop. The proper long-term fix is to replace the `lastEvent: VoiceEvent`
return value with an event queue (e.g. `useReducer` with an append action) so every event
is processed regardless of delivery timing. Defer this to Day 11 if the pattern causes
issues there; otherwise consider it Day 17 polish.

### `recording_saved` no longer reaches the UI

The filename-flash badge that Day 8 added (`saved: 20260523T...wav`) no longer fires,
because `recording_saved` is now excluded from the outer broadcast. The badge transitions
directly from `ptt_start` → "listening" → `transcribing` → transcript or error. This is
actually cleaner UX, but the Day 8 status doc described `recording_saved` as an intentional
debug signal. If Day 17's UI polish pass reinstates a file-level debug view, the broadcast
exclusion will need to be revisited.

### `ChatPanel` only handles `role: "user"` bubbles today

`ChatPanel.tsx` accepts `ChatMessage[]` typed as `{ role: "user" | "assistant"; text: string }`.
The assistant branch (`role === "assistant"`) renders a left-aligned neutral bubble. But no
assistant messages are added yet — that is Day 11's job (`services/conversation.py`
orchestrates LLM → TTS). Do not add assistant messages to the chat list before Day 11
wires the full conversation loop; partial wiring will produce UI state that is hard to
reason about.

### `app.state.ready` gate is checked only in `_handle_event_side_effects`, not in the STT call itself

`app.state.ready` prevents hotkey events from triggering audio or STT before the lifespan
finishes. However, if `app.state.stt_service` is somehow unavailable after startup (e.g.
an edge case where `AsyncGroq` raises during construction and the exception is swallowed),
`app.state.stt_service.transcribe()` would raise `AttributeError`. Day 9 does not guard
against this because `STTService.__init__` has no realistic failure mode with a valid API
key string. If Day 12 adds more complex initialization (e.g. model pre-warming), add an
`if not getattr(app.state, "stt_service", None): return` guard at the top of the
`recording_saved` branch.

### Groq rate-limit during aggressive testing

Groq's free tier rate-limits per minute, not per day. The `429 Too Many Requests` response
is caught by `GroqError` and surfaces as "I couldn't hear that — try again." in the UI —
the user message is correct, but the log will show the real HTTP status. During Day 10 or
Day 11 development, rapid PTT pressing (e.g. testing the TTS loop repeatedly) can hit this
limit. It resolves within 60 seconds. Not a bug, but worth knowing when debugging "sudden
failures" during a fast test session.

---

## 5. How to verify Day 9

```powershell
# 1. Clean start
netstat -ano | findstr :8000
# Stop-Process -Id <PID> if anything shows

# 2. Launch
cd frontend; npm run dev
# new terminal:
python -m backend.desktop

# 3. Check log for both startup lines (in this order):
#       audio recorder initialized
#       stt service initialized: model=whisper-large-v3

# 4. Happy path
#    Hold Alt+Space ~3s, say "ABL1 kinase T315I mutation RNA-seq"
#    Expected: badge → "transcribing…", then transcript bubble appears in chat

# 5. Bad API key
#    Edit .env: GROQ_API_KEY=invalid, restart backend
#    PTT a sentence → red toast "I couldn't hear that — try again."
#    Restore the real key

# 6. Network down
#    Disable Wi-Fi FIRST, then PTT a sentence
#    Expected: red toast after ~10s (the timeout)
#    Re-enable Wi-Fi, PTT again → transcribes normally, no restart needed

# 7. Empty audio mis-press
#    Tap-and-release Alt+Space very fast (<100ms)
#    Expected: red toast "I didn't hear anything." immediately

# 8. Clean shutdown
#    Click ✕ — confirm no leftover python.exe
```

---

## 6. Open items before Day 10

- [ ] Consider replacing the `lastEvent` pattern with an event queue in `useVoiceEvents`
      if Day 10's TTS events also arrive in rapid succession.
- [ ] The max-duration auto-stop (30s) still silently loses the buffer on `ptt_end` —
      deferred to Day 12 per Day 8 plan.
- [ ] `POST /audio/device` still does not validate the new device opens cleanly — deferred
      to Day 17 UI polish.

---

## 7. Files changed this day

```
NEW:
  backend/voice/stt.py
  backend/tests/test_stt_smoke.py
  frontend/src/components/ChatPanel.tsx

EDIT:
  backend/config/settings.py          (+4 STT fields under new # STT section)
  backend/main.py                     (import STTService + STTError; lifespan: stt_service
                                       init/close; dispatcher: recording_saved branch;
                                       ptt_end: empty-audio feedback; outer broadcast
                                       excludes recording_saved)
  backend/requirements.txt            (+groq==1.2.0)
  frontend/src/hooks/useWebSocket.ts  (+3 events in VoiceEvent union)
  frontend/src/App.tsx                (import ChatPanel; +messages/transcribing/errorToast
                                       state; new event handlers; updated statusLabel;
                                       mount ChatPanel + error toast in JSX)
  docs/journal.md                     (+Day 9 line)
```

---

## 8. Commit

```
feat: groq whisper stt integration

- Add STTService in backend/voice/stt.py: AsyncGroq client, whisper-large-v3,
  per-call latency logging, single STTError type for sanitised UI messages
- Extend Settings with stt_model / stt_language / stt_temperature / stt_timeout_seconds
- Wire stt_service into lifespan; gate app.state.ready on both recorder and stt
- New dispatcher branch: recording_saved triggers transcribe → broadcasts
  transcription_complete{text, latency_ms} or transcription_failed{error}
- Exclude recording_saved from outer broadcast loop (fixes React 18 batching
  issue that silently dropped the error toast)
- Empty audio mis-press now broadcasts transcription_failed immediately
- Frontend: extend VoiceEvent union; introduce ChatPanel for user transcript
  bubbles; badge shows "transcribing…"; red error toast auto-fades after 3s
- Manual verified: happy path, technical-term accuracy (ABL1/T315I/RNA-seq),
  empty audio, bad key, network down. Median latency ~900ms on 3-5s utterance.
```
