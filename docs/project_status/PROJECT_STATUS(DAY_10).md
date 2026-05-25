# Project Status — Day 10

**Period covered:** Day 10 (Text-to-Speech via Piper subprocess + sounddevice playback)
**Status:** Complete — all completion criteria met. Ready for Day 11 conversation state machine.
**Environment:** Windows 11, Python 3.13.5, sounddevice 0.5.5, Piper (Windows amd64 build), `en_US-lessac-medium` voice (22050 Hz mono int16)

> Checkpoint summary for Day 10: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 11.

---

## 1. What has been done

Day 10 completed the second half of the voice loop — the assistant now speaks back. Piper
is invoked as a per-utterance subprocess that streams raw PCM on stdout; the bytes are
collected, wrapped as a numpy int16 array, and played through sounddevice. The TTS path
is exposed both as a synchronous `POST /speak` debug endpoint (curl-able, awaits playback)
and as a fire-and-forget background task in `POST /chat` (text returns immediately, audio
plays out-of-band). Eight files were touched: Piper binary download, settings, the new
`TTSService`, the FastAPI lifespan, the chat route, the voice router, the voice models,
and a smoke test.

| Task | What landed | Status |
|---|---|---|
| 10.1 — Piper binary + voice | `piper/piper/piper.exe` (Windows amd64 zip extracted nested), `piper_voices/en_US-lessac-medium.onnx` + `.onnx.json` sidecar. PowerShell smoke test produced a clean WAV | Done |
| 10.2 — CLI flag truth-check | Confirmed `--output_raw` (underscore, not hyphen) is the raw-PCM-to-stdout flag for this Piper release. Sample rate read from voice's `.onnx.json` = 22050 Hz | Done |
| 10.3 — Playback strategy | Decision: **Option A** (all-at-once buffer → single `sd.play` call). Streaming deferred — adds ~90 lines for marginal latency win on typical replies | Done |
| 10.4 — Settings | 5 TTS fields added to `Settings`: `piper_binary_path` (Path), `piper_voice_path` (Path), `tts_sample_rate=22050`, `tts_output_device=None`, `tts_timeout_seconds=30` | Done |
| 10.5 — TTSService | `backend/voice/tts.py`: `SynthesisResult` (Pydantic, no PCM bytes in payload), `TTSError` (UI-safe), `TTSService` (asyncio subprocess, `--output_raw`, `_play_sync` helper, per-call latency logging on both success and failure). `backend/tests/test_tts_smoke.py` added (SHORT + LONG) | Done |
| 10.6 — Lifespan wiring | `app.state.tts_service` constructed after `stt_service`; `app.state.ready` gated on all three subsystems; shutdown order LIFO (tts → stt → recorder) | Done |
| 10.7 — POST /speak | `backend/api/voice.py`: synchronous endpoint (awaits full playback). `SpeakRequest`/`SpeakResponse` models in `backend/models/voice.py` with 1–2000 char validation + whitespace-only rejection. Returns 503 on `TTSError` with sanitised message, 500 on anything else | Done |
| 10.8 — /chat fires TTS | `_speak_safely` helper added; `chat()` accepts `Request`, schedules `asyncio.create_task(_speak_safely(...))` before returning. Text appears in response immediately; audio follows ~1s later | Done |
| 10.9 — Edge cases | 6 manual tests via `/speak`: short / medium / 30-word buffer / technical vocab / numeric / whitespace-422. All pass; 30-word ends cleanly on "cases" | Done |
| 10.10 — Journal + commit | `docs/journal.md` updated with Day 10 one-liner. Commit `822df0e` landed | Done |

**Completion criteria verified:**

