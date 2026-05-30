# Project Status — Day 25

**Period covered:** Day 25 (Week 4, Day 6 — Web Search)
**Status:** Complete — both search tools shipped and verified. grounded_search live Gemini call blocked by free-tier daily quota (20 RPD gemini-2.5-flash exhausted by Day 25 testing); code path confirmed correct via mock. Will confirm live on Day 26 when quota resets.
**Environment:** Windows 11, Python 3.13.5, tavily-python==0.7.25, google-genai==2.6.0

> Checkpoint summary for Day 25: Jarvis can now reach the live web. Two search tools
> registered (tools: 9). web_search (Tavily) fully verified with real results.
> grounded_search (Gemini Google Search grounding) code-complete; live path blocked
> by quota today. Frontend shows clickable source links after every search. Read
> before Day 26 (app launcher + timers).

---

## 1. What was done

| Task | What landed | Status |
|---|---|---|
| T-0a — Model probe | gemini-2.5-flash: OK; gemini-2.0-flash: 429; gemini-flash-lite-latest: OK. | Done |
| T-0b — Restore summarizer model | `summarizer_model` restored to `"gemini-2.5-flash"` in settings.py. | Done |
| T-0c — Reduce stage end-to-end | 25 chunks → map stage ✅ → reduce stage ✅ → structured PaperSummary with all 6 fields. Day 24 item 9 closed. | Done |
| T-0d — gitignore `data/dropped/` | Already covered by root `data/` gitignore entry — no change needed. | Done |
| T-1 — Tavily SDK install + verify | `tavily-python==0.7.25`. `AsyncTavilyClient` present. `search()` accepts `max_results`, `search_depth`, `include_answer`. Result shape confirmed: `title`, `url`, `content` per result, top-level `answer` present. Pinned in requirements.txt. | Done |
| T-2 — `web_search` tool | `backend/tools/web_search.py`. Lazy singleton `AsyncTavilyClient`. 800-char content cap. `_broadcast_sources` side-effect via `backend.api.voice.manager` (local import to avoid circular). Soft-errors on all Tavily failures. Registered in main.py lifespan. | Done |
| T-3 — `grounded_search` tool | `GeminiProvider.grounded_search()` method added (isolated call, only `google_search` tool, no function tools). `get_gemini_provider()` accessor in router.py. `backend/tools/grounded_search.py` reuses `_broadcast_sources`. `grounding_model=gemini-flash-lite-latest` (preserves 2.5-flash quota). Registered in main.py. | Done |
| T-4 — Frontend source links | `search_results` event added to `VoiceEvent` union. `searchSources` state in App.tsx; cleared on `transcription_complete`. Sources block in `ChatPanel.tsx`: dim-cyan header, truncated clickable links, `target="_blank"`. TypeScript: 0 errors. | Done |
| T-5 — System prompt directives | `50_tools.md`: `web_search` directive (technical/research queries), `grounded_search` directive (everyday facts), blanket no-URL-spoken rule (cite by title only). | Done |
| T-6 — Settings additions | 4 new fields: `tavily_search_depth="advanced"`, `tavily_include_answer=True`, `web_search_content_cap=800`, `grounding_model="gemini-flash-lite-latest"`. | Done |
| T-7 — Smoke tests | tools registered: 9 ✅. web_search("latest ABL1 kinase inhibitor papers 2025"): 5 real PMC/2025 results, answer present, 5 sources broadcast ✅. Both tools soft-error on quota/bad-key with no crash ✅. grounded_search blocked by quota — mock verified error-handling path ✅. | Done (partial on grounded live call) |
| T-8 — Journal + status | This file. | Done |

---

## 2. Key decisions and non-obvious choices

### Decision A — `ws_manager` import: `backend.api.voice.manager`, not `backend.main`

