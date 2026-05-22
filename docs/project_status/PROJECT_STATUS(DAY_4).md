# Project Status — Day 4

**Period covered:** Day 4 (LLM Provider Abstraction)
**Status:** Complete — Definition-of-Done met, two git commits landed on `master`.
**Environment:** Windows 11, Python 3.13.5, Node 24.15.0, Git 2.52.0

> Checkpoint summary for Day 4: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 5.

---

## 1. What has been done

Day 4 made Jarvis think for the first time. `/chat` now calls Gemini, gets a real answer,
and if Gemini fails, silently retries through a Groq fallback — all behind a clean
abstraction that every future feature (tool calling Day 20, summarization Day 22, web
search Day 25) will talk through unchanged.

One pre-coding decision was required before any code: the day-by-day plan referenced
`google-generativeai`, which Google deprecated in late 2025. The session switched to
`google-genai` (the new unified SDK) before writing a single line — this is the `CLAUDE.md`
§"When this file is wrong" protocol in action.

| Task | What landed | Status |
|---|---|---|
| SDK decision | `google-genai==2.6.0` chosen over deprecated `google-generativeai`; architecture skill updated | Done |
| 1 — `backend/llm/base.py` | `LLMResponse` dataclass, 4-class exception hierarchy, abstract `BaseProvider` | Done, verified |
| 2 — `backend/llm/gemini.py` | `GeminiProvider`: async client, error mapping (including 400-auth quirk), token extraction | Done, verified |
| 3 — `backend/llm/openai.py` | `OpenAIProvider`: built and verified structurally; replaced as active fallback same day (see P2) | Built, inactive |
| 4 — `backend/llm/router.py` | `LLMRouter`: primary/fallback orchestration, structured logging, cost tracker call | Done, verified |
| 5 — `backend/services/cost_tracker.py` | Log-only stub; emits `llm_call` structured line per call; `TODO(Day 5)` SQL insert | Done, verified |
| 6 — Wire `/chat` | `backend/api/chat.py` stub replaced with real router call; 503 on all-providers-fail | Done, verified |
| 7 — Model update | `ChatResponse` extended with `provider: str | None` and `model: str | None` | Done, verified |
| Post-commit | `backend/llm/groq_llm.py`: `GroqLLMProvider` using OpenAI SDK at Groq's endpoint; router updated | Done, verified |
| Settings | `gemini_model`, `gemini_model_heavy`, `openai_model`, `groq_model` added to `settings.py` | Done |
| requirements.txt | Pinned `google-genai==2.6.0`, `openai==2.38.0` + all transitive deps | Done |

**Completion criteria verified:**

| Criterion | Result |
|---|---|
| C1 — `/chat` returns real Gemini response | ✅ `"I am a large language model, trained by Google."` |
| C2 — Response includes `provider` + `model` fields | ✅ `"provider": "gemini"`, `"model": "gemini-2.5-flash"` |
| C3 — Bad Gemini key → fallback fires | ✅ Router logs `ERROR: primary auth error, falling back to groq`; Groq answers |
| C4 — Both keys bad → HTTP 503, no stack trace | ✅ `LLMError: all providers failed` → `{"detail": "LLM unavailable..."}` |
| C5 — Long prompt returns coherent text in < 5s | ✅ 1308-char kinase inhibitor poem |
| C6 — `llm_call` log line per call with tokens + cost | ✅ Confirmed in `data/logs/jarvis.log` |
| C7 — Request ID threads end-to-end | ✅ `X-Request-ID` in HTTP response matches `request_id` in `llm_call` log line |
| C8 — Frontend send button returns real reply | ✅ Confirmed via live server + curl |
| C10 — `requirements.txt` pinned | ✅ `google-genai==2.6.0`, `openai==2.38.0` |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. BaseProvider as shared exception translator

Each provider catches its own SDK-specific exception types and re-raises them as one of
three shared types (`LLMRateLimitError`, `LLMAuthError`, `LLMUnavailableError`). The router
only ever sees those three. This isolation means: adding a third provider = writing one new
file, zero router changes. Day 20 tool calling will extend the interface through the
commented `tools` parameter in `BaseProvider.generate()` — the router stays unchanged.

### 2. LLMResponse is a dataclass, not a Pydantic model