| Criterion | Result |
|---|---|
| Typing chat message produces spoken reply within ~2s of LLM returning | ✓ End-to-end ~1.0–1.5s after LLM (synth ~1s + playback start ~immediate) |
| No clipping, stutter, or audio glitches on 30+ word reply | ✓ Buffer test sentence ends cleanly on "cases" |
| Voice sounds natural (no cringe) | ✓ `lessac-medium` is acceptable; no voice swap needed |
| `POST /speak` works for short, long, and edge cases; 422 for empty/oversize/whitespace | ✓ All 6 cases verified |
| Bad binary path → clean `TTSError` via 503; no raw traceback | ✓ `FileNotFoundError` caught and re-raised as `TTSError` |
| Backend boots with three init lines (recorder, stt, tts) in order, clean shutdown | ✓ Order verified, no orphan `python.exe` |
| Synth, speak, fire-and-forget pattern, executor-wrap of sounddevice all explainable | ✓ |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. `asyncio.create_subprocess_exec` over blocking `subprocess.run`

The whole FastAPI loop must stay free to handle WebSocket frames, the chat request, and
the dispatcher queue. `subprocess.run` would block the event loop for the entire Piper
synth (~1s on typical replies, longer on long utterances). The asyncio variant returns a
`Process` whose `communicate()` is awaitable, so the loop continues servicing other tasks
while Piper runs. Same architectural choice Day 9 made for the Groq SDK.

### 2. `proc.communicate(text.encode())` rather than manual `stdin.write` + `stdout.read`

`communicate()` handles the stdin write, the stdout/stderr reads, and the process wait in
a single deadlock-safe call. Manually writing to stdin and reading stdout in sequence can
hang if Piper buffers more on stdout than the OS pipe can hold — the subprocess waits for
its stdout to drain, while we wait for stdin to be consumed. `communicate()` reads both
pipes concurrently from background tasks and avoids the trap.

### 3. `asyncio.wait_for(...)` timeout with explicit `proc.kill()`

`subprocess` natively has no timeout-then-kill semantics in async. Wrapping `communicate()`
in `wait_for(timeout=...)` raises `asyncio.TimeoutError` when exceeded; the `except` branch
calls `proc.kill()` to make sure the hung Piper process doesn't linger as a zombie. Without
the explicit kill, a runaway synth could pile up subprocesses across multiple `/speak`
calls.

### 4. Run sounddevice in `run_in_executor`, not on the event loop

`sd.play()` is non-blocking on its own (returns immediately while PortAudio plays on a
background thread), but `sd.wait()` blocks. For symmetric semantics — the caller awaits
playback completion — both calls go inside a synchronous helper `_play_sync` that runs in
the default thread pool via `loop.run_in_executor(None, ...)`. Day 8 used the same pattern
for `recorder.start_recording()` / `stop_recording()`. PortAudio is a blocking C API; the
executor is the only correct way to wait for it from asyncio.

### 5. `np.frombuffer(stdout, dtype=np.int16)` rather than `array.array` or struct parsing

`np.frombuffer` creates a numpy view over the underlying bytes object — zero-copy where
possible, O(1) instead of O(n) parsing. sounddevice's `play()` accepts numpy arrays
natively, so this is the smallest amount of work between subprocess stdout and PortAudio
buffer. Empty-buffer guard (`array.size == 0`) covers the edge case where Piper exits 0
but produces no PCM (corrupted voice file, malformed input).

### 6. Two endpoints with different concurrency models: sync `/speak` vs fire-and-forget `/chat`

`POST /speak` `await`s playback completion and returns latency metadata — the curl caller
explicitly wants to know "did the audio play?" so blocking until done is correct. The
chat path is the opposite: blocking the HTTP request for full playback (~3s for a 25-word
reply) would delay the text bubble in the UI and hold a connection slot. `asyncio.create_task`
schedules the speak coroutine on the loop without awaiting it; the HTTP response returns
immediately. Same coroutine, two callers, two latency profiles.

### 7. `_speak_safely` wraps `tts.speak()` because fire-and-forget has nowhere to surface errors

Once the HTTP response is back to the user, there's no longer a request to attach an error
to. Any exception in the background task would either crash the asyncio loop (if unhandled)
or be silently swallowed (if the task is never awaited). `_speak_safely` catches `TTSError`
at a warning level (user-facing failure, expected mode) and any other exception at exception
level (programmer bug, needs investigation), then returns. Day 11's state machine will
replace this with a WebSocket-broadcasted `speaking_failed` event so the UI can surface the
error visually.

