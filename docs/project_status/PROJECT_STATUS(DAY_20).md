# Project Status — Day 20

**Period covered:** Day 20 (Week 4, Day 1 — Tool-Calling Implementation)
**Status:** Complete — all deliverables shipped, voice smoke test passing.
**Environment:** Windows 11, Python 3.13.5, google-genai==2.6.0, Gemini 2.5 Flash

> Checkpoint summary for Day 20: what got built, why it was built that way, what went
> sideways, and what Day 21 needs to know. Read before Day 21.

---

## 1. What has been done

| Task | What landed | Status |
|---|---|---|
| Prerequisites — clean tree | Two pending Day 19 commits made before implementation started | Done |
| A-1 — `max_tool_calls` setting | Added to `backend/config/settings.py` as `max_tool_calls: int = 5` | Done |
| A-2 — `LLMResponse` discriminated union | `base.py`: replaced dataclass with `TextResponse \| ToolCallResponse`; both are Pydantic models with `type: Literal[...]` discriminator | Done |
| A-3 — Gemini tool-call branch | `gemini.py`: added `tools` kwarg; branches on `response.function_calls`; returns `ToolCallResponse` or `TextResponse`; `raw=response` on both | Done |
| A-4 — Router `tools` kwarg | `router.py`: threads `tools` to primary; fallback always gets `tools=None`. `groq_llm.py`: signature updated to accept (and ignore) `tools`; `str \| list` prompt handling added; return type fixed to `TextResponse` | Done |
| A-5 — Smoke test: no regression | Voice loop still works with `tools=None` (no registry tools yet). All four files committed atomically. | Done |
| B-1 — `_validate_schema()` | Rejects `$ref/$defs/allOf/oneOf/anyOf`; enforces `type=object` and `properties` key; fails at registration time not call time | Done |
| B-2 — `register()` decorator | Async enforcement via `asyncio.iscoroutinefunction`; duplicate collision check; stores `ToolSchema` in `self._tools` | Done |
| B-3 — `gemini_function_schemas()` | Lazy `google.genai.types` import; uses `parameters_json_schema=` (Day 19 B-1 finding); single `Tool` with all declarations | Done |
| B-4 — `execute()` | Hard/soft error split (Q7); `TypeError` promoted to `ToolSchemaError`; soft errors returned as `{"error": ..., "type": ...}` | Done |
| B-5 — Dunder methods | `__contains__` and `__len__` implemented | Done |
| B-6 — Registry smoke test | Throwaway script confirmed: register, `__len__`, `__contains__`, `gemini_function_schemas()`, `execute()`, hard error path, schema validation path | Done |
| C-1 — Re-read binding rules | Voice-pipeline SKILL.md §"Lock pattern" and §"Adding tool calls" reviewed | Done |
| C-2/C-3 — Tool-call loop | `conversation.py`: replaced single LLM call with loop (up to `max_tool_calls` iterations); MUTED re-check before each; `raw.candidates[0].content` preserved exactly; hard errors propagate; Q8 fallback fires final `tools=None` call | Done |
| C-4 — Hard error routing | Verified hard errors propagate to `_process_turn` catch block → `_handle_error` without lock conflict | Done |
| C-5 — Voice regression check | "What is the capital of France?" → spoken reply, single LLM call, zero tool calls in logs | Done |
| D-1 — `get_current_time.py` | First real tool; `@registry.register` decorator; returns `datetime.now().isoformat(timespec="seconds")`; no-arg schema | Done |
| D-2 — Lifespan import | `import backend.tools.get_current_time # noqa: F401` added to `backend/main.py` lifespan; startup logs `tools registered: 1` | Done |
| D-3 — System prompt | `backend/prompts/system/50_tools.md` created; one directive sentence per tool category | Done |
| D-4 — Voice smoke test | "What time is it?" → `tool_call iter=0: get_current_time({})` → `tool_result: get_current_time → '2026-...'` → spoken time. Full path confirmed in logs. | Done |
| E-1 — `tool-calling-pattern/SKILL.md` | 4-step pattern, JSON Schema rules, hard/soft error reference, MUTED rule, multi-turn `contents` rule, worked example, 7 gotchas, update policy. ~230 lines. | Done |
| E-2 — `PROJECT_STATUS(DAY_20).md` | This file | Done |

