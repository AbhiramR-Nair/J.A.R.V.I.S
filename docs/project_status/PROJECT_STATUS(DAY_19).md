# Project Status — Day 19

**Period covered:** Day 19 (Week 3, Day 5 — Close-out + Day 20 Design)
**Status:** Complete — all deliverables shipped, all 7 verification checks passing.
**Environment:** Windows 11, Python 3.13.5, PyWebView 6.2.1, React 19 + Vite, google-genai==2.6.0, Gemini 2.5 Flash

> Checkpoint summary for Day 19: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch on Day 20. Read before Day 20.

---

## 1. What has been done

Day 19 closed Week 3 cleanly, tagged the release, and spent the afternoon doing the
design work that Day 20 implementation depends on. Four deliverables shipped.

| Task | What landed | Status |
|---|---|---|
| A-1 — Regression sweep | All Day 18 checks re-run manually. All passing. Idle CPU: 16% combined (above the < 10% target — documented honestly). | Done |
| A-2 — Journal entry | Day 18 CPU figure (16% combined) appended to the existing Day 18 journal entry. Entry already covered the two non-trivial bugs; only the idle CPU number was missing. | Done |
| A-3 — Tag v0.3.0-blob | Annotated tag `v0.3.0-blob` created and pushed to GitHub. Commit `ca52c3f` includes journal, day_19_plan.md, and the accumulated SKILL.md edits. | Done |
| B-1 — Gemini SDK verification | `google-genai==2.6.0` confirmed. Installed source read directly. Function-calling API shape documented: `FunctionDeclaration(parameters_json_schema=...)`, `types.Tool(function_declarations=[...])`, `response.function_calls` property, `Part.from_function_response(...)` for multi-turn. Full findings in `docs/plans/day_20_plan.md` §1. | Done |
| B-2 — Code re-read | `base.py`, `router.py`, `conversation.py` read in full. LLM call location (line 395), lock pattern, and MUTED re-check seams documented. Key finding: `LLMResponse.text: str` is a required field — the current shape cannot represent a tool-call response. This confirms Q2 (typed union) is necessary, not optional. | Done |
| B-3 — 8 design questions | All 8 questions answered and recorded in `docs/plans/day_20_plan.md` §3. See §2 below for the rationale behind each decision. | Done |
| B-4 — Stub files | `backend/tools/__init__.py` (singleton) and `backend/tools/registry.py` (full signatures + docstrings + `raise NotImplementedError()` bodies) written. Both import cleanly. | Done |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. `parameters_json_schema=` not `parameters=` in FunctionDeclaration

The B-1 SDK read revealed that `FunctionDeclaration` has two mutually exclusive fields
for describing tool arguments: `parameters` (expects `types.Schema`, Gemini's own schema
object type) and `parameters_json_schema` (accepts a raw Python dict following JSON
Schema conventions). The plan's Q4 decision to hand-write schemas becomes significantly
easier with `parameters_json_schema=` — no need to construct `types.Schema` objects,
just pass the dict directly. This is the single most Day-20-relevant finding from B-1.

### 2. Multi-turn `contents` list for tool-call iteration

