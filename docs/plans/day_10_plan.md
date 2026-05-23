# Day 10 Plan — Text-to-Speech via Piper

**Period covered:** Day 10 (TTS pipeline — Piper subprocess + sounddevice playback)
**Prerequisites:** Day 9 complete (Groq STT working end-to-end; `app.state.stt_service` healthy in lifespan).
**Environment:** Windows 11, Python 3.13.5, sounddevice 0.5.5 (already installed). Piper binary + voice model to be downloaded today.
**Time budget:** ~5 hours.

> **One-line goal for the day:** the assistant speaks responses with a natural voice. Typing a chat message in the UI produces a spoken reply within ~2 seconds of the LLM returning.

---

## 0. Today's agenda

End-to-end picture of what Day 10 adds to the system:

```
[user types message in chat] ──HTTP POST──▶ /chat
                                              │
                                              ▼
                                       LLM router (Day 4)
                                              │
                                              ▼ response text
                                       save to SQLite (Day 5)
                                              │
                                              ▼
            ┌─────────────────────────────────┴────────────────────┐
            │                                                      │
   HTTP returns response                              tts_service.speak(text)   ◀── NEW
   (UI shows text immediately)                                     │
                                                                   ▼
                                                    Piper subprocess → raw PCM
                                                                   │
                                                                   ▼
                                                    sounddevice.play(np.int16, 22050)
                                                                   │
                                                                   ▼
                                                            (audible reply)
```

Day 10 explicitly does **not** wire TTS into the PTT → STT → LLM voice loop. That is Day 11's job — it will rebuild the loop around a proper state machine in `services/conversation.py`. Day 10 only proves the TTS subsystem works in isolation (via `POST /speak` debug endpoint) and via the typed-chat path (which already exists since Day 4).

**Boundary check:** if you find yourself today writing anything that looks like a state machine, an `idle → listening → thinking → speaking → idle` flow, or anything touching `recording_saved` in the dispatcher — stop. That belongs in Day 11.

---

## 1. Pre-flight (5 min)

Before any Piper work, verify Day 9 hasn't regressed:

- [ ] Backend boots cleanly: `python -m backend.desktop` shows both `audio recorder initialized` and `stt service initialized: model=whisper-large-v3` lines, in that order.
- [ ] Frontend builds: `cd frontend; npm run dev` succeeds, PyWebView window opens.
- [ ] PTT happy path still works: hold Alt+Space, say a short sentence, see transcript bubble appear.
- [ ] No leftover `python.exe` after closing the window (Day 9 verification step 8).

If any of these fail, fix before starting Day 10 — Day 10 layers on top of Day 9's lifespan, dispatcher, and WebSocket wiring.

---

## 2. Tasks

### Task 10.1 — Acquire Piper binary and voice model (30 min)

**Goal:** `piper/piper.exe` and `piper_voices/en_US-lessac-medium.onnx` (+ companion `.json`) exist locally and respond to a smoke test from PowerShell.

**Steps:**

1. Download the latest **Piper Windows x64** release zip from `github.com/rhasspy/piper/releases`. As of mid-2026 the asset is named something like `piper_windows_amd64.zip` — pick the most recent stable release that is not a prerelease.
2. Extract into `piper/` at the repo root. After extraction, `piper/piper.exe` should be a directly executable path.
3. Download the voice model files from `huggingface.co/rhasspy/piper-voices` → `en/en_US/lessac/medium/`. You need **both** files:
   - `en_US-lessac-medium.onnx` (the model, ~60 MB)
   - `en_US-lessac-medium.onnx.json` (the metadata sidecar — Piper will fail without it)
   Place both in `piper_voices/`.
4. Update `.gitignore` to exclude the binary directory and voice models. They are too large for git and will be re-fetched by Day 29's setup script anyway. Add:
   ```
   piper/
   piper_voices/
   ```