---

## 2. Implementation strategy — the *why* behind non-obvious choices

### `str | list` prompt signature across all providers

The plan's pseudocode passed a `list[types.Content]` directly to `self._llm.generate()`.
The router and providers originally accepted only `str`. Rather than adding a separate
`generate_multi_turn()` method (which would duplicate error handling), all four
`generate()` signatures were widened to `str | list`. Gemini passes this straight to
`contents=` (the SDK accepts both). Groq's fallback path flattens the list to text if
it ever receives one — a best-effort response for the very rare case of Gemini failing
mid-tool-loop.

### Lazy `from google.genai import types` in `conversation.py`

The tool-call loop needs `types.Content` and `types.Part` to build the multi-turn
contents list. Rather than adding a top-level SDK import to the orchestrator (which
has no other Gemini dependency), the import is local to `_run_pipeline`. This keeps
the orchestrator testable without the SDK present and makes the dependency explicit
and local to where it's actually used.

### `final_response` tracks the last `LLMResponse` for `_persist_turn`

The original code used `llm_response.provider` / `llm_response.model` for the persist
call. With the loop, the last response could be a `ToolCallResponse` (on the break) or
a `TextResponse` from the Q8 fallback. A separate `final_response` variable is updated
on each iteration and used for the provider/model fields, avoiding a potential
`AttributeError` if the loop structure changed.

### System prompt in `50_tools.md`, not appended to an existing file

The existing system prompt files are numbered `00_` through `40_`. Adding a `50_tools.md`
keeps tool directives isolated and easy to extend — each tool in Days 21–26 gets one
sentence added here, not buried in a monolithic prompt file. The loader assembles all
`*.md` files in alphabetical order, so `50_tools.md` appears after `40_safety.md` as
intended.

### `isinstance(llm_response, TextResponse)` over `llm_response.type == "text"`

Both work with the discriminated union. `isinstance` was chosen because it's type-safe:
a future refactor that accidentally removes the `type` field would still be caught by
the type checker, whereas a string comparison would silently become `False`. Minor, but
correct.

---

## 3. Problems faced and how they were handled

### Groq provider still returned `LLMResponse` (old dataclass, now removed)

Block A-3 updated Gemini to return `TextResponse`, but the Groq provider still had
`return LLMResponse(...)` — the old dataclass that no longer exists. This was caught
during the import chain verification (Block A-5 smoke test) before any voice testing.
Fixed by importing `TextResponse` in `groq_llm.py` and updating the return statement.

### Groq needed `str | list` signature too — not just the primary path

The router's fallback calls `self.fallback.generate(prompt, tools=None)` using the same
`prompt` argument. If the orchestrator passes a `list[types.Content]` and Gemini fails,
the router would pass that list to Groq. Without updating Groq's signature, this would
raise a `TypeError` (not caught by `except LLMError`) and propagate as an unhandled
exception. Fixed by widening Groq's signature and adding the list-flattening path.

### No other problems. The implementation followed the design doc closely.

---

## 4. Heads-up: what makes Day 21 tricky

Day 21 builds four project-memory tools: `set_active_project`, `list_projects`,
`log_to_project`, `recall_from_project`. These are harder than `get_current_time`
for three reasons:

1. **They touch SQLite and ChromaDB.** Unlike the time tool, errors here are soft
   (DB locked, ChromaDB timeout) but need to be returned as informative strings, not
   raw exception messages. The LLM needs to know "that project doesn't exist" vs
   "couldn't connect to DB."

2. **They need `project_id` context, not just the project name.** The orchestrator
   currently passes a `project_id: int` to `_run_pipeline`. The tool handlers need
   to resolve a human-readable name ("kinase project") to an integer ID via SQLite.
   Decide early: resolve in the tool handler itself, or inject the active project ID
   into the registry somehow. The cleanest approach for v1: tool handler queries
   SQLite to resolve the name, using the same `sqlite_store` module the orchestrator
   uses.