The plan guessed `backend.main` for the WebSocket manager singleton. The actual import is:
```python
from backend.api.voice import manager as ws_manager
```
This is used in both `backend/main.py` and `backend/api/pdf.py`. The local import inside `_broadcast_sources` avoids a circular import at module load time — tools are imported during the lifespan block before the app object is fully constructed.

### Decision B — `grounding_model = "gemini-flash-lite-latest"` (changed from plan's `gemini-2.5-flash`)

The plan specified `gemini-2.5-flash` for grounding. Changed to `gemini-flash-lite-latest` because:
- Grounded search is for simple current-fact queries (weather, news) — flash-lite quality is adequate
- `gemini-2.5-flash` has a 20 RPD free-tier daily cap; Day 25 testing exhausted it during the reduce-stage verification (T-0c)
- Flash-lite has a separate quota bucket (15 RPM vs 20/day), so the two don't interfere
- Reversible in one line if flash-lite doesn't support the `google_search` built-in tool (confirm on Day 26)

### Decision C — Isolated call for `grounded_search`, not wired into `router.generate()`

Gemini's `google_search` grounding tool cannot reliably coexist with our function-calling tools in the same `generate_content` request. Attempting to combine them causes the tool-call loop to break. The `grounded_search` tool handler makes a **separate, function-tool-free** Gemini call via `GeminiProvider.grounded_search()` — a dedicated method that attaches only `google_search`. The router's `generate()` method is not used for this path.

### Decision D — `searchSources` cleared on `transcription_complete`, not on `assistant_message`

Sources from the previous search persist through the full turn (user speaks → STT → LLM → tool call → broadcast → reply). They are cleared when `transcription_complete` fires for the **next** turn — i.e., when the user starts a new query. This means sources stay visible while the assistant is speaking, which is the desired behavior.

---

## 3. Problems and resolutions

### Problem A — `/chat` endpoint doesn't trigger tool calls

**Symptom:** Sending "What are the latest ABL1 inhibitor papers?" to `POST /chat` returned a response with no `tool_call` log entry — Gemini answered from training data.
**Cause:** The `/chat` handler calls `get_router().generate(message)` with no `tools=` argument and no system prompt. Tool calling only lives in `ConversationOrchestrator._run_pipeline()` (the voice pipeline).
**Fix:** Not a bug — the `/chat` endpoint is a simple text endpoint, not the tool-calling path. Smoke tests done via direct handler invocation and will be confirmed via PTT on Day 26.

### Problem B — `grounded_search` quota blocked during smoke test

**Symptom:** `GeminiProvider.grounded_search()` raised `LLMRateLimitError` (429) — `gemini-2.5-flash` 20 RPD limit exhausted by T-0c summarization testing.
**Status:** Not a code bug. Error-handling path confirmed: tool returns `{"error": "...", "type": "LLMRateLimitError"}` as a soft-error dict. Will confirm live call on Day 26 after quota resets.

### Problem C — `grounding_model` probe was also 429

**Symptom:** Test to confirm `gemini-flash-lite-latest` supports `google_search` grounding also returned 429 (flash-lite quota separately hit by probe calls earlier in the day).
**Status:** Cannot confirm flash-lite grounding capability from today's testing. If it fails on Day 26, switch `grounding_model` back to `gemini-2.5-flash` in one line.

---

## 4. Heads-up for Day 26 (App Launcher + Timers)

### Confirm grounded_search works on Day 26

First thing on Day 26: re-run the T-0a model probe, then test a live `grounded_search` call:
```bash
python -c "
import asyncio
from backend.llm.router import get_gemini_provider
async def t():
    result = await get_gemini_provider().grounded_search('weather in Bangalore today')
    print('OK — sources:', len(result['sources']))
    print('text snippet:', result['text'][:100])
asyncio.run(t())
"
```
If it 429s on flash-lite, change `grounding_model` to `"gemini-2.5-flash"` in settings.py. If flash-lite raises a capability error (not a 429), that also means switch back.