5. **Smoke test from PowerShell** (do this before writing a single line of Python — confirms the binary works at all):
   ```powershell
   echo "This is a test of the Piper text to speech engine." | `
       .\piper\piper.exe --model .\piper_voices\en_US-lessac-medium.onnx `
                          --output_file test.wav
   ```
   Then open `test.wav` in any media player and verify it plays a clean utterance. Delete it after confirming.

**Why this comes first:** if the binary doesn't work standalone, no amount of Python wrapping will fix it. Establish ground truth before building on top.

**Verify by checking:**
- [ ] `piper\piper.exe --help` runs without "is not recognized as a command" or DLL-missing errors.
- [ ] The smoke-test WAV plays back as recognisable, intelligible English.
- [ ] `.gitignore` excludes both `piper/` and `piper_voices/`.

---

### Task 10.2 — Confirm Piper's exact CLI flags before writing Python (10 min)

**This is a CLAUDE.md rule 4 moment.** Piper's flag names have drifted across versions — `--output-raw` vs `--output_raw`, `--output-file` vs `--output_file`, etc. Before writing `TTSService`, capture the truth from the binary you just downloaded:

```powershell
.\piper\piper.exe --help > piper_help.txt
notepad piper_help.txt
```

Copy the lines for **raw PCM output to stdout** and **model selection** into a scratchpad. You will pass those exact flag names to Claude Code in Task 10.3. If you skip this step, Claude will guess from training data and may give you flags that worked in an older release.

**You're looking for two things:**
- The flag that makes Piper write raw PCM (no WAV header) to stdout. Likely `--output-raw` or `--output_raw`. This is the path the TTSService will use.
- The output sample rate for `en_US-lessac-medium`. Should be 22050 Hz mono int16, but the `.onnx.json` sidecar is the source of truth — open it in a text editor and look for `sample_rate`. Record what you find.

**Verify by checking:**
- [ ] You have a confirmed flag name for raw PCM output (write it down).
- [ ] You have the confirmed sample rate from the voice's `.onnx.json` (write it down).

---

### Task 10.3 — Decision point: streaming vs all-at-once playback (10 min, no code)

Before asking Claude to write `TTSService`, decide the playback strategy. There are two real options:

**Option A — All-at-once (recommended for Day 10):**
- Spawn Piper, wait for the full subprocess to finish, collect all stdout into one `bytes` buffer.
- Convert to a single `numpy.int16` array.
- Call `sounddevice.play(array, sample_rate)` and `sounddevice.wait()`.
- **Pros:** ~30 lines of code. No streaming buffer logic. Trivially debuggable.
- **Cons:** for a 50-word reply, perceived latency is `synth_time + ~50ms`. Piper synth on CPU is roughly real-time, so a 10-second utterance takes ~10 seconds to start.
- **Reality check for medium voices on i3:** Piper medium is faster-than-realtime on most CPUs. A 30-word sentence (~10s of audio) synthesises in ~2–4s. This still meets the "<2s after LLM" criterion for typical-length replies (15–25 words).

**Option B — Streaming (defer to Week 3 polish or Day 13 buffer):**
- Read Piper's stdout chunk-by-chunk (e.g. 4096-sample blocks).
- Feed each chunk into a `sounddevice.OutputStream` as it arrives.
- **Pros:** time-to-first-audio is ~200ms regardless of utterance length.
- **Cons:** ~120 lines, ring-buffer logic, asyncio queue between subprocess reader and stream callback, careful cleanup on errors. Real engineering. Easy to get subtly wrong.

**Recommended for Day 10:** Option A. The completion criterion is "within 2 seconds of LLM finishing" — A meets it for normal replies. Streaming is a real upgrade but it is exactly the kind of thing that eats Day 10, Day 11, *and* Day 12 if started today. Park it as a Day 13 buffer-day candidate or a Week 3 polish item.

**Write down which option you chose** — you will tell Claude Code explicitly in the next task.

---

### Task 10.4 — Add TTS settings (15 min)

**Goal:** Piper paths and audio config live in `backend/config/settings.py`, not as magic literals scattered through `tts.py`.

