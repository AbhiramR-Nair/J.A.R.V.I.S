# Project Status — Day 21

**Period covered:** Day 21 (Week 4, Day 2 — Project Memory Tools)
**Status:** Complete — all four tools shipped, voice-tested end-to-end, cross-project isolation verified.
**Environment:** Windows 11, Python 3.13.5, google-genai==2.6.0, gemini-flash-lite-latest (quota issue — see §3)

> Checkpoint summary for Day 21: what got built, why it was built that way, what went
> sideways, and what Day 22 needs to know. Read before Day 22.

---

## 1. What has been done

| Task | What landed | Status |
|---|---|---|
| Pre-flight — API verification | Read `sqlite_store.py`, `vector_store.py`, `projects.py` to confirm actual function signatures before writing any code | Done |
| T-1 — `list_projects` tool | `backend/tools/list_projects.py`; calls `sqlite_store.list_projects()`; maps `list[dict]` → `list[str]` with `(active)` marker on active project | Done |
| T-2 — `set_active_project` tool | `backend/tools/set_active_project.py`; calls `sqlite_store.set_active_project(name)`; get-or-create is handled atomically in `sqlite_store` (no new helper needed) | Done |
| T-3 — `log_to_project` tool | `backend/tools/log_to_project.py`; resolves active project via `get_active_project()`; writes to ChromaDB first (non-fatal if it fails), always writes to SQLite; importance hard-coded to 10 | Done |
| T-4 — `recall_from_project` tool | `backend/tools/recall_from_project.py`; resolves active `project_id`; calls `vector_store.search(query, project_id=..., k=settings.semantic_k)`; returns `list[str]` directly (no mapping needed — `vector_store.search` already returns strings) | Done |
| T-5 — Lifespan registration | Four import lines added to `backend/main.py` lifespan block; startup log reads `tools registered: 5` | Done |
| T-6 — System prompt directives | `backend/prompts/system/50_tools.md` updated with one directive sentence per tool; trigger phrases for each: `set_active_project` ("switch to / work on"), `list_projects` ("what projects / list"), `log_to_project` ("log this / note that / remember that"), `recall_from_project` ("what did we say / recall") | Done |
| T-7 — Status bar UI | `StatusBar.tsx`: `activeProject` prop + `[projectname]` chip between waveform and state label. `App.tsx`: `activeProject` state, mount fetch from `GET /projects`, `project_changed` WS event handler. `useWebSocket.ts`: `project_changed` event type added. `conversation.py`: broadcasts `project_changed` after successful `set_active_project` tool call | Done |
| T-8 — Voice grammar tests | All four tools fired correctly via PTT voice queries. Logs confirmed end-to-end path | Done |
| T-9 — Cross-project isolation | Beta "gadgets" fact did not appear in alpha recall query. ChromaDB per-project collection isolation is working | Done |
| Fix — Case-sensitive project names | `sqlite_store.set_active_project()` now does `name.strip().lower()` before INSERT — prevents STT variants like "Kinase"/"KINASE"/"kinase" from creating duplicate projects | Done |
| Fix — Status bar chip not showing | `API_BASE` was `http://localhost:8000`; Edge WebView2 resolves `localhost` → IPv6 `::1`, silently failing the REST fetch. Changed to `http://127.0.0.1:8000`. Added belt-and-suspenders: `voice.py` sends `project_changed` on WS connect so the chip initialises without any REST call | Done |
| Fix — DB cleanup | `scripts/cleanup_projects.py` written and run; deleted misheard duplicates (Kainez, Kines, Logics Alpha, general project); restored `general` as active | Done |
| Config — Gemini model | `settings.py` switched from `gemini-2.5-flash` → `gemini-flash-lite-latest` (quota issue — see §3) | Done |

---

## 2. Implementation strategy — the *why* behind non-obvious choices

### Option A for active project resolution (handler queries `sqlite_store` directly)

The plan offered two options: (A) each tool handler calls `sqlite_store.get_active_project()` itself, or (B) inject the active `project_id` into `registry.execute()`. Option A was chosen because:
- Keeps the registry generic — it knows nothing about projects
- Each tool is self-contained and independently testable
- The "extra DB hit" is a single indexed read on a local SQLite file — imperceptible
- Only `log_to_project` and `recall_from_project` need the active project; `list_projects` and `set_active_project` operate on projects themselves