### 8. `Field(min_length=1, max_length=2000)` + `@field_validator` for whitespace

`min_length=1` checks the *raw* string length, not the stripped length. `"   "` passes that
check (3 characters) and reaches Piper, which dutifully synthesises silence. A field
validator that calls `.strip()` and rejects empty results plugs the hole at the Pydantic
boundary, before any subprocess is spawned. Keeps the validation in one place rather than
duplicating it inside `TTSService.synthesize`.

### 9. Synthetic request ID `tts-{N}ch` for log grouping

The TTS path runs from two places — the route handler and the `/chat` background task —
neither of which has a natural request ID at the synth-call layer. The text length is a
weak unique-ish identifier within a small time window; good enough to group "synth start /
piper exit / playback done" log lines visually when tailing. A UUID would be more correct
but adds noise to every line for marginal gain. Future-self note: if concurrent traffic
ever becomes real, switch to `uuid4().hex[:8]`.

### 10. LIFO shutdown order: tts → stt → recorder

Reverse-construction order is the conservative pattern for resource cleanup. Today both
`tts_service.close()` and `stt_service.close()` are effectively no-ops or harmless, so the
order doesn't matter functionally. But establishing the convention now means future-Day 11
or Day 12 changes (e.g., a Piper warm cache or an STT model pre-load) get correct shutdown
ordering for free. Same reason Day 8 introduced `app.state.ready` even when only the
recorder needed gating.

### 11. Default `piper_binary_path = "piper/piper/piper.exe"` (nested) rather than moving files

The Windows amd64 zip extracts into `piper/piper/` rather than `piper/` flat. Three options:
move the files manually, write a setup script, or just point the default at the actual
location. The third is one line in settings, requires no filesystem operation, and is
trivially overridable by `.env` if a future zip extracts differently. The `.gitignore`
already excludes `piper/` recursively so the nested folder is covered.

---

## 3. Problems faced and how they were handled

### Problem 1 — `sd.play()` rejected its own `dtype` argument

**What happened:** the SHORT smoke test produced this:

```
synth ok: 44346samples @ 22050Hz (1096ms)
sounddevice playback error: _CallbackContext.start_stream() got multiple
values for argument 'dtype'
```

Synthesis was fine — 44346 int16 samples returned from Piper in ~1 second. Playback was
the failure point, before any audio reached the speakers.

**Root cause:** the `_play_sync` helper passed `dtype="int16"` to `sd.play()`. When the
input is already a `numpy.int16` array, sounddevice infers `dtype` from the array's own
dtype and forwards both values (the inferred one + the explicit kwarg) to its internal
`_CallbackContext.start_stream`, which then sees `dtype` specified twice. This is a
classic "well-meaning explicit kwarg conflicts with library auto-detection" issue. If the
input had been raw `bytes` or a Python list, the explicit kwarg would have been required;
with a typed numpy array it's redundant *and* harmful.

**Fix:** removed the `dtype` kwarg from the `sd.play()` call. The array's own `.dtype`
attribute is now the single source of truth.

```python
sd.play(array, samplerate=sample_rate, device=device)  # was: ..., dtype="int16"
```

### Problem 2 — Whitespace-only text passed validation and produced near-silence audio

**What happened:** while running Task 10.7's validation tests, the case `"   "` (three
spaces) was expected to return 422 but returned 200 with `num_samples=4410` — Piper had
produced ~0.2s of near-silent PCM.

**Root cause:** `Field(min_length=1, max_length=2000)` on the request model checks the raw
character count of the input string. `"   "` is three characters, so it passes. The
validation was checking "is the string non-empty" when the intent was "does the string
contain meaningful content."

**Fix:** added a Pydantic `@field_validator` that calls `.strip()` and raises if the result
is empty. This rejects blank-equivalent input at the Pydantic boundary before any
subprocess is spawned, and the 422 response format matches the empty-string case so the
client sees consistent error shapes.

```python
@field_validator("text")
@classmethod
def text_not_blank(cls, v: str) -> str:
    if not v.strip():
        raise ValueError("text must not be blank or whitespace only")
    return v
```

