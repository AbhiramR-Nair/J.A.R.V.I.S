# Day 4 Plan — LLM Provider Abstraction

**Date slot:** Day 4 of 30 (Week 1 — Foundation)
**Predecessor:** Day 3 — FastAPI base, settings, request-ID logging, stub endpoints landed on `master`
**Successor:** Day 5 — SQLite schema (creates the `cost_log` table this day's cost tracker will write to)
**Time budget:** 5 hours (per `Day_by_Day_Plan_v2.md`)
**Git target:** one commit — `feat: llm provider abstraction with gemini primary, openai fallback`

> One-line goal: **`POST /chat` returns a real Gemini answer, and if Gemini is broken, OpenAI silently takes over — through a clean abstraction the rest of the codebase will use for the next 26 days.**

---

## 0. Why this day matters

Day 3 left every endpoint stubbed. `/chat` echoes its input. Day 4 is the first day where Jarvis *thinks*. But more importantly, it's the day the **shape** of LLM access is decided — every future feature (tool calling Day 20, paper summarization Day 22, web search Day 25) talks to LLMs through whatever interface lands today. If the abstraction is right, those features are 1-file additions. If it's wrong, they each become refactors.

The Day-3 status note specifically flagged **"Gemini SDK version drift"** as the next watch-out. That hits today — and turns out to be sharper than expected (see §2 below).

---

## 1. Before you start — a 5-minute review (do not skip)

Per `CLAUDE.md` §6 ("Stay in the read-review loop") and the daily loop in `Day_by_Day_Plan_v2.md`, start the day by re-reading what Day 3 actually shipped. You'll be touching these files today:

| File | What it does now | What Day 4 changes about it |
|---|---|---|
| `backend/api/chat.py` | Returns `(stub) I received: ...` | Wire to the LLM router; remove the stub string |
| `backend/config/settings.py` | Holds `gemini_api_key`, `openai_api_key`, `gemini_model`, `openai_model` (empty defaults tolerated) | **Read-only** — confirm the field names you're about to import. Add new settings here if needed |
| `backend/config/logging.py` | `setdefault('request_id', ...)` patcher (P1 from Day 3) | **Untouched** — but rely on the fact that LLM calls inside `/chat` automatically inherit the HTTP request's UUID via the ContextVar. No manual `logger.bind()` needed in the provider classes |
| `backend/models/chat.py` | `ChatRequest`, `ChatResponse` | May extend `ChatResponse` with `provider` and `model` fields (decision — see §4 Task 5) |
| `backend/main.py` | Mounts routers, request-ID middleware | **Untouched** |

**Concretely:**

```powershell
# from repo root
git log --oneline -5                          # confirm you're on the three Day-3 commits
cat backend/config/settings.py                # confirm the exact key names
cat backend/api/chat.py                       # confirm what you're replacing
cat backend/models/chat.py                    # confirm current response shape
```

If anything is unexpected, stop and figure out why before writing code.

---

## 2. The decision that has to happen first (do not skip — `CLAUDE.md` §2 "suggest, don't just write")

`Day_by_Day_Plan_v2.md` says to use `google-generativeai`. **As of 2026, that package is deprecated.** Google replaced it with the unified `google-genai` SDK (different package name, different client pattern, different imports). The old SDK still works but emits deprecation warnings and will not get new features.

**Two options:**

**Option A — `google-genai` (recommended, new SDK)**
```python
# install
pip install google-genai openai httpx

# usage
from google import genai
from google.genai import types

client = genai.Client(api_key=settings.gemini_api_key)
response = await client.aio.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
    config=types.GenerateContentConfig(
        system_instruction=settings.system_prompt,
        temperature=0.7,
    ),
)
print(response.text)
```
- ✅ Actively maintained, supports Gemini 2.5 Flash/Pro and beyond
- ✅ Has native `aio` (async) namespace — fits the "async all the way down" rule
- ✅ Function calling on Day 20 will use the same client
- ⚠ Plan and skill files reference the old name — they'll need a one-line update

**Option B — `google-generativeai` (what the plan says, deprecated)**
```python
pip install google-generativeai openai httpx
import google.generativeai as genai
genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel("gemini-1.5-flash")
response = await model.generate_content_async(prompt)
```
- ✅ Tutorials and Stack Overflow answers still mostly target this one
- ❌ Deprecated; Google explicitly says migrate to `google-genai`
- ❌ Doing this means re-doing it in a month or two anyway
- ❌ Some newer Gemini features may not land here

**Recommendation: Option A.** It's the same amount of work today, future-proof, and Day 20 (tool calling) will benefit. Per `CLAUDE.md` §"When this file is wrong" — if a rule conflicts with reality, flag it and the current session wins. This is a clean case for that.

**Action before any code:** confirm Option A with the user. If approved, also update one line in `.claude/skills/project-architecture/SKILL.md` (the "Gemini SDK version drift" gotcha) at the end of the day to say "use `google-genai`, not `google-generativeai`".

The rest of this plan assumes Option A. All code references below use `from google import genai`.

---

## 3. Agenda — what gets built today

A clean four-layer addition to the backend:

```
                 ┌──────────────────────────────────────┐
                 │  backend/api/chat.py  (Day 3 stub)   │
                 │  → now calls LLMRouter.generate()    │
                 └──────────────────────────────────────┘
                                  │
                                  ▼
                 ┌──────────────────────────────────────┐
   NEW           │  backend/llm/router.py               │
                 │  - try primary (Gemini)              │
                 │  - on RateLimit/APIError → fallback  │
                 │  - logs which provider answered      │
                 │  - calls cost_tracker.record()       │
                 └──────────────────────────────────────┘
                       │                       │
                       ▼                       ▼
   NEW   ┌──────────────────────┐  ┌────────────────────────┐
         │ llm/gemini.py        │  │ llm/openai.py          │
         │ GeminiProvider       │  │ OpenAIProvider         │
         │ implements           │  │ implements             │
         │ BaseProvider         │  │ BaseProvider           │
         └──────────────────────┘  └────────────────────────┘
                       │                       │
                       └───────────┬───────────┘
                                   ▼
                 ┌──────────────────────────────────────┐
   NEW           │  backend/llm/base.py                 │
                 │  abstract BaseProvider               │
                 │  + LLMResponse dataclass             │
                 │  + custom exceptions                 │
                 └──────────────────────────────────────┘

                 ┌──────────────────────────────────────┐
   NEW           │  backend/services/cost_tracker.py    │
   (stub-       │  - record(provider, model, tokens,   │
    writer)     │    usd, request_id) → log only today │
                 │  - DB write enabled Day 5            │
                 └──────────────────────────────────────┘
```

**Files created today:** 5
**Files modified today:** 1 (`backend/api/chat.py`) + optionally 1 (`backend/models/chat.py`)
**Lines of code expected:** ~250 total across all new files. If it balloons past 400, stop and ask why.

---

## 4. Tasks (in order)

Each task has: write a docstring/signature yourself → ask Claude Code → read every line → run, verify. Per `CLAUDE.md` §1, **every non-trivial code block gets a 3-5 line explanation comment above it.** These comments are not optional — they are for your future re-reading.

---

### Task 1 — `backend/llm/base.py` — the interface (~25 min)

**Why first:** locks the contract before any implementation. If Task 2 finds the interface doesn't fit Gemini, that's a signal to revise the interface here, not paper over it in Gemini's class.

**Write yourself first (signatures + docstrings, no body):**

```python
# backend/llm/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class LLMResponse:
    """Provider-agnostic response shape returned by every BaseProvider.

    Fields:
        text: the model's reply (final, post-streaming if any)
        provider: 'gemini' or 'openai' — which one actually answered
        model: the exact model id used (e.g. 'gemini-2.5-flash')
        prompt_tokens: input tokens (None if provider didn't report)
        completion_tokens: output tokens (None if provider didn't report)
        raw: the original SDK response object (for debugging only)
    """
    text: str
    provider: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: Any

class LLMError(Exception):
    """Base exception for any LLM call failure."""

class LLMRateLimitError(LLMError):
    """Provider hit a rate limit or quota. Router catches this and falls back."""

class LLMAuthError(LLMError):
    """Bad/missing API key. Router catches and falls back, but logs loudly —
    this is a config problem, not a transient one."""

class LLMUnavailableError(LLMError):
    """Network, 5xx, timeout. Router catches and falls back."""

class BaseProvider(ABC):
    """Abstract LLM provider. All providers must be async-only."""

    name: str  # 'gemini' or 'openai' — used in logs and LLMResponse

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        # tools: list[dict] | None = None,  # commented — wired Day 20
    ) -> LLMResponse:
        """Send a single user prompt; return the reply.

        Implementations MUST:
        - catch SDK-specific exceptions and re-raise as LLMRateLimitError /
          LLMAuthError / LLMUnavailableError so the router can react uniformly
        - return token counts when the SDK provides them (None otherwise)
        """
```

**Then ask Claude Code** to (a) confirm the interface is sensible, (b) suggest if anything is missing for Day 20 tool calling (it shouldn't need to *implement* tools today, but the interface should accept them — note the commented `tools` parameter above).

**Verify:** `python -c "from backend.llm.base import BaseProvider, LLMResponse, LLMError; print('ok')"`

---

### Task 2 — `backend/llm/gemini.py` — Gemini provider (~60 min)

**Why second:** Gemini is primary; if it doesn't work, falling back to anything is moot.

**Write yourself first:**

```python
# backend/llm/gemini.py
"""Gemini provider using google-genai (the new unified SDK).

NOT google-generativeai (deprecated as of late 2025).
See google.github.io/genai for the canonical patterns.
"""
from google import genai  # the new SDK
from google.genai import types
# ... import LLMResponse, BaseProvider, the three error classes
# ... import settings, logger

class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self) -> None:
        # Client is cheap to construct; we make one per provider instance.
        # The settings object is the cached singleton (see Day 3).
        # If the key is empty, the SDK will raise on first call — we want
        # that to surface as LLMAuthError below, not at construction time.

    async def generate(self, prompt: str, *, system_prompt: str | None = None) -> LLMResponse:
        # 1. Build GenerateContentConfig only if we have a system prompt
        #    or non-default params. Pass it through `config=...`.
        # 2. Call client.aio.models.generate_content(model=..., contents=prompt, config=...)
        # 3. Map response.usage_metadata.{prompt_token_count, candidates_token_count}
        #    → LLMResponse fields. Be defensive: usage_metadata can be missing.
        # 4. Catch and translate exceptions (see §5 — Error Mapping).
```

**Things to actually verify against the SDK** (per `CLAUDE.md` §4 "verify versions before suggesting code"):

- Is the async entrypoint `client.aio.models.generate_content(...)` or `client.models.generate_content_async(...)`? *(Current docs: `client.aio.models.generate_content` — but check the installed `google-genai` version with `pip show google-genai` and skim its `__init__` namespace if Claude is unsure.)*
- Where do token counts live? *(Currently: `response.usage_metadata.prompt_token_count` and `.candidates_token_count`. Confirm in the actual response object — log the raw response once during testing.)*
- Which exception class does the SDK raise on 429? On bad API key? *(google-genai raises `google.genai.errors.APIError` with `.code` — check by deliberately breaking the key and reading the traceback.)*

**Model to use:** `gemini-2.5-flash` for the chat endpoint. It's free-tier-friendly and fast enough for Jarvis's main conversation loop. Reserve `gemini-2.5-pro` for the importance scorer (Day 6) and paper summarization (Day 22) where quality matters more. **Add to `settings.py`:**

```python
gemini_model: str = "gemini-2.5-flash"        # default chat model
gemini_model_heavy: str = "gemini-2.5-pro"    # reserved for summarization & scoring
```

(If `gemini_model` already exists in settings.py from Day 3, just read its current value — don't overwrite a deliberate Day-3 choice.)

**Verify before moving on:**
```powershell
python -c "import asyncio; from backend.llm.gemini import GeminiProvider; print(asyncio.run(GeminiProvider().generate('Say hi in 5 words')).text)"
```
Should print 5-ish words from Gemini and not raise. If it raises, fix here — do NOT proceed to the router with a broken primary.

---

### Task 3 — `backend/llm/openai.py` — OpenAI fallback (~40 min)

**Why third:** symmetrical to Gemini but simpler (the OpenAI SDK has been stable for a year). Use `openai>=1.0` with `AsyncOpenAI`.

```python
# backend/llm/openai.py
from openai import AsyncOpenAI, RateLimitError, AuthenticationError, APIError, APIConnectionError
# ... imports as in gemini.py

class OpenAIProvider(BaseProvider):
    name = "openai"
    # __init__: AsyncOpenAI(api_key=settings.openai_api_key)
    # generate:
    #   - client.chat.completions.create(
    #         model=settings.openai_model,
    #         messages=[
    #             {"role": "system", "content": system_prompt} if system_prompt else None,
    #             {"role": "user", "content": prompt},
    #         ].filter(None),
    #     )
    #   - text = response.choices[0].message.content
    #   - tokens = response.usage.prompt_tokens / completion_tokens
    #   - catch RateLimitError → LLMRateLimitError, AuthenticationError → LLMAuthError,
    #     (APIError, APIConnectionError) → LLMUnavailableError
```

**Model setting:** `openai_model: str = "gpt-4o-mini"` is plenty for fallback duty (cheap, capable, fast). Add to `settings.py` if not already there.

**Verify:**
```powershell
python -c "import asyncio; from backend.llm.openai import OpenAIProvider; print(asyncio.run(OpenAIProvider().generate('Say hi in 5 words')).text)"
```

---

### Task 4 — `backend/llm/router.py` — fallback orchestration (~50 min)

**Why fourth:** can only be built once both providers individually work.

```python
# backend/llm/router.py
from .base import BaseProvider, LLMResponse, LLMError, LLMRateLimitError, LLMUnavailableError
from .gemini import GeminiProvider
from .openai import OpenAIProvider
from ..services.cost_tracker import cost_tracker
from loguru import logger

class LLMRouter:
    """Tries primary (Gemini); falls back to OpenAI on rate/network errors.

    Does NOT fall back on LLMAuthError if both providers have bad keys — at
    that point it re-raises so the user sees a clear config problem rather
    than a confusing "all providers failed" loop.
    """

    def __init__(self, primary: BaseProvider, fallback: BaseProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    async def generate(self, prompt: str, *, system_prompt: str | None = None) -> LLMResponse:
        # 1. Try primary. On LLMRateLimitError or LLMUnavailableError → log
        #    a structured WARNING with reason, then try fallback.
        # 2. On LLMAuthError from primary → log ERROR, still try fallback
        #    (the user may have only one key — the fallback might work).
        # 3. On any error from BOTH → raise LLMError("all providers failed: ...")
        #    with both underlying errors chained.
        # 4. After a successful call: cost_tracker.record(response, request_id_var.get())
        # 5. Return the LLMResponse.

# Module-level singleton — built once at app startup
_router: LLMRouter | None = None

def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter(primary=GeminiProvider(), fallback=OpenAIProvider())
    return _router
```

**Critical detail about logging here:** because the router is called *from inside* `/chat`'s request handler, the request-ID ContextVar is already set by the Day-3 middleware. So `logger.warning("primary failed, falling back", reason=...)` automatically picks up the right `request_id`. **You do NOT need `logger.bind(request_id=...)` here** — that's only needed for background tasks (per the P1 follow-up note in the Day-3 status doc).

---

### Task 5 — `backend/services/cost_tracker.py` — stub writer (~25 min)

**Why fifth:** small but important. Day 5 creates the `cost_log` table. Today we build the *interface* so Day 5 just adds the SQL.

The plan calls for this on Day 4, but the DB doesn't exist yet. Two options:

**Option A — log-only today, DB tomorrow.** `cost_tracker.record(...)` writes a structured log line (`logger.info("cost", extra={...})`) and that's it. Day 5 swaps the implementation to also `INSERT INTO cost_log`. Routes don't change.

**Option B — defer cost tracker entirely to Day 5.** Don't import it from the router yet.

**Recommendation: A.** It costs 15 lines today and ensures the cost-tracking call site exists in the router, so when Day 5 swaps the implementation nothing in the router has to change. Also gives you a paper trail in logs from day one.

```python
# backend/services/cost_tracker.py
"""Cost tracker — stub-writer for Day 4.

Today: logs every LLM call with token counts. No DB write yet.
Day 5: this same function will also INSERT INTO cost_log.
The router calls cost_tracker.record(...) — that call site does not change.
"""
from loguru import logger
from ..llm.base import LLMResponse

# Pricing per million tokens (USD). Update when Google/OpenAI change rates.
# Source: ai.google.dev/pricing and openai.com/api/pricing (last checked: Day 4)
_PRICING = {
    "gemini-2.5-flash":   {"input": 0.075, "output": 0.30},
    "gemini-2.5-pro":     {"input": 1.25,  "output": 5.00},
    "gpt-4o-mini":        {"input": 0.15,  "output": 0.60},
}

def estimate_cost_usd(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> float:
    """Estimate USD cost for one call. Returns 0.0 if tokens are unknown."""
    if not prompt_tokens or not completion_tokens or model not in _PRICING:
        return 0.0
    p = _PRICING[model]
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000

class CostTracker:
    async def record(self, response: LLMResponse) -> None:
        cost = estimate_cost_usd(response.model, response.prompt_tokens, response.completion_tokens)
        logger.info(
            "llm_call",
            extra={
                "provider": response.provider,
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "estimated_usd": round(cost, 6),
            },
        )
        # TODO(Day 5): INSERT INTO cost_log (...)

cost_tracker = CostTracker()
```

**Verify:** after Task 6 wiring, hit `/chat` once and `grep llm_call data/logs/jarvis.log` — should show a structured line with the model, tokens, and cost.

---

### Task 6 — Wire `/chat` to the router (~20 min)

The smallest diff of the day. Replace the Day-3 stub in `backend/api/chat.py`:

```python
# Before (Day 3):
return ChatResponse(reply=f"(stub) I received: {req.message}", request_id=request_id)

# After (Day 4):
from ..llm.router import get_router
from ..llm.base import LLMError

try:
    result = await get_router().generate(req.message)
    return ChatResponse(
        reply=result.text,
        provider=result.provider,   # new field — see Task 7
        model=result.model,         # new field — see Task 7
        request_id=request_id,
    )
except LLMError as e:
    # All providers failed. Surface a human-readable message, NOT a stack trace.
    logger.error(f"chat failed: {e}")
    raise HTTPException(status_code=503, detail="LLM unavailable, please try again.")
```

**Per `CLAUDE.md` §"All external API calls need graceful error handling":** the 503 is critical. Without it, a transient Gemini+OpenAI outage returns a 500 stack trace to the user. Test this by setting both API keys to garbage briefly.

---

### Task 7 — Tiny model update + optional frontend tweak (~15 min)

**Update `backend/models/chat.py`:** add `provider: str` and `model: str` to `ChatResponse` so the frontend can show which one answered (useful during debugging when fallback fires). Keep them optional (`provider: str | None = None`) to avoid breaking the Day-3 test client if any tests exist.

**Frontend (optional, ~10 min):** in `App.tsx`, the existing chat send button — render the provider in small grey text next to the reply: `(via gemini)`. Don't refactor the chat panel; that's Week 2. One-line addition.

If short on time, skip the frontend tweak. The browser test is `curl` + `/docs` — both show the `provider` field without any UI change.

---

## 5. Error mapping — the part that decides whether the fallback actually fires

The router's job is to react uniformly to SDK errors. Each provider class translates SDK-specific exceptions into the three base error types. Get this wrong and the fallback never fires — or fires when it shouldn't.

| Scenario | google-genai raises | openai raises | Map to |
|---|---|---|---|
| Bad API key | `APIError` with code 401/403 | `AuthenticationError` | `LLMAuthError` |
| Rate limit / quota | `APIError` with code 429 | `RateLimitError` | `LLMRateLimitError` |
| Server 5xx | `APIError` with code 5xx | `APIError` | `LLMUnavailableError` |
| Network timeout | `httpx.HTTPError` (under the hood) | `APIConnectionError` | `LLMUnavailableError` |
| Malformed prompt / 400 | `APIError` with code 400 | `BadRequestError` | Let it raise as `LLMError` — fallback unlikely to help, fix the prompt |

**Concrete implementation pattern (per provider):**

```python
try:
    response = await client.aio.models.generate_content(...)
except genai_errors.APIError as e:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code == 429:
        raise LLMRateLimitError(str(e)) from e
    if code in (401, 403):
        raise LLMAuthError(str(e)) from e
    if code and 500 <= code < 600:
        raise LLMUnavailableError(str(e)) from e
    raise LLMError(str(e)) from e
except Exception as e:
    # Any non-API failure (network, etc.) — treat as unavailable
    raise LLMUnavailableError(str(e)) from e
```

**Test that the mapping is right:** set `gemini_api_key` to garbage temporarily → call `/chat` → confirm the logs say "primary failed: auth → falling back to openai" and the reply still comes back from OpenAI.

---

## 6. Completion criteria (from the day-by-day plan, expanded)

Tick each one before you commit.

- [ ] **C1.** `POST /chat` with `{"message": "Hello"}` returns a real Gemini response (curl + `/docs`).
- [ ] **C2.** The response includes `"provider": "gemini"` and `"model": "gemini-2.5-flash"`.
- [ ] **C3.** Set `GEMINI_API_KEY=bad` in `.env`, restart, repeat C1 → response still comes back, with `"provider": "openai"`. Logs show a WARNING about primary failure. Restore the real key after.
- [ ] **C4.** Set BOTH keys to bad → `/chat` returns HTTP 503 with `{"detail": "LLM unavailable, please try again."}`. **No stack trace in the response body.** Logs show ERROR.
- [ ] **C5.** Long prompt (e.g. "Write a 200-word poem about kinase inhibitors") works in under ~5 seconds and returns coherent text.
- [ ] **C6.** Every `/chat` call produces one `llm_call` structured log line with `provider`, `model`, `prompt_tokens`, `completion_tokens`, `estimated_usd`. Confirm in `data/logs/jarvis.log`.
- [ ] **C7.** Request IDs flow end-to-end: the `X-Request-ID` in the HTTP response matches the `request_id` in the `llm_call` log line for that call. *(Sanity check that Day-3's ContextVar threading works under real load.)*
- [ ] **C8.** Frontend send-message button (the one from Day 3) actually returns a real reply, not the stub. **You can show the moment to yourself and feel the project become real.**
- [ ] **C9.** Out loud, in plain English, explain: what `BaseProvider` is for. What `GeminiProvider.generate` does step by step. What the router does on rate-limit error. What `cost_tracker.record` will become on Day 5. *(Per `CLAUDE.md` — comprehension test.)*
- [ ] **C10.** `requirements.txt` updated with **pinned** `google-genai==X.Y.Z` and `openai==X.Y.Z` (the actual versions you installed, copied from `pip freeze`). Per the standing watch-out and the Day-3 status doc heads-up.

---

## 7. End-of-day chores

Before commit:
- [ ] Delete or move any throwaway test scripts (`test_gemini.py`, `try_openai.py`) — per `CLAUDE.md` "File organization rules", no loose `.py` files in repo root.
- [ ] Run all four Day-3 curl commands to confirm `/health`, `/memory`, `/voice-state`, and `/ws/voice` still work — Day 4 should not have touched any of them.
- [ ] One line in `docs/journal.md`: e.g. *"Day 4 — Gemini primary, OpenAI fallback working; switched from deprecated google-generativeai to google-genai. ~270 LOC across 5 new files."*
- [ ] Update `.claude/skills/project-architecture/SKILL.md` gotcha line from `google-generativeai` → `google-genai`. (One-line edit. Per `CLAUDE.md` §"When this file is wrong".)
- [ ] **Commit** with the exact message:
  `feat: llm provider abstraction with gemini primary, openai fallback`
- [ ] Glance at Day 5 — SQLite schema. Note that today's `cost_tracker` has a `TODO(Day 5)` waiting for you.

---

## 8. Heads-up — things to watch downstream

These are things today's design decisions ripple into. None require action today; flagging so you (and a future session) don't get blindsided.

1. **Model selection per call site.** Today everything uses `settings.gemini_model` (Flash). Day 6 (importance scoring) and Days 22-24 (paper summarization) will want Pro for quality. The `generate()` signature does NOT take a `model` parameter today. **Decision deferred:** add `model: str | None = None` to `generate()` on Day 6, or build a second `generate_heavy()` method. Either is fine; today's interface doesn't lock you in.

2. **Tool calling (Day 20) needs to extend, not replace, this interface.** The interface intentionally has a commented `tools` parameter (Task 1). On Day 20, uncomment it, add `tool_results: list[ToolResult] | None`, and add a new field to `LLMResponse` for `tool_calls`. The router itself shouldn't need changes — only the provider classes.

3. **Streaming is not built today.** Both SDKs support streaming (`stream=True` / `generate_content_stream`). For v1 conversation latency, non-streaming Flash is fast enough (~1.5s typical). If end-to-end latency on Day 11's full voice loop is too high, *then* revisit streaming — not before.

4. **The router has no retry-with-backoff.** A single rate-limit error → immediate fallback. For v1 this is correct (Gemini's free tier rarely rate-limits a single user). If false fallbacks become annoying, add a single 500ms retry on rate-limit *before* falling back. Don't pre-build it.

5. **No conversation history yet.** Today `/chat` is one-shot — each message is independent. Day 5 (SQLite messages table) + Day 6 (semantic memory) provide the context that turns this into a real conversation. **Do not** add a `messages: list[Message]` history parameter to `generate()` today — it'll be more obvious how to shape it after Day 6.

6. **Pricing constants in `cost_tracker.py` will drift.** Add a comment with the date you sourced them, and a TODO to re-check at the end of Month 1. Off-by-2x cost is fine; off-by-10x is a problem.

7. **The Day-3 P4 stale memory** ("Day 10 voice/STT pipeline bugs") is still uncorrected. If a fresh Claude Code session today acts confused about where the project is, correct or delete that memory before going further.

---

## 9. If the day goes sideways

Common failure modes, in expected order of likelihood:

| Symptom | Likely cause | First thing to check |
|---|---|---|
| `ImportError: cannot import name 'genai' from 'google'` | Installed the wrong package — `google-generativeai` instead of `google-genai` | `pip show google-genai` vs `pip show google-generativeai` |
| Gemini returns 400 "model not found" | Model name wrong (e.g. `gemini-flash` instead of `gemini-2.5-flash`) | Print `settings.gemini_model`, compare to current Gemini model list |
| Fallback never fires when Gemini key is bad | Exception mapping in `gemini.py` — the SDK exception isn't being caught and re-raised as `LLMAuthError` | Log the raw exception type and code from inside the `except`; adjust the mapping |
| Fallback fires on every call even with good keys | Exception mapping too eager — catching `Exception` and treating it as unavailable | Tighten the broad `except Exception` to specific SDK error types |
| OpenAI returns "you exceeded your current quota" | Free OpenAI tier exhausted | Use a tiny model (`gpt-4o-mini`) and short prompts; or accept that OpenAI fallback isn't tested today (Gemini alone is enough to commit Day 4) |
| Tokens come back `None` always | `response.usage_metadata` access path wrong | Log the raw `response` once; navigate to where tokens actually live |
| Request ID is `-` in `llm_call` log lines | The router was called from a background task, not an HTTP request — ContextVar default kicked in | Confirm `/chat` is the only call site today (it should be); if not, add `logger.bind(request_id=...)` at the background-task call site |

**Per `CLAUDE.md` §"When I'm stuck":** if you paste an error to Claude Code, ask it to (1) explain the error, (2) give 2-3 likely causes ranked by probability, (3) suggest a one-line diagnostic. Don't accept an immediate rewrite.

---

## 10. Time budget breakdown

| Block | Allotted | Notes |
|---|---|---|
| §1 Review + §2 SDK decision (with user) | 20 min | The decision conversation is short if you've read §2 |
| Task 1 — base.py | 25 min | |
| Task 2 — gemini.py | 60 min | Most likely to overrun — give it room |
| Task 3 — openai.py | 40 min | Easier than Gemini; SDK is stable |
| Task 4 — router.py | 50 min | |
| Task 5 — cost_tracker.py | 25 min | |
| Task 6 — wire /chat | 20 min | |
| Task 7 — model update + optional frontend | 15 min | Skip frontend if behind |
| Verification — all 10 completion criteria | 30 min | Don't skimp |
| Cleanup + commit + journal | 15 min | |
| **Total** | **5h 0min** | Matches the plan's budget |

**If past 5 hours and still not done:**
- Tasks 1-4 are the irreducible core. Skip Task 7 entirely (no frontend, leave `ChatResponse` as Day-3 shape).
- Task 5 can be deferred to Day 5 if cost-tracking isn't working — just don't import it from the router yet.
- Do not skip Task 4 (the router) to "ship something" — without it, today is just "added Gemini directly to chat.py", which is Day 4 of a different project.

---

## 11. One-sentence test before bed

> *"I can ask Jarvis a real question, get a real answer, and if I deliberately break Gemini, I get the same answer from OpenAI without restarting anything."*

If that's true, Day 4 is done.