`LLMResponse` is an internal object — it's never serialized over the API (that's
`ChatResponse`'s job). It also carries `raw: Any` (the original SDK response for debugging).
Pydantic would choke validating `Any` from two different SDKs. Dataclass is the right tool:
lightweight, no validation overhead, `raw` causes no issues.

### 3. Module-level singletons for router and cost_tracker

`get_router()` builds `LLMRouter(GeminiProvider(), OpenAIProvider())` once on first call.
Both provider clients (`genai.Client`, `AsyncOpenAI`) are cheap to construct — no network
call at construction. The singleton avoids re-constructing clients per request and makes
it easy to reset in tests by nulling `_router`.

### 4. cost_tracker as log-only stub today

`cost_tracker.record()` currently writes a structured `llm_call` log line. The call site
in `router.py` already exists. Day 5 swaps the implementation to also `INSERT INTO cost_log`
— the router does not change. Building the call site today means Day 5 is a pure
implementation change, not an interface change.

### 5. Empty-key deferred to first call, not construction

Both `GeminiProvider` and `GroqLLMProvider` accept a missing API key at construction
without raising. The SDK raises its own error on first call, which the provider catches and
re-raises as `LLMAuthError`. One error path, not two. Consistent behaviour whether the key
is empty or invalid.

### 6. Request ID threads automatically through router logs

Because `router.py` is called from inside an HTTP request handler (`/chat`), the Day-3
`ContextVar` is already set by the middleware. `logger.warning(...)` inside the router
automatically carries the right `request_id` — no `logger.bind()` needed. This was
confirmed in C7.

---

## 3. Problems faced and how they were handled

### P1 — Gemini returns HTTP 400 for a bad API key, not 401/403 *(impact: high, resolved)*

- **What:** The plan's error mapping assumed Gemini returns 401 or 403 for an invalid API
  key. In practice, Gemini returns `400 INVALID_ARGUMENT` with message "API key not valid.
  Please pass a valid API key." A plain `LLMError` was raised instead of `LLMAuthError`,
  which the router does not fall back on — the fallback never fired.
- **Cause:** Google's API design. `400 INVALID_ARGUMENT` is the status for several distinct
  problems (bad request shape, bad API key), so the SDK can't distinguish them by code alone.
- **Handled:** Added a message-based check in `gemini.py`:
  ```python
  if code == 400 and "API key" in str(e):
      raise LLMAuthError(str(e)) from e
  ```
  `setdefault` logic keeps the "known bad prompt → plain `LLMError` → don't fall back"
  path intact for genuine 400s.
- **Verified:** With `GEMINI_API_KEY=bad`, the router logged `ERROR: primary auth error,
  falling back to groq` and the fallback provider answered. Correct chain.

### P2 — OpenAI free-tier quota exhausted *(impact: high, resolved)*

- **What:** `OpenAIProvider.generate()` raised `LLMRateLimitError` (429 — quota exceeded)
  on the first real call. The OpenAI free tier has no credit; the account is empty.