### `sqlite_store.set_active_project` already handles get-or-create atomically

Pre-flight revealed that `sqlite_store.set_active_project()` already does `INSERT OR IGNORE` + deactivate-all + activate-target in one transaction. Decision 2 from the plan (whether to add a separate helper) was moot — the existing function was already correct.

### `vector_store.search` already returns `list[str]`

Pre-flight confirmed that `vector_store.search()` returns `list[str]` directly (the `documents[0]` slice is done inside the function). Decision 3 from the plan (mapping Chroma objects to strings) was also moot — no mapping needed in `recall_from_project`.

### `log_to_project`: ChromaDB non-fatal, SQLite always writes

The Day 6 design of `sqlite_store.save_memory()` accepts `chroma_id=None` for exactly this case. If ChromaDB embedding fails (network issue, Gemini embedding API quota), the text is preserved in SQLite and the tool still returns a confirmation. A `WARNING` is logged so the degraded state is visible.

### `project_changed` broadcast from orchestrator, not from tool handler

Coupling a tool handler to `ws_manager` would make tools non-portable and hard to test. Instead, `conversation.py` inspects the tool result after `registry.execute()`: if `fc_name == "set_active_project"` and the result is a `str` (success, not a soft-error `dict`), it broadcasts `{"type": "project_changed", "name": active["name"]}`. The tool stays clean; the WebSocket concern stays in the orchestrator.

### Project name normalization via `name.strip().lower()`

STT consistently mishears project names spoken aloud (e.g. "kinase" → "Kainez", "Kines"). Normalizing to lowercase at the storage boundary means the LLM can pass any casing and the correct project is found. This is the right place to enforce the invariant — callers don't need to know about it, and it applies uniformly to both the tool handler and the `POST /projects/active` API route that goes through the same `sqlite_store` function.

---

## 3. Problems faced and how they were handled

### Gemini 2.5 Flash free-tier quota: 20 RPD

**Symptom:** `primary unavailable, falling back to groq` on every query. Groq responded without tools, hallucinating project names.

**Root cause:** `gemini-2.5-flash` (preview model) has a free-tier cap of only **20 requests per day** — far below the 1500 RPD the project plan assumed (that figure applies to the older `gemini-1.5-flash`).

**Fix applied:** switched `gemini_model` and `gemini_model_heavy` in `settings.py` to `gemini-2.0-flash` first (1500 RPD free tier). That also hit quota, revealing the project's free-tier bucket was exhausted for the day across models.

**Workaround:** `gemini-flash-lite-latest` — the only model with available quota on the day. It responded correctly and handled all tool calls. This is a lighter model suitable for chat and tool-calling, but less capable than `gemini-2.0-flash` for complex reasoning.

**`gemini-1.5-flash` 404:** the `google-genai 2.6.0` SDK uses `v1beta` API path; `gemini-1.5-flash` (without a version suffix) returns 404 on that path. The model list shows only `gemini-2.x` and `gemini-3.x` variants as available.

### Case-sensitive project names creating duplicates

**Symptom:** saying "switch to kinase project" over multiple sessions created `kinase`, `Kainez`, `Kines` as separate DB rows.

**Root cause:** `set_active_project` passed the name to SQLite verbatim. STT transcribes the same word differently across recordings.

**Fix:** `name.strip().lower()` in `sqlite_store.set_active_project()` before any DB operation. All future switches normalize at the storage boundary.

**Cleanup:** `scripts/cleanup_projects.py` cascade-deleted the four misheard duplicates (with their associated memory/message/conversation rows) and restored `general` as active.

### Status bar chip not visible after Day 21 deployment

**Symptom:** `[general]` chip never appeared in the status bar despite `activeProject` state and conditional render being in place.

**Root cause:** `API_BASE` was set to `http://localhost:8000`. Edge WebView2 (the browser engine inside PyWebView on Windows) resolves `localhost` to `::1` (IPv6) for HTTP connections. Uvicorn binds only to `127.0.0.1` (IPv4). The REST fetch for `GET /projects` silently failed — caught by `.catch(() => {})` — leaving `activeProject` as `""` forever.

