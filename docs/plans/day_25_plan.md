# Day 25 Plan — Web Search (Tavily + Gemini Grounding)

**Period:** Week 4, Day 6 — Web Search
**Goal:** "What's the latest on X?" returns current, spoken results with clickable source links in the chat panel. Jarvis stops being limited to its training cutoff and to PDFs you hand it — it can now reach the live web.
**Prerequisites:** Days 20–24 complete (tool registry working, `summarize_paper` + `fetch_arxiv` shipped). Gemini quota reset since Day 24.
**Time budget:** ~4 hours (Pre-flight ~45 min + build ~3 h + verify ~30 min)

> Read `.claude/skills/tool-calling-pattern/SKILL.md` before writing any tool. This day adds **one or two new tools** following the 4-step pattern. The schema rules and the hard-vs-soft error split there are load-bearing — getting the JSON schema wrong throws a cryptic Gemini 400 at *call* time, not registration.

---

## 0. Pre-flight (T-0) — clear the Day 24 carry-over first

Day 24 shipped all the summarization code but **could not run the reduce stage end-to-end** because the Gemini free-tier quota was exhausted (map stage burned through 15 RPM on 17–25 chunks before reduce could run). Verification checklist item 9 from Day 24 is still open. Close it before adding anything new — you do not want to debug a web-search problem on top of an unverified summarizer.

There is also one piece of housekeeping (`data/dropped/` gitignore) carried over from the Day 24 heads-up.

### T-0a — Check which Gemini models are live again