### Problem 3 — FastAPI trailing slash redirect ate the curl response body

**What happened:** during the first attempt at Task 10.8's verification, this returned
nothing visible:

```
curl -X POST http://localhost:8000/chat/ -H "Content-Type: application/json" -d "..."
```

No JSON, no error, just an empty line and a fresh prompt. The chat handler was being
reached (text reply was generated and TTS played), but curl printed nothing.

**Root cause:** the router is mounted with `prefix="/chat"` and the route is `@router.post("")`,
so the canonical URL is `/chat` (no trailing slash). When `/chat/` is requested, FastAPI's
default `redirect_slashes=True` responds with a 307 Temporary Redirect to `/chat`. curl
does not follow redirects unless `-L` is passed, so it silently consumed the 307 response
(which has no body) and exited.

**Fix:** none required in the code — the redirect behaviour is correct. The user-visible
fix was to drop the trailing slash from the curl URL. Documenting this here because
*every* future curl test of any FastAPI endpoint that doesn't end in a slash will hit the
same trap, and the silent-empty-response failure mode is genuinely confusing.

### Problem 4 — Initial Pydantic settings attempt used a globally-scoped string length cap

**What happened:** the first draft of `SpeakRequest` used:

```python
text: str
model_config = {"str_min_length": 1, "str_max_length": 2000}
```

This was caught before being tested but is worth recording.

**Root cause:** `str_min_length` / `str_max_length` in Pydantic v2's `model_config` apply
to *every* string field in the model and any nested models, not just the field they appear
near. If `SpeakRequest` later grew a `voice: str` or `language: str` field, those would
have inherited the 1–2000 bound silently.

**Fix:** moved the constraints onto the field directly via `Field(min_length=1, max_length=2000)`.
This is the per-field idiom — scoped, explicit, and what Pydantic's own docs recommend.

---

## 4. Heads-up: downstream complications to watch

### Concurrent `/chat` calls produce overlapping audio

`asyncio.create_task` schedules each TTS coroutine independently. Two `/chat` calls
within ~1 second of each other will run two `tts.speak()` calls in parallel, both calling
`sd.play()` — PortAudio mixes them, which usually sounds like garbled overlapping speech
or worse. No serialisation exists today.

**Implication:** Day 11's conversation state machine introduces a `speaking` state that
must gate new utterances. Until then, the assistant cannot be safely spammed. This is
acceptable for the typed-chat single-user flow today but is the first thing Day 11 will
fix.

### TTS errors in the `/chat` path are silent to the UI

`_speak_safely` logs `TTSError` at warning level but does not send anything to the
WebSocket. The chat panel shows the text reply and the user wonders why no audio played.

**Implication:** Day 11 will introduce `speaking_failed` (mirror of Day 9's
`transcription_failed`) broadcast over the existing WS channel, with the same red-toast
auto-fade pattern in the frontend. The handler is already conceptually in place; only the
broadcast wiring is missing. Until then, log-tailing is the only way to see TTS failures
during chat.

### No frontend chat input — typed `/chat` testing is curl-only

The current UI is voice-only (PTT → STT → user bubble). There is no text input box for
typing messages, so end-to-end verification of `/chat` requires curl. The plan does not
add an input until UI polish (~Day 17) or the conversation refactor (Day 11), whichever
comes first.

**Implication:** any Day 11/12 work that depends on testing `/chat` interactively will
either need a temporary input field or has to be tested via curl. The voice loop (Day 11
end goal) is fully testable via PTT once STT → LLM → TTS is stitched.

### `tts-{N}ch` request ID collides under concurrent traffic

The bound `req_id` in TTS logs uses text length as the unique-ish key. Two utterances of
the same character count running concurrently will have their log lines interleaved under
the same ID, defeating the grouping purpose.

**Implication:** moot today (single-user, single-utterance-at-a-time), but the moment Day
11's state machine allows queued utterances or Day 22+'s PDF summarisation produces multi-
chunk synth, the IDs need to switch to `uuid4().hex[:8]` or a per-call counter. One-line
change when the time comes.