Add a new `# TTS` section to the `Settings` class (parallel to Day 9's `# STT` section). Suggested fields — exact names and types to confirm with Claude when implementing, but the *what* is:

- `piper_binary_path: Path` — defaults to `Path("piper/piper.exe")`. Use `pathlib.Path` not `str` so path operations are sane on Windows.
- `piper_voice_path: Path` — defaults to `Path("piper_voices/en_US-lessac-medium.onnx")`.
- `tts_sample_rate: int` — defaults to **whatever you read from the voice's `.onnx.json` in Task 10.2**. For `lessac-medium` this is 22050. Putting it in settings means swapping voices later won't require a code change.
- `tts_output_device: int | None` — defaults to `None` (system default device). Surface in UI on Day 17.
- `tts_timeout_seconds: int` — defaults to 30. Maximum subprocess wait. Long replies can synth slowly; this is a sanity ceiling.

**Verify by checking:**
- [ ] Backend still boots after the settings edit — Pydantic doesn't complain about defaults or types.
- [ ] No magic numbers (22050, 30, etc.) hard-coded anywhere in the future `tts.py`.

---

### Task 10.5 — Build `backend/voice/tts.py` (90 min)

**Goal:** a self-contained `TTSService` that converts text to audible output. Mirrors the shape of `STTService` from Day 9 — same exception pattern, same latency-logging discipline, same project conventions.

**Before asking Claude to write the file, write the docstrings/signatures yourself** (per CLAUDE.md rule "write the docstring/signature yourself before asking Claude Code to implement"). Sketch:

```python
class TTSError(Exception):
    """UI-safe TTS exception. The message is shown verbatim to the user."""


class SynthesisResult(BaseModel):
    """Result of a single Piper synthesis call. pcm_bytes is excluded from logs."""
    pcm_bytes: bytes
    sample_rate: int
    num_samples: int
    latency_ms: int


class TTSService:
    """Wraps Piper subprocess + sounddevice playback.

    Lifecycle: construct once in lifespan, call speak() per utterance, close() on shutdown.
    """

    def __init__(self, settings: Settings) -> None: ...

    async def synthesize(self, text: str) -> SynthesisResult:
        """Run Piper as subprocess; return raw PCM bytes + metadata.

        Raises TTSError on subprocess failure, timeout, or empty output.
        """

    async def speak(self, text: str) -> SynthesisResult:
        """synthesize() + play through sounddevice. Returns the result for logging."""

    async def close(self) -> None:
        """Currently a no-op — Piper has no persistent process. Reserved for symmetry
        with STTService.close() and for future caching."""
```

Now hand this to Claude Code with these explicit constraints (paste them in the prompt so the rules are visible):

1. **Use `asyncio.create_subprocess_exec`**, not the blocking `subprocess.run`. The whole FastAPI loop must stay free.
2. **Use the raw-PCM flag from Task 10.2** (whichever exact spelling `piper --help` showed). Do not assume `--output-raw` if `--output_raw` is what the binary expects.
3. **Pipe text via stdin**, read all of stdout, capture stderr separately for error reporting.
4. **Apply a timeout** using `asyncio.wait_for(proc.communicate(...), timeout=settings.tts_timeout_seconds)`. On timeout, kill the subprocess and raise `TTSError("TTS took too long.")`.
5. **Convert stdout bytes to int16 numpy array** via `np.frombuffer(data, dtype=np.int16)`. Empty array → `TTSError("TTS produced no audio.")`.
6. **Play via sounddevice** with `await asyncio.get_running_loop().run_in_executor(None, _play_sync, array, sr)`. PortAudio's `play()`/`wait()` are blocking C calls — same pattern Day 8 used for the recorder. Do not call `sd.play()` directly inside an async function.
7. **Latency logging on both success and failure** (same discipline as Day 9 §2.6). Bind a short request ID via `logger.bind(req_id=...)` so a single utterance's lines group together in the log.
8. **Log a short text preview** in the log line (first ~60 chars + length), never the full text. Long log lines hurt grep.

**A note on threading and sounddevice:** `sounddevice.play()` is non-blocking by default — it returns immediately and audio plays on a background PortAudio thread. The synchronous helper should call `sd.play(array, sr); sd.wait()` so the caller can await actual completion. Tell Claude this explicitly; otherwise you'll get "the function returns before audio finishes."

**Verify by writing a smoke test** at `backend/tests/test_tts_smoke.py` (mirrors `test_stt_smoke.py` from Day 9):

```python
# Run with: python -m backend.tests.test_tts_smoke
# Should print a result, play "Day ten TTS smoke test." through speakers, exit clean.
```

Run it. If it prints a result and you hear the sentence, the service works.

**Verify by checking:**
- [ ] `python -m backend.tests.test_tts_smoke` plays "Day ten TTS smoke test." through default speakers.
- [ ] A 30+ word sentence (try: "This is a longer sentence designed to exercise the buffer with multiple clauses, technical terms like ABL1 kinase, and enough words to expose any chunked playback bug that a short test would hide.") plays cleanly with no stutter, clipping, or cut-off ending.
- [ ] Killing the subprocess mid-synth (e.g. point the binary path to a non-existent file) surfaces a clean `TTSError`, not a raw `FileNotFoundError`.
- [ ] Subprocess timeout fires correctly if you set `tts_timeout_seconds` to `1` and run a long utterance — kills the process and raises `TTSError`.

---

### Task 10.6 — Wire `TTSService` into the FastAPI lifespan (15 min)

**Goal:** `app.state.tts_service` is available after startup, gated by `app.state.ready`, and closed on shutdown.

Edits to `backend/main.py`, mirroring how Day 9 added `stt_service`:

1. Import `TTSService` and `TTSError` from `backend.voice.tts`.
2. In the lifespan startup block, **after** `stt_service` initialisation:
   ```python
   tts_service = TTSService(settings)
   app.state.tts_service = tts_service
   logger.info("tts service initialised: voice={voice}", voice=settings.piper_voice_path.name)
   ```
3. Update `app.state.ready` gating: it must now require recorder **and** stt **and** tts.
4. In the lifespan shutdown block, call `await app.state.tts_service.close()` — order vs stt close doesn't matter (both are stateless today), but for predictability close in reverse-construction order: tts → stt → recorder.

**Verify by checking:**
- [ ] Backend boot log shows three lines in order: `audio recorder initialized`, `stt service initialized`, `tts service initialised`.
- [ ] Hitting any endpoint before all three init lines have appeared returns a 503 from the existing `ready`-gate (no change to gate logic, just the additional condition).
- [ ] Clean shutdown: closing the window logs reverse-order close lines and exits with no orphaned `python.exe`.

---

### Task 10.7 — Add `POST /speak` debug endpoint (30 min)

**Goal:** a curl-able endpoint that bypasses LLM and chat and just exercises TTS. Critical for triaging "is TTS broken, or is the chat wiring broken?" later.

**Where it lives:** `backend/api/voice.py` already exists from Day 3 (per Version 1 plan). Add the endpoint there. If it doesn't exist, create it and register the router in `main.py`.

**Suggested shape — surface this to Claude with the trade-off:**

> **Option A: synchronous (await TTS, return when done)**
>
> `POST /speak { "text": "hello" }` → blocks until audio playback ends → `{ "latency_ms": 1230, "num_samples": 27040 }`
>
> Easier to test from curl, easier to interpret.
>
> **Option B: fire-and-forget**
>
> Returns `202 Accepted` immediately, TTS runs in a background task.
>
> Better for real chat (don't block the HTTP request on speaker output), but harder to test.

For a **debug endpoint**, Option A is correct — you want curl to wait so you can see the result and know whether it worked. The fire-and-forget pattern is what `POST /chat` will use in Task 10.8.

**Pydantic models** in `backend/models/voice.py`:
- `SpeakRequest { text: str }` with `min_length=1` and `max_length=2000` validation.
- `SpeakResponse { latency_ms: int, num_samples: int }` — never return the raw PCM bytes over HTTP.

**Error handling:** catch `TTSError` in the route, return `503` with `{ "error": str(e) }`. Any other exception → log and let FastAPI's default handler return 500.

**Verify by checking:**
- [ ] `curl -X POST http://localhost:8000/speak -H "Content-Type: application/json" -d "{\"text\":\"Hello from Day ten.\"}"` plays audio and returns `{"latency_ms": ..., "num_samples": ...}`.
- [ ] Empty text → 422 from Pydantic validation.
- [ ] 2001-character text → 422 from Pydantic validation.
- [ ] Point `piper_binary_path` to a non-existent file in `.env`, restart, retry the curl → 503 with the `TTSError` message; no traceback in the HTTP response.

---

### Task 10.8 — Wire LLM response → TTS in `/chat` (45 min)

**Goal:** typing a message in the chat panel produces a spoken reply. This is the completion criterion.

**The fire-and-forget pattern:**

In the `/chat` route, after the LLM response has been built and the message has been saved to SQLite, schedule TTS as a background task and return the HTTP response immediately:

```python
# After building `response_text` and saving to memory:
asyncio.create_task(_speak_safely(request.app.state.tts_service, response_text))
return ChatResponse(text=response_text, ...)
```

Where `_speak_safely` is a small helper that wraps `tts_service.speak(text)` in `try/except TTSError` and logs (without re-raising — there's no HTTP response to attach the error to). Best-effort.

**Why fire-and-forget rather than `await`:** the chat panel shows the text immediately (better UX), and audio plays alongside. If you `await` the speak call, the HTTP request blocks for the full synth+playback duration, the UI bubble appears delayed, and the connection holds a slot open. Bad.

**Why no WebSocket broadcast for TTS state today:** Day 11 adds the `speaking` state and the proper `tts_started` / `tts_ended` events when it builds the conversation state machine. Adding them now in `/chat` means writing them twice — once for chat, once for the voice loop — and they'd need to be unified on Day 11 anyway. Resist.

**Verify by checking:**
- [ ] Type "What's the capital of France?" in the chat input, hit send → text "Paris" (or similar) appears in the chat panel within ~1s, audio plays within ~2s of that.
- [ ] If TTS fails mid-playback (e.g. unplug speakers — unusual, but try anyway), the log shows the `TTSError` but the chat panel is unaffected.
- [ ] Multiple rapid `/chat` calls: the second one's TTS may overlap or queue the first. **This is expected for Day 10** — Day 11's state machine will serialise via the `speaking` state. Do not try to fix overlap today.

---

### Task 10.9 — Long-utterance and edge-case tests (30 min)

The Day_by_Day_Plan_v2 explicitly warns: *"Test on a long sentence (30+ words) — short tests hide buffering bugs."* Take this seriously.

Run through this small checklist via `POST /speak`:

1. **Short:** `"Hello."` — should be ~200ms latency and crisp.
2. **Medium:** `"What is the capital of France? The capital of France is Paris."` — natural pacing, no stutter between sentences.
3. **Long (the buffer test):** 30+ words. Try this exact sentence so you have a reproducible probe:
   > "The ABL1 kinase domain harbours several clinically relevant resistance mutations, of which T315I is the most notorious, conferring approximately forty-fold resistance to first-generation tyrosine kinase inhibitors and necessitating the use of ponatinib in many cases."
   - Should play continuously, no cut-off, no audible gap mid-sentence, no truncation at the end.
4. **Technical vocab:** `"ABL1, T315I, RNA-seq, EGFR, ponatinib."` — Piper will pronounce these phonetically, not perfectly, but it should produce *something* recognisable rather than silently swallow words.
5. **Numbers and punctuation:** `"The dose is 2.5 milligrams per kilogram, administered every 12 hours."` — verify the numerals are spoken, not skipped.
6. **Empty after stripping** (the validation test from Task 10.7): `"   "` → 422.

**If long-utterance has a cut-off ending,** the most likely cause is the subprocess's stdout buffer being closed before all PCM is read. The fix is usually replacing `proc.stdout.read()` with `(await proc.communicate(text.encode()))[0]` which awaits process end before returning stdout. Surface this to Claude as a debugging step if it happens — do not pre-emptively redesign.

**Verify by checking:**
- [ ] All 6 cases pass.
- [ ] Particular attention: long sentence ends cleanly with the final word "cases" audible.

---

### Task 10.10 — Journal + commit (10 min)

1. **Update `docs/journal.md`** with one line for Day 10. Convention from Day 9: terse, factual, links to commit hash optional.
   ```
   Day 10: Piper TTS wired through chat. /speak debug endpoint live. ~1s latency on 30-word reply.
   ```

2. **Commit.** Suggested message (mirrors Day 9's commit shape):

   ```
   feat: piper tts integration

   - Add TTSService in backend/voice/tts.py: asyncio subprocess, raw PCM via
     sounddevice, single TTSError type for sanitised UI messages
   - Extend Settings with piper_binary_path / piper_voice_path / tts_sample_rate /
     tts_output_device / tts_timeout_seconds (TTS section)
   - Wire tts_service into lifespan; gate app.state.ready on recorder + stt + tts
   - New endpoint POST /speak (synchronous, debug): exercises TTS in isolation
     with Pydantic-validated input (1-2000 chars), returns latency + sample count
   - /chat now fires asyncio.create_task(speak(response_text)) after returning;
     UI shows text immediately, audio plays out-of-band
   - Manual verified: short / medium / 30+ word / technical-vocab / numeric tests
     pass cleanly. End-to-end latency on typical reply ~1.0-1.5s after LLM returns.
   ```

---

## 3. Completion criteria (the gate to Day 11)

All must be true before starting Day 11:

- [ ] Typing a chat message produces a spoken response within ~2 seconds of the LLM returning. (Original v2 criterion.)
- [ ] No clipping, stuttering, or audio glitches on a 30+ word reply.
- [ ] Voice sounds natural enough that you don't cringe. (If lessac-medium does cringe, switch voice in Task 10.4 — try `en_US-amy-medium` or `en_GB-alan-medium`. Two ONNX swaps, no code changes.)
- [ ] `POST /speak` works for short, long, and edge-case inputs; returns 422 for empty/oversize.
- [ ] Bad binary path or bad voice path → clean `TTSError` message via 503; no raw traceback exposed.
- [ ] Backend boots with three init lines (recorder, stt, tts) and shuts down cleanly with no orphan process.
- [ ] You can explain (out loud to yourself) what `synthesize()`, `speak()`, the fire-and-forget pattern in `/chat`, and the executor-wrapping of sounddevice each do, and why.

---

## 4. Heads-up: complications to expect downstream

These are not problems to fix today — they're notes for Day 11+ so future-you isn't surprised.

### Concurrent `speak()` calls overlap or interleave

Day 10's `/chat` fires-and-forgets. Two rapid `/chat` calls produce two TTS tasks racing for the speaker. There is no serialisation. On Day 11, the conversation state machine will gate this — `speaking` state blocks new utterances until the previous one ends. Today, just don't spam `/chat`.

### TTS errors are silent to the UI

If TTS fails after `/chat` returns, the user sees no indication. The error is logged but the chat panel shows the text reply and the user wonders why no audio played. Day 11's state machine will broadcast a `speaking_failed` event over WebSocket, with the same red-toast pattern Day 9 introduced for STT failures. For Day 10 this is an accepted limitation.

### Day 9's `lastEvent` fragility may bite again

Day 9 §4 flagged that two WebSocket events arriving back-to-back can be batched by React 18 and silently lose one. Day 10 deliberately broadcasts **no new TTS-related WebSocket events** from the dispatcher, which means today's path is safe. Day 11 will introduce `tts_started` / `tts_ended` / `speaking_failed`, which can arrive in rapid succession with the existing `transcription_complete`. **Recommendation:** when Day 11 starts and you're about to add those events, also do the `useReducer`-based event-queue refactor in `useVoiceEvents`. It's an hour of work that prevents an entire class of bug. The Day 9 open items list already flags this.

### Piper's pronunciation of technical vocabulary is imperfect

Lessac-medium will mispronounce "ABL1" as "able one" or "ay-bee-ell-one" inconsistently. This is a Piper limitation, not a bug. There is a **phoneme override** mechanism via SSML-ish input that can be added later if it becomes painful. For Day 10, accept the imperfection — the assistant's job is to convey the answer audibly, not to be perfectly correct on every gene symbol. Note this as a Month 2 polish candidate.

### Sample-rate mismatch between voice and TTS settings will produce chipmunk or slow-mo audio

If you swap voices later (e.g. from `en_US-lessac-medium` at 22050 to `en_US-amy-low` at 16000) without updating `tts_sample_rate` in settings, sounddevice will play the int16 stream at the wrong rate. The fix is correct — read sample rate from `voice_path.with_suffix('.onnx.json')` on init and override the setting. But that's a small refactor. For Day 10, just keep the rate matching the voice manually.

### Day 9 open items remain open

The three carryovers from Day 9 §6 are *not* addressed by Day 10:
- Event-queue refactor in `useVoiceEvents` → still deferred (Day 11 if it bites, else Day 17).
- 30s max-duration auto-stop silently loses the buffer → still Day 12.
- `POST /audio/device` validation → still Day 17.

Day 10 deliberately does not touch any of these. They are tracked.

---

## 5. Files expected to change

```
NEW:
  backend/voice/tts.py
  backend/tests/test_tts_smoke.py
  piper/                              (binary, gitignored)
  piper_voices/                       (.onnx + .onnx.json, gitignored)
  docs/day_10_plan.md                 (this file, if not already there)

EDIT:
  backend/config/settings.py          (+5 TTS fields under new # TTS section)
  backend/main.py                     (import TTSService + TTSError; lifespan: tts_service
                                       init/close; app.state.ready gated on three subsystems)
  backend/api/voice.py                (+ POST /speak route — or create file if absent)
  backend/api/chat.py  (or wherever
   the /chat handler lives)           (after returning, fire asyncio.create_task(speak(...))
                                       wrapped in _speak_safely)
  backend/models/voice.py             (+ SpeakRequest, SpeakResponse)
  .gitignore                          (+piper/, +piper_voices/)
  docs/journal.md                     (+Day 10 line)
```

No frontend changes are required for Day 10. The chat panel and message rendering from Day 9 are sufficient. Day 11 introduces the `speaking` badge state.

---

## 6. If today goes sideways

Per CLAUDE.md "When I'm stuck" — don't immediately rewrite. Order of triage when something doesn't work:

1. **Did Piper itself work in Task 10.1's PowerShell smoke test?** If not, no Python change will help — re-download, re-extract, check antivirus quarantine.
2. **Does `python -m backend.tests.test_tts_smoke` play audio?** If yes but `/speak` fails, the bug is in the FastAPI/route layer, not the service. If no, the bug is in `TTSService`.
3. **Is `app.state.tts_service` actually attached at startup?** Hit `GET /health` after startup; if you've extended health to report subsystem status, check there. Otherwise add a one-line `print` after assignment temporarily (then remove it — `logger.info` is permanent, `print` is temporary).
4. **Are the Piper flags correct?** Re-run `piper --help` (Task 10.2). The flag spelling is the single most common silent failure mode.
5. **Is sounddevice playing through the wrong device?** Run `python -c "import sounddevice as sd; print(sd.query_devices())"` and verify the default output is what you expect.

If stuck for >1 hour on any single task, descope: ship `POST /speak` working in isolation today, defer the `/chat` wiring to start of Day 11. A working `/speak` is a complete Day 10 deliverable on its own — Day 11 needs `TTSService` anyway and would do the chat wire as part of the state machine.