Run the model-availability probe from the Day 24 status doc. (Same script, reproduced here so you don't have to dig.)

```bash
python -c "
import asyncio; from google import genai; from backend.config.settings import get_settings
s = get_settings(); client = genai.Client(api_key=s.gemini_api_key)
async def t():
    for m in ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-flash-lite-latest']:
        try:
            await client.aio.models.generate_content(model=m, contents='ping')
            print(f'OK  {m}')
        except Exception as e:
            print(f'NO  {m}: {str(e)[:60]}')
asyncio.run(t())
"
```

**Expected:** at least `gemini-2.5-flash` prints `OK`. If it still shows 503/429, fall back to `gemini-2.0-flash` for the summarizer and note it; do **not** spend Day 25 fighting Google's quota.

### T-0b — Restore the summarizer model

Day 24 temporarily set `summarizer_model = "gemini-flash-lite-latest"` to limp through testing. If T-0a shows `gemini-2.5-flash` is back:

```python
# backend/config/settings.py
# Restore the quality model for paper summarization now that quota has reset.
# flash-lite was a Day-24-only stopgap; it produced weaker structured summaries.
summarizer_model: str = "gemini-2.5-flash"   # was temporarily "gemini-flash-lite-latest"
```

Minimal diff — change only this one line. Leave everything else in `settings.py` untouched.

### T-0c — Complete the blocked Day 24 reduce-stage test (the important one)

This is the actual carried-over test. Run a **full** summarization end-to-end and confirm the reduce stage now produces structured output. Use one already-cached input so there's no fresh download:

- Arxiv path: `data/arxiv/2312.04019.pdf` (cached Day 24), **or**
- Dropped path: `data/dropped/2022.12.31.522396v1.full.pdf` (cached Day 24)

Steps:
1. Restart the backend. Confirm startup log: `tools registered: 7`.
2. Via PTT: *"Summarize arxiv 2312.04019"* (or drop the cached PDF and say *"summarize this"*).
3. Watch `data/logs/jarvis.log` for the full chain:
   ```
   map stage: 17–25 intermediates logged
   reduce stage: structured output produced   ← this is what failed Day 24
   ```
4. Confirm the spoken reply contains `key_claims` + `relevance_to_user`, and the chat panel shows the full structured summary.

**Pass condition:** reduce stage completes; you hear a coherent spoken summary. If reduce **still** 429s even on `gemini-2.5-flash`, the map stage is firing too many concurrent calls — add a small `asyncio.Semaphore` around the map calls (note it as a Day 25 micro-fix, don't expand it into a refactor). Otherwise, mark Day 24 item 9 ✅ and move on.

### T-0d — gitignore `data/dropped/`

Carried over from the Day 24 heads-up. Dropped PDFs should never be committed.

```gitignore
# data/.gitignore (or root .gitignore — match wherever recordings/ and arxiv/ are)
data/dropped/
```

Mirror the existing pattern for `data/recordings/` and `data/arxiv/`. Quick `git status` after to confirm no stray PDFs are staged.

**T-0 done when:** correct model restored, Day 24 summarization verified end-to-end, `data/dropped/` ignored.

---

## 1. Agenda for the day

Days 22–24 gave Jarvis the ability to read documents you hand it. Day 25 gives it the ability to **go find information itself**. Two retrieval paths, picked by the LLM based on query type:

| Query type | Tool | Backend |
|---|---|---|
| Technical / research / "latest papers on X" | `web_search` | Tavily API |
| Everyday current facts (weather, scores, general news) | `grounded_search` | Gemini Google-Search grounding |

The spoken reply is a **short synthesis**; the **clickable source links** go to the chat panel (never read URLs aloud — see §5). Both tools follow the existing tool-calling 4-step pattern, so the orchestrator and the tool-call loop stay **unchanged**.

**Scope discipline:** `web_search` (Tavily) is the **core deliverable and must ship**. `grounded_search` (Gemini grounding) is the **second task and is cuttable** — see §2 Decision 1 and §8. Notably, `grounded_search` hits the *same Gemini quota that broke Day 24*, while Tavily has its own generous free tier (1000 searches/month). So the safer, more reliable feature is also the core one. That's deliberate.

---

## 2. Decisions to settle before coding

Per the working style, settle these before writing 200 lines. My recommendation is given for each; override any of them in-session and I'll adjust.

### Decision 1 — Ship one tool or two today?

- **(a) `web_search` only.** Tavily can answer weather/news/papers acceptably. Ships in ~1.5 h. Defer `grounded_search`.
- **(b) Both `web_search` + `grounded_search`.** Matches the plan's "LLM picks by query type." More work; `grounded_search` adds grounding-metadata parsing + a provider method + Gemini-quota exposure.

**Recommendation:** build **(a) fully and solidly first**, then add **(b)** only if T-0 was clean and you have >1.5 h left. The completion criteria want grounded search, but Tavily satisfies the user-facing need on its own, so (b) is the honest cut line if the day runs long.

### Decision 2 — Async Tavily client vs sync + executor

- **(a) `AsyncTavilyClient`** — native async, matches the project's async-first rule. Requires confirming the installed `tavily-python` exposes it (T-1).
- **(b) sync `TavilyClient` wrapped in `run_in_executor`** — the exact pattern you already used for arxiv's `Client.results()` in Day 24.

**Recommendation:** **(a)** if T-1 confirms `AsyncTavilyClient` exists; **(b)** as the fallback. Don't block the day on this — if the async import isn't there, wrap the sync client and move on.

### Decision 3 — Tavily client lifecycle

- **(a) module-level lazy singleton** in `web_search.py` — built once on first call, reused (holds an httpx pool). Mirrors the "construct once, never per-call" rule for the STT/TTS clients.
- **(b) per-call `AsyncTavilyClient(...)`** inside the handler — simplest, slightly wasteful.

**Recommendation:** **(a)**. The lazy singleton is ~5 extra lines and avoids rebuilding a connection pool on every search.

### Decision 4 — How do source links reach the UI?

The spoken answer must not contain URLs (TTS reading `h-t-t-p-s-colon-slash...` is unbearable). But the user wants clickable links in chat. So the links need a separate path to the frontend.

- **(a) Tool broadcasts a `search_results` WebSocket event** as a side effect, returning content to the LLM for synthesis. Orchestrator untouched. Cost: the tool now has a side effect beyond returning data, and needs the `ws_manager` singleton (local import to dodge a circular import).
- **(b) Tool returns links in its dict; the orchestrator extracts and broadcasts them.** Keeps tools pure, but special-cases tool names in the orchestrator — uglier, and it touches the carefully-locked `conversation.py`.

**Recommendation:** **(a)**. It's the more contained change and keeps `conversation.py` (the lock-sensitive file) untouched, which `voice-pipeline/SKILL.md` strongly prefers. Accept the small impurity. **Verify where your `ws_manager` singleton actually lives** — I've assumed `backend.main` below but adjust the import.

### Decision 5 — Do web searches get persisted to memory?

- **(a) No.** Search is ephemeral retrieval, not a memory-worthy event. If the user wants to keep a finding, they say "log this" (existing Day 21 flow).
- **(b) Yes**, auto-persist like `summarize_paper` did (importance=10).

**Recommendation:** **(a)**. Keeps scope tight and avoids polluting project memory with every casual query. (Search results are also low-signal compared to a paper summary.) This is consistent with the "responds, doesn't hoard" design.

---

## 3. Tasks

### T-1 — Install and **verify** the Tavily SDK

```bash
pip install tavily-python
pip freeze | grep -i tavily   # note the exact version
```

Then pin it in `backend/requirements.txt` (same discipline as the Day 24 arxiv pins).

**Verify the API shape before writing code** (CLAUDE.md rule 4 — SDKs drift). Two things to confirm:

```bash
python -c "
import tavily, inspect
print('version:', getattr(tavily, '__version__', 'unknown'))
print('AsyncTavilyClient?', hasattr(tavily, 'AsyncTavilyClient'))
from tavily import TavilyClient
print('search sig:', inspect.signature(TavilyClient.search))
"
```

- If `AsyncTavilyClient?` is `True` → Decision 2(a).
- If `False` → Decision 2(b) (sync + `run_in_executor`).
- Confirm `search()` accepts `max_results`, `search_depth`, `include_answer`. If the signature differs, adjust the handler in T-2.

**Confirm the result shape** with one live call (uses one of your 1000 free searches):

```bash
python -c "
import os, json
from tavily import TavilyClient
from backend.config.settings import get_settings
c = TavilyClient(api_key=get_settings().tavily_api_key)
r = c.search('latest ABL1 inhibitor papers', max_results=2, include_answer=True)
print('top-level keys:', list(r.keys()))
print('result[0] keys:', list(r['results'][0].keys()))
print('answer present:', bool(r.get('answer')))
"
```

You're checking that each result has `title`, `url`, `content` (the keys the Day 24 status doc specified the tool returns).

### T-2 — `web_search` tool (Tavily) — **core, must ship**

Create `backend/tools/web_search.py` following the 4-step pattern. Schema is **hand-written** (no Pydantic `.model_json_schema()` — it emits `$defs`/`$ref` that Gemini rejects; see the tool-calling SKILL).

```python
"""Web search tool — queries Tavily and returns ranked results for the LLM to synthesize."""

from tavily import AsyncTavilyClient   # T-1: fall back to sync TavilyClient + run_in_executor if absent

from backend.config.settings import get_settings
from backend.tools import registry

# Module-level lazy singleton (Decision 3). The client holds an httpx connection
# pool; building it once and reusing it is cheaper than per-call construction,
# matching the "construct once" rule used for the STT/TTS clients.
_client: AsyncTavilyClient | None = None


def _get_client() -> AsyncTavilyClient:
    global _client
    if _client is None:
        _client = AsyncTavilyClient(api_key=get_settings().tavily_api_key)
    return _client


@registry.register(
    name="web_search",
    description=(
        "Search the web for current information. Use this when the user asks about "
        "recent papers, latest research, or news on a topic (e.g. 'latest ABL1 "
        "inhibitor papers', 'recent work on T315I resistance'). Returns sources with "
        "titles, URLs, and content snippets. Prefer this for anything technical or "
        "research-related. Do not guess at recent facts — always call this tool."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query, phrased as you would type it into a search engine.",
            },
            "max_results": {
                "type": "integer",
                "description": "How many results to return. Default 5.",
            },
        },
        "required": ["query"],
    },
)
async def web_search(query: str, max_results: int = 5) -> dict:
    """Query Tavily, trim results, broadcast links to the UI. Soft-errors on API failure."""
    settings = get_settings()
    client = _get_client()

    # Tavily failures (network, quota, bad key) are SOFT errors: return an error
    # dict so the LLM can apologise gracefully instead of crashing the turn into
    # ERROR state. (tool-calling SKILL §"Hard vs soft errors".)
    try:
        response = await client.search(
            query=query,
            max_results=max_results,
            search_depth=settings.tavily_search_depth,    # "basic" (fast) or "advanced" (research)
            include_answer=settings.tavily_include_answer, # Tavily's own synthesis, saves LLM tokens
        )
    except Exception as exc:   # Tavily raises several types; treat all as soft
        return {"error": str(exc), "type": exc.__class__.__name__}

    # Trim each result's content so we don't flood the LLM context with five full
    # web pages. Cap lives in settings (no magic numbers rule).
    cap = settings.web_search_content_cap
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": (r.get("content", "") or "")[:cap],
        }
        for r in response.get("results", [])
    ]

    # Side effect (Decision 4): push raw links to the UI so the user gets clickable
    # sources alongside the spoken summary. The LLM gets `results` for synthesis.
    await _broadcast_sources(results)

    return {
        "answer": response.get("answer"),   # may be None if include_answer is off
        "results": results,
    }


async def _broadcast_sources(results: list[dict]) -> None:
    """Broadcast a search_results event so ChatPanel can render clickable links."""
    # Local import avoids a circular import at module load (tools are imported during
    # lifespan, before ws_manager may be ready). VERIFY this path — ws_manager may
    # live somewhere other than backend.main in your code.
    from backend.main import ws_manager

    sources = [{"title": r["title"], "url": r["url"]} for r in results if r["url"]]
    await ws_manager.broadcast({"type": "search_results", "sources": sources})
```

Then **Step 3 of the pattern** — register the import in the `main.py` lifespan block:

```python
import backend.tools.web_search  # noqa: F401   # add next to fetch_arxiv import
```

> If T-1 said no `AsyncTavilyClient`: keep the sync `TavilyClient` as the singleton and call it via
> `await asyncio.get_running_loop().run_in_executor(None, lambda: client.search(...))` — same shape you used for arxiv's `Client.results()` in Day 24.

### T-3 — `grounded_search` tool (Gemini grounding) — **second, cuttable**

> **Critical architecture note.** Gemini's Google-Search grounding is a *built-in tool* (`google_search`). You **cannot reliably pass `google_search` AND your function-calling tools in the same request** — doing so breaks the whole tool-call loop. That is exactly why `grounded_search` is its **own** registry tool whose handler makes a **fresh, function-tool-free** Gemini call. The main conversation keeps its function tools; this handler runs an isolated grounded call.

> **Do not bypass the router** (architecture rule). Grounding is a Gemini-specific feature, so add a small method to the Gemini provider and reach it through the router, rather than instantiating a raw `genai.Client` inside the tool.

**Step A — add a grounding method to the Gemini provider** (`backend/llm/gemini.py`):

```python
async def grounded_search(self, query: str) -> dict:
    """Run a Google-Search-grounded Gemini generation. Returns text + source URLs.

    Grounding is a built-in Gemini tool, separate from our function tools — that is
    why this is an isolated call with ONLY google_search attached.
    """
    from google.genai import types   # lazy import, same convention as the registry

    response = await self._client.aio.models.generate_content(
        model=self._settings.grounding_model,           # e.g. gemini-2.5-flash (T-0)
        contents=query,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    # Sources live in grounding_metadata, NOT in the text. The exact attribute path
    # has shifted across google-genai versions — verify against the installed SDK.
    sources = []
    try:
        gm = response.candidates[0].grounding_metadata
        for chunk in (gm.grounding_chunks or []):
            if getattr(chunk, "web", None):
                sources.append({"title": chunk.web.title, "url": chunk.web.uri})
    except (AttributeError, IndexError):
        pass   # no grounding metadata returned; a text-only answer is still valid

    return {"text": response.text, "sources": sources}
```

Verify the grounding-metadata path before trusting it:

```bash
python -c "
import asyncio
from google import genai
from google.genai import types
from backend.config.settings import get_settings
s = get_settings(); c = genai.Client(api_key=s.gemini_api_key)
async def t():
    r = await c.aio.models.generate_content(
        model='gemini-2.5-flash', contents='weather in Thiruvananthapuram today',
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]))
    print('text:', r.text[:120])
    gm = r.candidates[0].grounding_metadata
    print('has grounding_chunks:', hasattr(gm, 'grounding_chunks'))
asyncio.run(t())
"
```

**Step B — the tool** (`backend/tools/grounded_search.py`):

```python
"""Grounded search tool — answers current-fact questions via Gemini Google-Search grounding."""

from backend.llm.router import get_gemini_provider   # VERIFY accessor name in your router
from backend.tools import registry
from backend.tools.web_search import _broadcast_sources   # reuse the same UI broadcast


@registry.register(
    name="grounded_search",
    description=(
        "Answer everyday current-fact questions using Google Search grounding. Use "
        "this for things like weather, sports scores, or general news where a short "
        "grounded answer is enough. For technical or research-paper queries, prefer "
        "web_search instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question to answer with a grounded search.",
            },
        },
        "required": ["query"],
    },
)
async def grounded_search(query: str) -> dict:
    # Soft-error: grounding hits the Gemini quota that broke Day 24, so a clean
    # error dict lets the LLM fall back ("couldn't reach search, try again") instead
    # of crashing the turn.
    try:
        result = await get_gemini_provider().grounded_search(query)
    except Exception as exc:
        return {"error": str(exc), "type": exc.__class__.__name__}

    await _broadcast_sources(
        [{"title": s["title"], "url": s["url"]} for s in result["sources"]]
    )
    return result
```

Register in `main.py` lifespan (cut this line if deferring T-3):

```python
import backend.tools.grounded_search  # noqa: F401
```

> The exact provider accessor (`get_gemini_provider`) is a guess — **adjust to whatever your `router.py` actually exposes**. If the router has no clean accessor, add a one-line `get_gemini_provider()` helper there rather than reaching into internals from the tool.

### T-4 — Frontend: render source links in chat

Consume the new `search_results` event and show a compact, clickable list under the assistant's spoken answer. Keep it minimal — this is "good enough," not a design exercise.

1. **`frontend/src/hooks/useWebSocket.ts`** — add `search_results` to the event union and store the latest `sources` array (same shape you added `pdf_pending` in Day 24).
2. **`frontend/src/components/ChatPanel.tsx`** — when `sources` are present, render a small "Sources" block: each item an `<a href={url} target="_blank" rel="noreferrer">{title}</a>`.

```tsx
{sources.length > 0 && (
  <div className="mt-2 text-xs opacity-80">
    <div className="font-medium mb-1">Sources</div>
    {sources.map((s) => (
      <a
        key={s.url}
        href={s.url}
        target="_blank"
        rel="noreferrer"
        className="block truncate text-cyan-300 hover:underline"
      >
        {s.title || s.url}
      </a>
    ))}
  </div>
)}
```

> WebView2 note (same family of issue as Day 24's drop handling): external `target="_blank"` links may not open a browser inside the webview by default. If clicks do nothing, open them via the PyWebView/JS bridge or `window.open` shim — note it as a Day 25 micro-fix, don't expand it.

### T-5 — System prompt directives (`backend/prompts/system/50_tools.md`)

The system prompt is loaded once at startup; without a directive the LLM may ignore a tool. Add one line per tool telling it **when** to call, and one rule about not speaking URLs.

```markdown
- `web_search`: when the user asks about recent papers, latest research, or news on a
  topic (e.g. "latest ABL1 inhibitor papers", "recent T315I work"), call `web_search`
  with a focused query. Prefer this for technical/research questions. Speak a brief
  synthesis of the findings — do NOT read out URLs; the UI shows the source links.
- `grounded_search`: when the user asks an everyday current-fact question (weather,
  scores, general news), call `grounded_search`. Keep the spoken answer short and do
  not read out URLs.
```

(Drop the `grounded_search` line if T-3 is cut.)

### T-6 — Settings additions (`backend/config/settings.py`)

No magic numbers — every tunable goes here:

```python
# --- Web search (Day 25) ---
tavily_search_depth: str = "advanced"       # "basic" = faster/cheaper; "advanced" = better for research
tavily_include_answer: bool = True           # let Tavily pre-synthesize; reduces LLM token load
web_search_content_cap: int = 800            # max chars of each result's content passed to the LLM
grounding_model: str = "gemini-2.5-flash"    # model for Google-Search-grounded calls (T-3)
```

Confirm `tavily_api_key` already exists in `Settings` (it's in the locked stack from Day 1); if not, add it and to `.env` / `.env.example`.

### T-7 — Smoke test via voice (the read-review loop)

1. Restart backend. Confirm startup log: `tools registered: 8` (was 7 — or `9` if both new tools landed; `7`→`8` if only `web_search`).
2. PTT: **"What are the latest ABL1 inhibitor papers?"**
   - `data/logs/jarvis.log` shows `tool_call iter=0: web_search({'query': ...})` then `tool_result: web_search -> ...`.
   - You hear a short spoken synthesis (recent, plausibly 2025–2026).
   - Chat panel shows a "Sources" list with clickable links.
3. PTT: **"What's the weather today?"**
   - If T-3 shipped: `grounded_search` fires. If only `web_search` shipped: `web_search` answers — also fine.
4. PTT: **"Set a timer for 1 minute"** (a non-search query) — confirms search tools didn't cannibalize other tools. Should still route to the timer tool (Day 26) or, if not built yet, respond normally without calling a search tool.
5. **Mute mid-search:** start a search, immediately hit Ctrl+Alt+J. Search is slow (advanced depth + synthesis = several seconds), so this exercises the existing per-iteration MUTED re-check in the tool loop (`tool-calling/SKILL.md §"The MUTED re-check rule"`). No new code — just confirm mute lands at the next LLM-call boundary and you don't hear the result.

### T-8 — Verify, journal, commit

Run the §4 checklist. One line in `docs/journal.md`. Commit logical chunks (§7).

---

## 4. Completion criteria

From `Day_by_Day_Plan_v2.md`, Day 25:

- [ ] "Latest ABL1 inhibitor papers?" → relevant 2025–2026 results, spoken synthesis.
- [ ] "What's the weather today?" → grounded (or Tavily, if grounded cut) answer.
- [ ] Links visible in chat for follow-up.

Plus this day's specifics:

- [ ] T-0 cleared: correct summarizer model restored; **Day 24 reduce stage verified end-to-end**; `data/dropped/` gitignored.
- [ ] `web_search` registered; `tools registered:` count incremented.
- [ ] Tavily/Gemini failures return a soft-error dict (no crash to ERROR state).
- [ ] No URLs read aloud.
- [ ] Mute lands cleanly during a slow search.

---

## 5. Watch-outs

- **Never speak URLs.** TTS reading a URL character-by-character is the worst UX in the app. Links go to the UI via `search_results`; the spoken reply is prose only. Enforced by the `50_tools.md` directive — keep it.
- **`grounded_search` ≠ a function tool inside the main call.** Grounding (`google_search`) and function-calling tools don't reliably coexist in one Gemini request. The isolated-call design in T-3 is the whole point — don't "simplify" it by adding `google_search` to the main tool list.
- **Grounded search hits the Day 24 quota.** It uses the same Gemini free tier that ran dry on Day 24. Tavily has its own 1000/month free tier. If Gemini quota is tight, lean on `web_search` and treat `grounded_search` as the cut.
- **Don't route grounding through `router.generate()` blindly.** The generic router method likely doesn't expose the `google_search` config. Add the dedicated provider method (T-3 Step A) — but still reach it via the router/provider, never a raw client (architecture rule).
- **Trim content (T-6 cap).** Five `advanced`-depth results with full content can be thousands of tokens fed back into the LLM. The 800-char cap keeps the synthesis call cheap and fast.
- **Verify the Tavily and grounding-metadata API shapes (T-1, T-3).** Both SDKs drift. The verification snippets exist precisely so you don't write code against a remembered API.
- **`ws_manager` import path is assumed.** I guessed `backend.main`. Confirm where your singleton lives before trusting `_broadcast_sources`.

---

## 6. Files changed this day (anticipated)

```
NEW:
  backend/tools/web_search.py             -- Tavily web search tool (core)
  backend/tools/grounded_search.py        -- Gemini grounded search tool (cuttable)

EDIT:
  backend/llm/gemini.py                   -- grounded_search() provider method (if T-3)
  backend/main.py                         -- lifespan imports for the new tool(s)
  backend/config/settings.py             -- T-0b model restore + Day 25 search settings
  backend/prompts/system/50_tools.md      -- web_search + grounded_search directives
  backend/requirements.txt                -- tavily-python pin
  frontend/src/hooks/useWebSocket.ts      -- search_results event + sources state
  frontend/src/components/ChatPanel.tsx   -- render clickable sources block
  data/.gitignore (or root)               -- data/dropped/  (Day 24 carry-over)
  docs/journal.md                         -- Day 25 entry
```

Minimal diffs throughout — especially in `settings.py` (one-line model restore) and `conversation.py` (**no changes** — the tool-call loop already handles new tools and the MUTED re-check).

---

## 7. Commits

```
[ ] chore(deps): pin tavily-python in requirements
[ ] fix(config): restore gemini-2.5-flash summarizer model after quota reset
[ ] chore(git): ignore data/dropped/
[ ] feat(tools): web_search tool via Tavily with soft-error handling
[ ] feat(desktop): render search source links in chat panel
[ ] feat(llm): gemini grounded_search provider method            # if T-3
[ ] feat(tools): grounded_search tool via gemini grounding       # if T-3
[ ] docs(prompts): web_search + grounded_search directives in 50_tools.md
[ ] docs: day 25 journal + plan
```

---

## 8. Drop-cut order (if the day runs long)

Cut from the bottom:

1. `web_search` (Tavily) + UI links — **protect**; this is the day's reason for being.
2. `50_tools.md` directive for `web_search` — cheap, keep.
3. Mute-during-search verification — keep (it's just a test, no code).
4. `grounded_search` tool + provider method — **first to cut.** Tavily already answers weather/news; slip grounded search to a buffer day or Month 2. Removing it is two import lines + two files; nothing else depends on it.
5. `advanced` search depth → drop to `basic` if Tavily latency feels slow; revisit later.

A Day 25 that ships only `web_search` cleanly, with links in chat, fully meets the product need. `grounded_search` is polish.

---

## 9. Heads-up for Day 26 (App Launcher + Timers)

- **Day 26 scope:** `open_app(name)` from an `apps.yaml` whitelist via `subprocess.Popen`; `set_timer(minutes, label)` firing a `plyer` toast + spoken "your timer is done". Both are new tools — same 4-step pattern, two more registry entries.
- **Concurrent timers** need background tasks held by strong references (`self._inflight`-style) so they aren't GC-cancelled — the same gotcha called out in `voice-pipeline/SKILL.md`. A timer is a long-lived background task, not a request/response tool, so think about where it's owned.
- **`apps.yaml` paths are per-machine.** Use absolute paths; for Microsoft Store apps, `start shell:AppsFolder\<package-id>` (noted in the V1 fallback plan).
- If `grounded_search` was cut today, Day 26 has slack — fold it back in there if you want both search paths before the Day 28 demo script.
- Quick check before Day 26: re-run the T-0a model probe each morning until the demo — the free tier resets daily and you don't want a quota surprise mid-demo.