This was the same IPv6 issue already documented for WebSocket (the `WS_VOICE_URL` comment in `config.ts` explains it), but not yet applied to `API_BASE`.

**Fix:** changed `API_BASE` to `http://127.0.0.1:8000` in `frontend/src/api/config.ts`. Added a belt-and-suspenders path: `voice.py` now sends a `project_changed` event immediately after the `connected` handshake on every WS connection, so the chip initialises without needing a REST call at all.

**Implication for SettingsPanel and other REST calls:** any other `fetch(${API_BASE}/...)` calls in the frontend would have been silently failing in PyWebView for the same reason. The `API_BASE` fix resolves all of them.

### Cross-project isolation test: unexpected result explained

**Symptom:** while in "alpha" project, a `recall_from_project(query="gadgets")` returned content about "widgets" that seemed to come from the "Logics Alpha" project.

**Root cause — not an isolation bug:** this is the known mid-turn switch edge case (documented in Day 21 plan §"Watch out for"). The user said "Log this alpha-only fact about widgets" which STT heard as "Logics Alpha only fact about widgets". The LLM called `set_active_project("Logics Alpha")` (creating a new project) and then `log_to_project(...)`. At the end of that turn, `_persist_turn` stored the full conversation — including the text "Logics Alpha only fact about widgets" — in the **turn-start project** ("alpha"), not in "Logics Alpha". The recall query found this conversation text (semantically close to "gadgets") within alpha's own collection.

**Isolation verdict:** the beta "gadgets" fact did **not** appear in the alpha recall. ChromaDB collection-per-project isolation is working correctly.

---

## 4. Heads-up for Day 22 and beyond

### Switch Gemini model before Day 22 — this is critical

Day 22 starts PDF summarization, which makes many LLM calls (chunked summarization of a paper). `gemini-flash-lite-latest` may not produce the quality of structured output needed. Before starting Day 22:

1. **Check quota** by running:
   ```
   python -c "
   import asyncio
   from google import genai
   from backend.config.settings import get_settings
   s = get_settings()
   client = genai.Client(api_key=s.gemini_api_key)
   async def t():
       for m in ['gemini-2.0-flash', 'gemini-2.5-flash']:
           try:
               r = await client.aio.models.generate_content(model=m, contents='ping')
               print(f'OK  {m}')
           except Exception as e:
               print(f'NO  {m}: {str(e)[:60]}')
   asyncio.run(t())
   "
   ```
2. If `gemini-2.0-flash` is available → set `GEMINI_MODEL=gemini-2.0-flash` in `.env` (or update `settings.py`).
3. If `gemini-2.5-flash` is available → set `GEMINI_MODEL=gemini-2.5-flash` (better reasoning for PDF).
4. If both are still rate-limited → proceed with `gemini-flash-lite-latest` but expect lower summarization quality. Consider enabling billing on Google AI Studio (~$5/month at personal use volume ends the quota problem permanently).

### `gemini_model_heavy` is the same as `gemini_model` right now

Days 22–24 use `gemini_model_heavy` for PDF summarization (the `heavy` tier is meant for quality-critical calls). Currently both point to `gemini-flash-lite-latest`. Once a better model is available, update `gemini_model_heavy` separately from `gemini_model` — the heavy tier should use the most capable available model.

### Mid-turn switch edge case: conversation stored in wrong project

If the user says "switch to X and log Y" in one utterance, `log_to_project` correctly writes to project X (it calls `get_active_project()` fresh after the switch). However, `_persist_turn` runs with the **turn-start** `project_id`, so the user/assistant conversation lines for that turn are stored in the **previous** project's collection. This means:
- The `log_to_project` write is in the correct project (X)
- The conversation context (what the user said, what Jarvis replied) is in the wrong project (the one active when the turn started)

This causes minor false positives in `recall_from_project` (conversation text from the wrong project appears in semantic search). Acceptable for v1. **Month 2 fix:** re-fetch `get_active_project()` at the start of `_persist_turn` instead of using the turn-start `project_id`.

### STT project-name mishearing is a persistent risk

Even with lowercase normalization, STT can mishear a project name badly enough to create a new project instead of switching to the existing one (e.g. "kinase" → "kines" would now create a lowercase "kines" rather than switching to "kinase"). The normalization fix prevents case variants but not phonetic variants.