The Gemini API's function-calling model is conversational: after a tool call, the client
must send back the full conversation history as a `contents` list — not just the tool
result. This means the tool-call loop in `conversation.py` must preserve
`response.candidates[0].content` (the model's function-call turn) and include it in the
next generate call. If this object is lost, Gemini returns an API error. Documented in
`day_20_plan.md` §5 as the primary downstream complication for Day 20.

### 3. Typed union replaces `LLMResponse` dataclass (Q2)

`LLMResponse` currently requires `text: str`. When Gemini returns a tool call there is
no text — reading `.text` raises or returns empty. The only clean fix is a discriminated
union: `TextResponse | ToolCallResponse`, switched on a `type: Literal[...]` field. This
change touches four files (`base.py`, `gemini.py`, `router.py`, `conversation.py`) and
must be committed atomically on Day 20.

### 4. Singleton stored in `backend/tools/__init__.py`, not in a tool module or `__main__`

Day 18 taught the hard lesson about `__main__` module identity. The registry singleton
lives in `backend/tools/__init__.py` — a module that is always imported as
`backend.tools` regardless of which file imports it. Every tool module does
`from backend.tools import registry` and gets the same object. This is the direct
application of the Day 18 lesson to the registry design.

### 5. `_validate_schema()` helper added beyond the plan spec

The plan specified a validation check as an "implementation note" under Q4. The stub
formalises this as a named private method `_validate_schema(name, schema)` rather than
inline logic inside `register()`. This keeps `register()` readable and makes the
validation independently testable on Day 20.

### 6. Lazy SDK import in `gemini_function_schemas()`

`registry.py` does not import `google.genai.types` at the module top level. The import
is deferred to inside `gemini_function_schemas()`. This keeps the registry importable in
any test context that does not have the SDK installed (e.g. unit tests for the registry
logic itself, run in a minimal venv). In production the SDK is always present, so the
deferred import is a one-time cost on the first tool-enabled generate call.

### 7. Tool errors: two-category split (Q7)

Hard errors (`ToolNotFoundError`, `ToolSchemaError`) re-raise out of `execute()` to the
orchestrator, which routes them to `_handle_error` and ERROR state. These represent the
LLM making a mistake at the structural level — a retry is unlikely to help.

Soft errors (any other exception from the handler) are caught inside `execute()` and
returned as `{"error": str(exc), "type": exc.__class__.__name__}`. The LLM receives this
as a tool result and can respond gracefully ("I wasn't able to find that file"). This
pattern means a network timeout in `web_search` never crashes the voice loop.

### 8. Tool-call loop slots between lines 395 and 417 of `conversation.py` (Q1)

The LLM call at line 395 and the SPEAKING transition at line 417 are separated by ~20
lines that persist the turn. The tool-call loop replaces that gap: iterate (call LLM →
check for tool call → execute → loop), then fall out with the final text. The MUTED
re-check runs before each LLM call inside the loop, not just at THINKING entry — this
ensures mute-during-tool-execution works correctly even for slow tools.

---

## 3. Problems faced and how they were handled

No problems faced today. The SDK source read was straightforward. The `parameters_json_schema`
vs `parameters` distinction was a genuine finding that required reading the installed
source (not the docs), but it was not a blocker — it clarified the implementation path
rather than complicating it.

The idle CPU figure (16% combined) is above the < 10% target from the original plan. This
was measured during A-1 and documented honestly in the journal. It is not a regression
from Day 18 — the same measurement on that day was ~13% on `msedgewebview2.exe` alone
(Day 15 journal). The RAF idle guard from Day 18 reduced the spike but has not hit the
target at rest. Not a Day 20 concern.

---

## 4. Heads-up: downstream complications to watch

### `LLMResponse` type change touches four files — keep Day 20 commit atomic

`base.py` (replaces dataclass with union), `gemini.py` (adds tool-call branch),
`router.py` (return type annotation update), `conversation.py` (switch on `response.type`)
must all ship in the same commit. A partial migration — e.g. `base.py` updated but
`conversation.py` still reading `.text` directly — will cause a runtime `AttributeError`
that is hard to debug under time pressure. Stage all four together before committing.

### `response.candidates[0].content` must be preserved between tool-call iterations

The multi-turn `contents` list requires the model's exact content object from the
previous turn. Reconstructing it from strings will cause a Gemini API error. In the
tool-call loop implementation, keep a `contents: list` variable that grows by two entries
per iteration (model's content + function response). Do not discard any entry.

### Gemini fallback (Groq LLM) cannot call tools

`router.py` will pass `tools=None` to the fallback provider. This is correct — Groq's
LLM API does not use the same function-calling protocol. When the fallback is hit during
a tool-heavy query, the user gets a text-only reply. This must not raise; the orchestrator
treats `TextResponse` from the fallback identically to `TextResponse` from Gemini.

### `_handle_error` requires the lock to be held (asserted at line 315)

Hard tool errors propagate out of the tool-call loop (which runs outside the lock) up to
`_process_turn`. The existing `_process_turn` catch block at line 353–356 re-acquires the
lock before calling `_handle_error`. This is already the correct pattern — do not add a
lock acquisition inside the tool-call loop before re-raising a hard error.

---

## 5. How to verify Day 19

```
1. git tag --list shows v0.3.0-blob.

2. git log --oneline -5 shows the week 3 close-out commit (ca52c3f).

3. docs/plans/day_20_plan.md exists and is at least 100 lines.
   (Actual: 175 lines.)

4. python -c "from backend.tools import registry; print(type(registry))"
   prints <class 'backend.tools.registry.ToolRegistry'>.

5. python -c "from backend.tools import registry; registry.register"
   does not raise on import or attribute access — the method exists,
   raises NotImplementedError only when called.

6. Full PTT voice loop still works: hold Alt+Space → ask a question →
   get a spoken reply. The stub package exists but is not imported by
   anything in the runtime path.

7. PROJECT_STATUS(DAY_19).md exists with all 8 sections present.
```

All 7 checks confirmed passing on 2026-05-28.

---

## 6. Open items before Day 20

- [ ] Implement `ToolRegistry.register()` against the stub docstring
- [ ] Implement `ToolRegistry._validate_schema()` — assert no $ref/$defs/allOf/oneOf/anyOf at top level
- [ ] Implement `ToolRegistry.gemini_function_schemas()` using `parameters_json_schema=` (B-1 finding)
- [ ] Implement `ToolRegistry.execute()` with the Q7 hard/soft error split
- [ ] Implement `ToolRegistry.__contains__` and `__len__`
- [ ] Update `backend/llm/base.py`: replace `LLMResponse` dataclass with `TextResponse | ToolCallResponse` discriminated union
- [ ] Update `backend/llm/gemini.py`: add tool-call branch (if `response.function_calls`, return `ToolCallResponse`)
- [ ] Update `backend/llm/router.py`: add `tools` kwarg; thread to primary; fallback skips tools
- [ ] Add `max_tool_calls: int = 5` to `backend/config/settings.py`
- [ ] Add tool-call loop to `conversation.py` THINKING stage (between lines 395–417) with MUTED re-check and lock pattern
- [ ] Build `get_current_time` as the first real tool — smoke test via voice: "what time is it?"
- [ ] Import tool modules explicitly in `backend/main.py` lifespan
- [ ] Once working: write `.claude/skills/tool-calling-pattern/SKILL.md`

---

## 7. Files changed this day

```
NEW:
  docs/plans/day_20_plan.md             — B-1 SDK findings, B-2 code notes, Q1-Q8 decisions
  docs/project_status/PROJECT_STATUS(DAY_19).md  — this file

EDIT:
  docs/journal.md                       — Day 18 idle CPU figure appended (16% combined)
  docs/plans/day_19_plan.md             — committed (was untracked from prior session)
  .claude/skills/project-architecture/SKILL.md  — accumulated edits committed
  backend/tools/__init__.py             — global registry singleton (was empty 0-byte placeholder)
  backend/tools/registry.py             — ToolRegistry stub with full signatures and docstrings
                                          (was empty 0-byte placeholder)
```

---

## 8. Commits

```
ca52c3f  chore: tag v0.3.0-blob; week 3 close-out
         (journal CPU figure, day_19_plan.md, SKILL.md edits)

[pending] docs: day 20 plan with design decisions for tool-calling registry
          feat(tools): registry stub with type signatures and docstrings

[pending] docs: Day 19 project status document
```