3. **System prompt update for all four tools at once.** `50_tools.md` needs four
   directive sentences — one per tool, covering: "switch to X project", "log this:
   ...", "what did we say about X?", "what projects do I have?". Each must be
   directive enough that the LLM calls the right tool for the right phrase.

4. **Cross-project isolation must hold.** The Day 6 invariant: every ChromaDB read/write
   scopes to the active `project_id`. The `recall_from_project` tool is the first voice
   path that touches ChromaDB. Verify isolation by: creating two projects, logging to
   each, then querying one and confirming results from the other don't appear.

---

## 5. How to verify Day 20

```
1. python -c "from backend.tools import registry; print(len(registry))"
   -- Before any tool import: prints 0.
   -- After: import backend.tools.get_current_time; prints 1.

2. Backend startup log includes: "tools registered: 1"

3. Voice query "what time is it?" produces in data/logs/jarvis.log:
   - tool_call iter=0: get_current_time({})
   - tool_result: get_current_time -> '2026-...'
   - SPEAKING transition -> TTS

4. Voice query "hello, how are you?" produces a single-LLM-call path
   (no tool_call lines in logs) — LLM correctly routes to text-only.

5. git log --oneline -6 shows 4 Day 20 commits + 2 prior Day 19 commits.

6. .claude/skills/tool-calling-pattern/SKILL.md exists and is >= 100 lines.

7. PROJECT_STATUS(DAY_20).md exists with all 8 sections.

8. data/logs/jarvis.log contains no unexpected ERROR lines from today.
```

All checks confirmed passing on 2026-05-28.

---

## 6. Open items before Day 21

- [ ] Implement `set_active_project(name: str) -> str` tool
- [ ] Implement `list_projects() -> list[str]` tool
- [ ] Implement `log_to_project(content: str) -> str` tool (importance=10, always save)
- [ ] Implement `recall_from_project(query: str) -> list[str]` (semantic search, active project)
- [ ] Update `backend/prompts/system/50_tools.md` with directives for all four tools
- [ ] Voice grammar tests: "switch to kinase project", "log this: ...", "what did we say about T315I?", "what projects do I have?"
- [ ] UI: show active project name in status bar (currently only visible via API)
- [ ] Verify cross-project isolation still holds (Day 6 invariant)

---

## 7. Files changed this day

```
NEW:
  backend/tools/get_current_time.py              -- first real tool
  backend/prompts/system/50_tools.md             -- tool directives for system prompt
  .claude/skills/tool-calling-pattern/SKILL.md   -- 4-step pattern + reference
  docs/project_status/PROJECT_STATUS(DAY_20).md  -- this file

EDIT:
  backend/config/settings.py   -- added max_tool_calls = 5
  backend/llm/base.py          -- LLMResponse -> TextResponse | ToolCallResponse union;
                                  prompt: str | list in BaseProvider
  backend/llm/gemini.py        -- tools kwarg; branch on response.function_calls;
                                  prompt: str | list
  backend/llm/router.py        -- tools kwarg threaded; prompt: str | list
  backend/llm/groq_llm.py      -- tools kwarg (ignored); prompt: str | list with
                                  list-flatten path; LLMResponse -> TextResponse return
  backend/tools/registry.py    -- all 5 methods + 2 dunders implemented (was stubs)
  backend/services/conversation.py -- tool-call loop replacing single LLM call;
                                       added imports for TextResponse, registry,
                                       ToolNotFoundError, ToolSchemaError
  backend/main.py              -- tool module import in lifespan; startup log
```

---

## 8. Commits

```
67fbce0  feat(tools): registry stub with type signatures and docstrings  [Day 19]
fe98e85  docs: day 20 plan and Day 19 status document                    [Day 19]
b1cf924  feat(llm): discriminated union for LLMResponse to support tool calls
d733edf  feat(tools): implement ToolRegistry with JSON Schema validation
6ccebd2  feat(conversation): tool-call loop in THINKING stage with MUTED re-check
8619851  feat(tools): get_current_time tool + lifespan registration + system prompt
[pending] docs: tool-calling-pattern SKILL.md + Day 20 status document
```