**Month 2 fix:** fuzzy-match the spoken name against existing project names before creating a new one. If similarity > threshold, switch to the existing project. If below threshold, create and confirm aloud: "Created new project 'kines' — did you mean 'kinase'?"

### `scripts/cleanup_projects.py` is a one-off utility, not production code

The cascade-delete script was written for today's DB cleanup. It is safe to re-run (deleting non-existent names is a no-op), but it hard-codes specific project names. If the DB ever needs cleanup again, update the `junk` tuple before running.

---

## 5. How to verify Day 21

```
1. Backend startup log: "tools registered: 5"

2. Voice: "What projects do I have?"
   → log: tool_call iter=0: list_projects({})
   → spoken: "general (active), kinase, ..."

3. Voice: "Switch to kinase project"
   → log: tool_call iter=0: set_active_project({"name": "kinase"})
   → UI chip updates to [kinase]

4. Voice: "Log this: T315I shows a 40-fold resistance shift"
   → log: tool_call iter=0: log_to_project({"content": "T315I..."})
   → SQLite: SELECT content, importance FROM memory ORDER BY id DESC LIMIT 1;
      → importance = 10, project_id = kinase's id

5. Voice: "What did we say about T315I?"
   → log: tool_call iter=0: recall_from_project({"query": "T315I"})
   → spoken: the logged fact

6. Cross-project isolation:
   → switch to project alpha → log an alpha-only fact
   → switch to project beta  → log a beta-only fact about a different topic
   → switch back to alpha → recall beta's topic → must return nothing / not surface beta fact
```

All checks confirmed passing on 2026-05-29.

---

## 6. Open items before Day 22

- [ ] **Check Gemini quota before starting** — switch `gemini_model` and `gemini_model_heavy` to `gemini-2.0-flash` or `gemini-2.5-flash` if available. Proceed with `gemini-flash-lite-latest` only if both are still rate-limited.
- [ ] **Consider enabling billing** on Google AI Studio before Day 22 PDF work begins — avoids hitting daily caps mid-session on a compute-heavy day.
- [ ] Install `pymupdf` if not already: `pip install pymupdf` (Day 22 dependency).
- [ ] Pick a test PDF tonight — a 10–20 page arxiv paper in your domain works best for the Day 22 chunked summarization prototype.

---

## 7. Files changed this day

```
NEW:
  backend/tools/list_projects.py          -- list all projects, marks active
  backend/tools/set_active_project.py     -- switch/create project by name
  backend/tools/log_to_project.py         -- write note to ChromaDB + SQLite
  backend/tools/recall_from_project.py    -- semantic search in active project
  scripts/cleanup_projects.py             -- one-off: delete misheard test projects
  docs/project_status/PROJECT_STATUS(DAY_21).md  -- this file

EDIT:
  backend/main.py                         -- 4 tool import lines in lifespan block
  backend/prompts/system/50_tools.md      -- 4 new directive sentences
  backend/memory/sqlite_store.py          -- name.strip().lower() in set_active_project
  backend/config/settings.py             -- gemini_model → gemini-flash-lite-latest
  backend/services/conversation.py        -- project_changed broadcast after set_active_project
  backend/api/voice.py                    -- project_changed sent on WS connect (chip init fix)
  frontend/src/api/config.ts              -- API_BASE localhost → 127.0.0.1 (IPv6 fix)
  frontend/src/hooks/useWebSocket.ts      -- project_changed event type added
  frontend/src/App.tsx                    -- activeProject state + fetch + event handler
  frontend/src/components/StatusBar.tsx   -- activeProject prop + [chip] display
```

---

## 8. Commits

```
[pending] feat(memory): normalize project names to lowercase in set_active_project
[pending] feat(tools): list_projects and set_active_project memory tools
[pending] feat(tools): log_to_project and recall_from_project memory tools
[pending] feat(ui): show active project in status bar with live WebSocket update
[pending] docs(prompts): tool directives for the four project-memory tools
[pending] config: switch to gemini-flash-lite-latest (free-tier quota exhausted)
[pending] fix(ui): active project chip not showing in status bar
[pending] docs: Day 21 project status (updated with chip fix)
```