### Day 26 scope: `open_app` + `set_timer`

- `open_app(name: str)`: looks up `name` in `backend/tools/apps.yaml` whitelist, launches via `subprocess.Popen`. Use absolute paths; for Microsoft Store apps, `start shell:AppsFolder\<package-id>` syntax.
- `set_timer(minutes: float, label: str)`: `asyncio.create_task` with a `plyer` toast on completion. **Strong reference required** — store tasks in a `_inflight: set[Task]` on the tool module (or on `app.state`) to prevent GC cancellation. A timer is a long-lived background task, not a request/response.
- Both are new tools: 4-step pattern, `tools registered: 11` expected.
- If `grounded_search` was not confirmed live today, Day 26 has slack for that verification.

### `data/dropped/` note

`data/` is already gitignored at the root. No separate entry needed. (Confirmed Day 25 T-0d.)

---

## 5. Verification checklist

```
1. tools registered: 9  ✅

2. python -c "from backend.tools.web_search import web_search; print('OK')"
   → "OK" + "registered tool: web_search"  ✅

3. python -c "from backend.tools.grounded_search import grounded_search; print('OK')"
   → "OK" + "registered tool: grounded_search"  ✅

4. web_search("latest ABL1 kinase inhibitor papers 2025", max_results=5):
   → results count: 5  ✅
   → answer present: True  ✅
   → content capped at ≤800 chars: True  ✅
   → broadcast fired with 5 sources  ✅
   → first result from PMC/2025  ✅

5. web_search with bad API key → {"error": ..., "type": ...} soft dict, no crash  ✅

6. grounded_search → LLMRateLimitError → soft dict {"error": ..., "type": "LLMRateLimitError"}, no crash  ✅

7. grounded_search live call (real Gemini response):
   → BLOCKED by quota today. Confirm on Day 26.  ⏳

8. TypeScript tsc --noEmit: 0 errors  ✅

9. search_results event in VoiceEvent union  ✅

10. Sources block renders in ChatPanel when sources.length > 0  ✅ (code verified; visual confirm on Day 26 via PTT)

11. 50_tools.md: web_search + grounded_search directives + no-URL rule present  ✅
```

---

## 6. Files changed this day

```
NEW:
  backend/tools/web_search.py               -- Tavily web search tool (core)
  backend/tools/grounded_search.py          -- Gemini grounded search tool
  docs/project_status/PROJECT_STATUS(DAY_25).md  -- this file

EDIT:
  backend/llm/gemini.py                     -- GeminiProvider.grounded_search() method
  backend/llm/router.py                     -- get_gemini_provider() accessor
  backend/main.py                           -- lifespan imports for web_search + grounded_search
  backend/config/settings.py               -- summarizer_model restored; 4 web-search settings added
  backend/prompts/system/50_tools.md       -- web_search + grounded_search + no-URL directives
  backend/requirements.txt                  -- tavily-python==0.7.25 pinned
  frontend/src/hooks/useWebSocket.ts        -- search_results event type added to VoiceEvent union
  frontend/src/App.tsx                      -- searchSources state; search_results handler; clear on transcription
  frontend/src/components/ChatPanel.tsx     -- SearchSource type; sources prop; Sources block with links
  docs/journal.md                           -- Day 25 entry
```

---

## 7. Commits

```
[ ] fix(config): restore gemini-2.5-flash summarizer model after quota reset
[ ] chore(deps): pin tavily-python==0.7.25 in requirements
[ ] feat(tools): web_search tool via Tavily with content cap and source broadcast
[ ] feat(llm): GeminiProvider.grounded_search() method + get_gemini_provider() accessor
[ ] feat(tools): grounded_search tool via Gemini Google Search grounding
[ ] feat(desktop): render search source links in ChatPanel; search_results WS event
[ ] docs(prompts): web_search + grounded_search directives; no-URL-spoken rule
[ ] docs: day 25 journal + plan + status
```