### `_speak_safely` background tasks can outlive the lifespan

If the backend is shut down while a TTS task is mid-playback, `asyncio.create_task` has no
explicit cancellation hook in the lifespan teardown. The task may be silently cancelled
when the loop closes, or PortAudio may segfault if the executor thread is killed mid-write.
Not observed in testing, but a theoretical issue.

**Implication:** if shutdown-during-speak ever produces a crash or hang, the fix is to
track outstanding `_speak_safely` tasks in a set on `app.state` and `await asyncio.gather(
*tasks, return_exceptions=True)` in the lifespan shutdown block. Defer until evidence
warrants.

### Piper mispronounces technical vocabulary

`lessac-medium` will sometimes render `ABL1` as "able one" or "ay-bee-ell-one", and gene
symbols like `T315I` are inconsistent. This is a Piper / espeak phoneme-mapping limitation,
not a bug.

**Implication:** Piper supports phoneme-level overrides via SSML-ish input, but adding
them is a Month 2 polish item (and arguably premature: the voice is intelligible enough to
convey the answer). Flag if user feedback specifically asks for fidelity on gene names.

### Sample rate is hardcoded; voice swaps require a settings change

`tts_sample_rate=22050` is locked to `lessac-medium`. Swapping to (e.g.) `en_US-amy-low`
at 16000 Hz without updating settings produces chipmunk audio with no other error signal.

**Implication:** future Day 17 (voice selection UI) or Day 12 (audio polish) should read
sample rate from `voice_path.with_suffix('.onnx.json')` on `TTSService.__init__` rather
than trusting the settings value. Small refactor; defer until voice swapping is actually a
feature.

### Day 9 carryovers remain open

None of these were touched by Day 10 — they are explicitly Day 11+:
- React `useVoiceEvents` `lastEvent` pattern still drops back-to-back events; will bite
  hard when Day 11 adds `speaking_started` / `speaking_ended` next to `transcription_complete`
- 30s max-duration auto-stop on PTT still silently loses the buffer
- `POST /audio/device` still does not validate the new device opens cleanly

### Pydantic `model_config` foot-gun for string length

The aborted `model_config = {"str_min_length": ..., "str_max_length": ...}` attempt is
worth a written reminder. These are *model-wide* string constraints; per-field constraints
belong on `Field(...)`. Easy to confuse with `field_validator` in muscle memory.

### FastAPI trailing-slash redirect is a permanent foot-gun for curl testing

Any new endpoint registered without a trailing slash will redirect `/path/` → `/path` with
a 307 that curl silently swallows. Worth documenting once in any future testing notes;
worth recommending `-L` (follow redirects) as a curl-test default.

---

## 5. How to verify Day 10

```powershell
# 1. Clean start
netstat -ano | findstr :8000
# Stop-Process -Id <PID> if anything shows

# 2. Launch
cd frontend; npm run dev
# new terminal:
python -m backend.desktop

# 3. Confirm three startup lines in log (this exact order):
#       audio recorder initialized
#       stt service initialized: model=whisper-large-v3
#       tts service initialised: voice=en_US-lessac-medium.onnx

# 4. Smoke test — should hear two sentences
python -m backend.tests.test_tts_smoke
#    Expected: SHORT and LONG pass; latency ~1s each

# 5. POST /speak — all 6 cases
#    Short
curl -X POST http://localhost:8000/speak -H "Content-Type: application/json" -d "{\"text\":\"Hello.\"}"
#    Medium
curl -X POST http://localhost:8000/speak -H "Content-Type: application/json" -d "{\"text\":\"What is the capital of France? The capital of France is Paris.\"}"
#    Long (buffer test — listen for 'cases' at the end)
curl -X POST http://localhost:8000/speak -H "Content-Type: application/json" -d "{\"text\":\"The ABL1 kinase domain harbours several clinically relevant resistance mutations, of which T315I is the most notorious, conferring approximately forty-fold resistance to first-generation tyrosine kinase inhibitors and necessitating the use of ponatinib in many cases.\"}"
#    Technical vocab
curl -X POST http://localhost:8000/speak -H "Content-Type: application/json" -d "{\"text\":\"ABL1, T315I, RNA-seq, EGFR, ponatinib.\"}"
#    Numerics
curl -X POST http://localhost:8000/speak -H "Content-Type: application/json" -d "{\"text\":\"The dose is 2.5 milligrams per kilogram, administered every 12 hours.\"}"
#    Whitespace — must 422
curl -X POST http://localhost:8000/speak -H "Content-Type: application/json" -d "{\"text\":\"   \"}"

# 6. /chat with TTS fire-and-forget (NOTE: no trailing slash on /chat — 307 eats curl)
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"message\": \"What is the capital of France?\"}"
#    Expected: JSON returns immediately; audio plays ~1s after
#    Log line: chat tts ok: latency=???ms samples=???

# 7. Clean shutdown
#    Click ✕ — confirm no leftover python.exe
```