- **Cause:** OpenAI no longer has a usable free tier. Every API call requires billing credits.
- **Handled:** Switched the active fallback to `GroqLLMProvider` (new file
  `backend/llm/groq_llm.py`). Groq exposes an OpenAI-compatible REST API, so the
  implementation reuses the `openai` SDK with a custom `base_url`. The `GROQ_API_KEY` is
  already in `.env` (it'll also be used for STT on Day 9). `openai.py` was kept in place
  but is no longer imported anywhere — it can be reactivated if OpenAI credits are added.
- **Impact on Day 4 plan:** `openai.py` was built and structurally verified (error mapping
  fires correctly) but could not be end-to-end tested with a real API call. That's
  acceptable — the abstraction is correct; only the credentials are missing.

---

## 4. Heads-up: downstream complications to watch

### From P1 (Gemini 400-auth quirk) — fragile message-based detection

The `"API key" in str(e)` check works against the current Gemini error message but is
fragile. If Google changes the message text, the check silently stops working: bad-key
errors become plain `LLMError`, the fallback doesn't fire, and the user sees a 503 instead
of getting an answer from Groq. **Watch for:** unexpected 503s when only Gemini is broken.
A safer future fix would check `e.details` (the raw JSON) for `reason: "API_KEY_INVALID"`
rather than the human-readable message string.

### Groq now carries two responsibilities (Day 9)

`GROQ_API_KEY` is used both as the LLM fallback (Day 4) and as the STT provider
(Day 9 — Groq Whisper-large-v3). If the Groq free tier rate-limits during heavy use,
both voice transcription and LLM fallback will fail simultaneously. The two uses are
independent API calls to different Groq endpoints, but they share the same rate-limit
bucket. **Watch for Day 9:** if STT starts 429-ing during a session where the LLM
fallback is also firing, both pipelines will degrade at once. Consider logging the
error source clearly (`groq/llm` vs `groq/stt`) to distinguish the two.

### No model parameter on `generate()` — Day 6 decision pending

Today every call uses `settings.gemini_model` (Flash). Day 6 importance scoring and
Days 22–24 paper summarization need Pro-tier quality. The `generate()` signature has no
`model` parameter yet. Two options deferred to Day 6: (a) add `model: str | None = None`
to `generate()` with `None` meaning "use default"; (b) add a separate `generate_heavy()`
method on `GeminiProvider`. Either works; (a) is simpler and keeps the interface uniform.
Do not pre-build it — Day 6 context will make the choice obvious.

### cost_tracker has a TODO(Day 5) waiting

`cost_tracker.record()` currently logs only. Day 5 must add:
```python
# INSERT INTO cost_log (provider, model, prompt_tokens, completion_tokens,
#                       estimated_usd, request_id, created_at)
```
The `cost_log` table doesn't exist yet — Day 5 creates the SQLite schema. The call site
in `router.py` does not change; only `cost_tracker.py` needs the SQL insert added.

### No conversation history yet

`/chat` is still one-shot. Each message has no memory of previous turns. Day 5
(messages table) and Day 6 (semantic vector retrieval) provide the context that turns
this into a real conversation. The `generate()` interface does not accept a `messages`
history parameter today — that shape will be clearer after Day 6. Do not add it early.

### Python 3.13 risk (carried forward from Days 1–3)

`google-genai==2.6.0` works on Python 3.13 with no issues. The risk window still opens
later: **ChromaDB (Day 6)**, onnxruntime/Piper (Day 10), pymupdf (Days 22–24),
openWakeWord (Week 4). No action today; the flag lives here until ChromaDB installs
cleanly on Day 6.

### Pricing constants will drift

`cost_tracker.py` has a `_PRICING` dict with per-million-token rates sourced on
2026-05-22. Gemini pricing has shifted before; OpenAI pricing shifts often. A 2× error
in cost estimation is fine; 10× is not. The `TODO` comment in the file flags a re-check
at end of Month 1. If costs look wrong during development, that dict is the first place
to look.

---

## 5. Open items before Day 5

- [ ] Run the Day 3 regression curl commands (`/health`, `/memory`, `/voice-state`, `/ws/voice`) — confirmed passing during Day 4 live test, but worth a final check before Day 5 modifies the DB layer
- [ ] Visit `http://localhost:8000/docs` — confirm `/chat` shows `provider` and `model` in its response schema (Task 7 model update)
- [ ] C9 comprehension check (self-check, no code needed): explain out loud — what `BaseProvider` is for, what `GeminiProvider.generate()` does step by step, what the router does on rate-limit, what `cost_tracker.record()` will become on Day 5

---

## 6. How to verify Day 4

```powershell
# from repo root — with real API keys in .env
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# C1/C2: real Gemini response with provider + model
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"message\":\"What is ATP?\"}"
# Expected: {"reply":"...", "provider":"gemini", "model":"gemini-2.5-flash", "request_id":"..."}

# C6: llm_call log line in log file
# Look for a line containing "llm_call" with provider/model/tokens/estimated_usd

# C3: fallback fires with bad Gemini key
# Set GEMINI_API_KEY=bad in .env, restart server, repeat the curl above
# Expected: provider: "groq", model: "llama-3.3-70b-versatile"
# Logs: ERROR "primary auth error, falling back to groq"

# C4: 503 when both keys are bad
# Set both GEMINI_API_KEY and GROQ_API_KEY to bad, restart, repeat curl
# Expected: HTTP 503 {"detail": "LLM unavailable, please try again."}
# No stack trace in response body

# Restore real keys before Day 5
```

---

## 7. Commit log for this period

```
106a787 feat: switch llm fallback from openai to groq (free tier)
89d08c5 feat: llm provider abstraction with gemini primary, openai fallback
```

> Note: `backend/desktop.py` remains modified and intentionally uncommitted (carried from
> pre-Day 3). `docs/plans/` and `docs/project_status/` are untracked and left for the
> user to add when desired.