---

## 6. Open items before Day 11

- [ ] Day 9 carryover — replace `lastEvent` with `useReducer` event queue in
      `useVoiceEvents`. Day 11 will add `speaking_started` / `speaking_ended` /
      `speaking_failed` next to `transcription_complete`, which is exactly the
      back-to-back pattern that bit Day 9.
- [ ] Day 8 carryover — 30s max-duration auto-stop silently loses the buffer.
- [ ] Day 8 carryover — `POST /audio/device` still does not validate the new device opens
      cleanly.
- [ ] Day 10 — `_speak_safely` background tasks have no explicit cancellation in lifespan
      shutdown. Track only if shutdown-during-speak ever surfaces a crash.
- [ ] Day 10 — `tts_sample_rate` is hardcoded; read from `.onnx.json` sidecar on init
      when voice swapping becomes a feature.

---

## 7. Files changed this day

```
NEW:
  backend/voice/tts.py
  backend/tests/test_tts_smoke.py
  docs/plans/day_10_plan.md
  piper/                              (binary tree, gitignored)
  piper_voices/                       (.onnx + .onnx.json, gitignored)

EDIT:
  .gitignore                          (consolidated piper/, piper_voices/,
                                       wake_word_models/ as full directory excludes)
  backend/config/settings.py          (+5 TTS fields under new # TTS section)
  backend/main.py                     (import TTSService; lifespan: tts_service
                                       init/close; ready gate on all three subsystems;
                                       shutdown LIFO order tts → stt → recorder)
  backend/api/voice.py                (import Request, JSONResponse, SpeakRequest,
                                       SpeakResponse, TTSError; POST /speak route)
  backend/api/chat.py                 (import asyncio, Request, TTSError, TTSService;
                                       _speak_safely helper; chat() accepts Request;
                                       create_task(_speak_safely(...)) before return)
  backend/models/voice.py             (import Field, field_validator; SpeakRequest with
                                       length + whitespace validation; SpeakResponse)
  docs/journal.md                     (+Day 10 line)
```

---

## 8. Commit

```
822df0e feat: piper tts integration

- Add TTSService in backend/voice/tts.py: asyncio subprocess, --output_raw
  PCM via sounddevice run_in_executor, single TTSError for sanitised UI messages,
  latency logged on both success and failure paths
- Extend Settings with 5 TTS fields: piper_binary_path / piper_voice_path /
  tts_sample_rate / tts_output_device / tts_timeout_seconds
- Wire tts_service into lifespan; gate app.state.ready on recorder + stt + tts;
  shutdown order LIFO: tts -> stt -> recorder
- New endpoint POST /speak (synchronous debug): Pydantic-validated input
  (1-2000 chars, whitespace-only rejected), returns latency + sample count,
  503 on TTSError
- /chat fires asyncio.create_task(_speak_safely(...)) after returning;
  UI shows text immediately, audio plays out-of-band
- Gotcha: sd.play() dtype= kwarg conflicts when array is already np.int16 -- removed
- Manual verified: short / medium / 30-word buffer test / technical vocab /
  numeric / whitespace-422. End-to-end latency ~1s synth on typical reply.
```
