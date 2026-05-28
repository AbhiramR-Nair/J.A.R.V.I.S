# Day 19 Plan — Week 3 Close-Out + Day 20 Design

**Period:** Day 19 (Week 3, Day 5 — buffer + Day 20 preparation)
**Shape:** Hybrid — short morning close-out + afternoon design block + end-of-day status doc
**Budget:** ~6 hrs (1.5 hrs morning + 4 hrs afternoon + 45 min end-of-day status)
**Environment:** Windows 11, Python 3.13.5, PyWebView 6.2.1, React 19 + Vite, Gemini 2.5 Flash

> Day 19 has three distinct parts. Morning ships Week 3 and tags the release.
> Afternoon spends 4 hours designing Day 20's tool-calling architecture *on
> paper* before any production code is written. End of day mirrors Day 18:
> a `PROJECT_STATUS_DAY_19.md` document capturing what shipped, the *why*
> behind each design decision, and what to watch in Day 20. The bias is:
> settle every hard-to-reverse decision today so tomorrow is straight
> implementation.

---

## 1. Goal

Close Week 3 cleanly, tag `v0.3.0-blob`, then resolve every Day 20 design
question before any tool-calling code is written. Three artefacts are
**created today** (none of them pre-exist):

1. `docs/plans/day_20_plan.md` — recording the SDK findings (B-1), code-shape
   notes (B-2), and the answers to all 8 design questions (B-3).
2. `backend/tools/registry.py` and `backend/tools/__init__.py` — stub files
   with type signatures, docstrings, and `raise NotImplementedError()`
   bodies (B-4). No working logic.
3. `PROJECT_STATUS_DAY_19.md` — end-of-day status doc mirroring the
   `PROJECT_STATUS_DAY_18_.md` structure (B-5).

The explicit non-goal is writing any tool-calling logic today. Per the
"write the docstring/signature yourself" rule in `CLAUDE.md`, today is the
docstring half of that workflow.

---

## 2. Prerequisites (verify before starting)

Quick sanity check at the very start. ~5 minutes.

- [ ] `git status` is clean (no leftover Day 18 work).
- [ ] On `main` branch, up to date with remote.
- [ ] Backend boots: `python -m backend.desktop` opens window, no errors in
      `data/logs/jarvis.log`.
- [ ] Frontend dev server is fine: `cd frontend && npm run dev` (or use the
      built static files if Day 17 baked them).
- [ ] Voice loop responds: hold Alt+Space, ask anything, hear reply.

If any of these fail, fix before starting Block A. A red Day 18 is not a
green Day 19.

---

## 3. Block A — Morning, Close Week 3 (~1.5 hrs)

### A-1 — Ad-hoc regression sweep (~15 min)

Re-run the Day 18 §5 checklist quickly. These all passed yesterday; one more
pass confirms nothing rotted overnight.

| Check | Pass criterion |
|---|---|
| Snap-to-corner | Drag near each of 4 corners, releases within 60px snap. |
| Snap persistence | Snap to top-right, quit via tray, relaunch, window appears top-right. |
| Tray hide/show | X button hides; tray icon stays; single-click brings window back. |
| Tray quit | Right-click → Quit fully exits (no `python.exe` / `msedgewebview2.exe` left). |
| Connection dot | Kill backend → dot turns amber within 1s; ~5s later red; restart → cyan. |
| Voice loop | Hold Alt+Space → ask "what time is it?" → spoken reply, all 7 states cycle. |
| CPU at idle | < 10% (Task Manager → Details → python.exe + msedgewebview2.exe combined). |

Record the actual CPU number in the journal entry (A-2). If anything fails,
file it as an open item in `docs/plans/day_19_plan.md` and continue.

### A-2 — Journal entry (~10 min)

Append to `docs/journal.md`. One-line discipline rule per the Day-by-Day
plan, but for Day 18 specifically a short paragraph is appropriate given the
two non-trivial bugs (PyWebView `moved` semantics, `__main__` module
identity). Capture in your own words so future-you remembers the lesson.

Template:

```
## 2026-05-28 — Day 18

Snap-to-corner, minimize-to-tray, connection dot all shipped. All 17 manual
tests passing. Two bugs worth remembering:
- PyWebView `moved` fires continuously during drag, not just at drag-end. Fixed
  with 150ms debounce + 500ms snap cooldown.
- `python -m backend.desktop` loads __main__ as `sys.modules['__main__']`, not
  `sys.modules['backend.desktop.__main__']`. Singleton state must live in a
  non-__main__ module.
Idle CPU: X.X%.
```

### A-3 — Tag and push the release (~10 min)

```bash
git tag -a v0.3.0-blob -m "Week 3 complete: blob, audio reactivity, window polish"
git push origin v0.3.0-blob
```

Verify on GitHub: the tag is visible under Releases / Tags.

Optional: add a one-paragraph release note on the GitHub tag page summarising
what landed in Weeks 1–3 (voice loop, blob, snap, tray). 10 minutes, helpful
for the eventual public release.

### A-4 — No Week 3 demo (deferred to Day 30)

Skipping per Day 19 decision. The full v1 demo video on Day 30 will cover
everything end-to-end and is the only demo recording that actually matters.

---

## 4. Block B — Afternoon, Day 20 Design (~4 hrs)

This block has four sub-blocks. They must run in order — B-3 depends on B-1
and B-2, B-4 depends on B-3.

### B-1 — Verify the Gemini SDK shape (~30 min)

The Gemini Python SDK has shifted API shape multiple times. Two distinct
packages exist with different function-calling APIs. `CLAUDE.md` rule 4
makes this verification mandatory before any new Gemini code.

**Step 1 — Identify which package is installed.**

```bash
grep -i -E "gemini|generativeai|google-genai" backend/requirements.txt
pip show google-genai 2>NUL
pip show google-generativeai 2>NUL
```

Likely outcomes:
- `google-generativeai` (older, 0.x or early 1.x). Function-calling API:
  pass Python functions directly OR `glm.Tool` objects with
  `FunctionDeclaration` list. Tools attached at model construction or per
  `generate_content()` call.
- `google-genai` (newer, the Gen AI SDK). Function-calling API: explicit
  `types.Tool(function_declarations=[types.FunctionDeclaration(...)])`
  passed via `types.GenerateContentConfig`, model called via
  `client.models.generate_content(...)`.

**Step 2 — Read the actual installed source.**

```bash
python -c "import google.genai; print(google.genai.__file__)"
# OR
python -c "import google.generativeai; print(google.generativeai.__file__)"
```

Open that path in VS Code. Grep for `Tool`, `FunctionDeclaration`,
`function_calling`, `tool_config`. Note the exact import paths and call
signatures *in the version you have*. Do not rely on blog posts or LLM
training data — both predate your installed version.

**Step 3 — Read your existing `backend/llm/gemini.py`.**

What does `generate()` currently do? Which SDK calls does it use? This is
the surface you will extend (or replace) on Day 20.

**Step 4 — Document findings.**

In `docs/plans/day_20_plan.md`, record:
- Installed package name and exact version.
- The current shape of how to pass `tools` to a generate call.
- The shape of the response when the model calls a function (where the
  function name / arguments appear in the response object).
- Any version-specific gotchas you find in the SDK source (deprecation
  warnings, parameter name changes, etc.).

### B-2 — Re-read existing code, take notes (~45 min)

Three files. Read carefully — don't skim. Take notes inside
`docs/plans/day_20_plan.md` under a "Current code shape" section.

**File 1: `backend/llm/base.py`** (~15 min)

- What is the abstract method signature of `BaseProvider.generate()`?
- What does it return today? (Plain string? Typed `LLMResponse`? Dict?)
- Are there any other methods (`stream`, `embed`, etc.) that need a similar
  tool-aware extension?

**File 2: `backend/llm/router.py`** (~15 min)

- How does the router pick between Gemini and OpenAI?
- Where does fallback happen? (Catch a specific exception type? Check rate
  limit headers?)
- Does it return the provider's response unchanged, or does it normalise?
- Where is the natural seam to add tool-call awareness?

**File 3: `backend/services/conversation.py`** (~15 min)

- Find the `_run_pipeline` method (or whatever name the THINKING stage
  uses).
- Locate the exact line where `llm_router.generate(...)` is called inside
  the THINKING stage.
- Re-read the surrounding lock pattern: where is `self._lock` acquired,
  where is it released?
- Re-read the `voice-pipeline/SKILL.md` §"Adding tool calls inside THINKING
  (Day 20)" — the rules there are binding. Tool execution must:
  - Run outside the lock.
  - Re-check `MUTED` between each tool call, not just at THINKING entry.
  - Raise on fatal errors so `_handle_error` can route to ERROR state.

### B-3 — Settle the 8 design questions (~2 hrs)

Each question gets an answer recorded in `docs/plans/day_20_plan.md`. The
recommendations below are my prior; push back on any you disagree with. The
goal is **a decision, with rationale, before any code is written.**

#### Q1 — Where does the tool-call loop live?

**Options:** `backend/llm/router.py`, `backend/services/conversation.py`
THINKING stage, or a new `backend/services/tool_loop.py`.

**Recommendation:** `conversation.py` THINKING stage. The
`voice-pipeline/SKILL.md` already documents the lock/MUTED-re-check pattern
around it. Adding the loop where state management already lives keeps
cancellation semantics correct and avoids a new module. Putting it in the
router would couple the router to the registry; putting it in a new module
adds a file without adding clarity.

**Tradeoff:** `conversation.py` will grow. Worth re-measuring file length
after Day 20 to decide whether a future split is needed.

#### Q2 — What does `BaseProvider.generate()` return when tools are in play?

**Options:**
- (a) Typed union: `LLMResponse = TextResponse | ToolCallResponse`.
- (b) New method `generate_with_tools(prompt, tools) -> ToolCallOrText`.
- (c) Provider handles the whole loop internally given a registry reference.

**Recommendation:** (a). Single LLM entry point, typed union the
orchestrator switches on. The orchestrator owns the loop (Q1), so providers
stay stateless. (c) couples providers to the registry, which makes testing
and provider-swapping painful.

**Implementation note:** Pydantic discriminated union with a `type: Literal["text"]`
/ `type: Literal["tool_call"]` field is the cleanest shape.

#### Q3 — Sync or async tool handlers?

**Options:** all async, all sync, or mixed with auto-wrapping in the registry.

**Recommendation:** all async. Per `CLAUDE.md` "async-first." Sync work
(`subprocess.Popen`, blocking file I/O) wraps itself in `asyncio.to_thread`
or `loop.run_in_executor`. Mirrors the existing pattern in
`conversation.py` (`_save_recording`, `recorder.start_recording`).

**Tradeoff:** A trivial sync tool (`get_current_time` returning a string)
has to be `async def` even though it does no I/O. Worth it for consistency.

#### Q4 — JSON Schema source-of-truth — hand-written or Pydantic-derived?

**Options:**
- Hand-written JSON Schema in the registration call.
- Auto-derived from Pydantic models via `.model_json_schema()`.

**Recommendation:** hand-written. Pydantic auto-derive emits `$defs`,
`title`, and other keys that the Gemini API rejects or silently ignores.
Six tools' worth of schema is small enough to write by hand, and
hand-writing keeps the call site readable.

**Implementation note:** Add a small `validate_schema()` helper that asserts
the registered schema follows the subset Gemini accepts (no `$ref`, no
`$defs`, no `allOf`/`oneOf`/`anyOf` at the top level). Fails loud at
registration, not at the first tool call.

#### Q5 — Provider-neutral or Gemini-only schemas in the registry?

**Recommendation:** store provider-neutral OpenAPI-style JSON Schema in the
registry. Add `gemini_function_schemas()` as a translator method that
converts to Gemini's expected shape on demand. An `openai_tool_schemas()`
translator is an hour's work later if/when OpenAI fallback needs tools.

**For Day 20:** only wire Gemini. The OpenAI fallback path can skip tools
for now — if Gemini is down, a basic chat reply without tools is acceptable
degraded behaviour.

#### Q6 — How are tools registered?

**Options:** decorator at module import, explicit registration in FastAPI
lifespan, or auto-discovery via folder scan.

**Recommendation:** decorator pattern with a global singleton.
- `backend/tools/__init__.py` exports `registry = ToolRegistry()`.
- Each tool module: `from backend.tools import registry`, then
  `@registry.register(name=..., description=..., parameters=...)`.
- FastAPI lifespan imports the tools package (`import backend.tools.web_search`
  etc.), which runs the decorators at import time.

**Tradeoff:** A tool that is never imported is never registered. The
lifespan needs an explicit list of tool modules to import. Acceptable —
keeps the side effect explicit.

#### Q7 — Tool errors: fatal or recoverable?

**Two categories needed:**

- **Hard errors** — raise to the orchestrator, route through `_handle_error`,
  state goes to ERROR:
  - `ToolNotFoundError`: LLM hallucinated a tool name not in the registry.
  - `ToolSchemaError`: arguments don't match the registered schema.
  - Max iterations exceeded (Q8).

- **Soft errors** — return as a tool result, feed back to LLM, let it react:
  - File not found.
  - Subprocess returns non-zero.
  - Network timeout from inside a tool's HTTP call.
  - Any other exception raised by the handler.

**Recommendation:** registry's `execute()` method catches all exceptions
from the handler. Distinguishes by exception type:
- If the exception is a `ToolNotFoundError` or `ToolSchemaError`, re-raise.
- Otherwise, log and return `{"error": str(exc), "type": exc.__class__.__name__}`
  as the tool result. The LLM sees this and can apologise or try again.

#### Q8 — Multi-tool safety cap of 5 — what happens at iteration 6?

**Recommendation:** after the 5th tool call, force a final LLM call with
`tools=None`. The LLM produces a text-only response summarising what it did
or apologising for getting stuck. Do not truncate silently — that leaves the
user with no spoken reply, which is worse than an apology.

**Implementation:**

```python
for iteration in range(settings.max_tool_calls):
    response = await llm_router.generate(prompt, tools=registry.gemini_function_schemas())
    if response.type == "text":
        return response.text
    # else execute tool, append result to prompt, loop
# Cap hit:
final = await llm_router.generate(
    prompt + "\n[max tool calls reached; reply in text only]",
    tools=None,
)
return final.text
```

### B-4 — Write the stub file (~45 min)

Two files to create. Type signatures, docstrings, and `raise NotImplementedError()`
bodies. **No working logic.**

#### File 1: `backend/tools/__init__.py`

Contains the global registry singleton:

```python
"""Tools package.

The global `registry` singleton is the entry point for all tool registration.
Tool modules import this and decorate their handlers.
"""
from backend.tools.registry import ToolRegistry

# Global singleton. Tool modules register against this at import time.
registry = ToolRegistry()
```

#### File 2: `backend/tools/registry.py`

The full shape (signatures only — write the docstrings yourself per CLAUDE.md
rule 1):

```python
"""Tool registry for LLM function calling.

See docs/plans/day_20_plan.md for the design decisions behind this module.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import BaseModel


# Tool errors --------------------------------------------------------------

class ToolError(Exception):
    """Base class for tool-related errors."""


class ToolNotFoundError(ToolError):
    """LLM called a tool name not present in the registry."""


class ToolSchemaError(ToolError):
    """Arguments passed to a tool did not match its registered schema."""


# Tool schema --------------------------------------------------------------

class ToolSchema(BaseModel):
    """Single tool registration entry.

    YOU WRITE: explanation of each field, why this shape, what `parameters`
    must contain (subset of JSON Schema Gemini accepts).
    """
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]

    model_config = {"arbitrary_types_allowed": True}


# Registry -----------------------------------------------------------------

class ToolRegistry:
    """Stores tool schemas and dispatches LLM-driven calls to handlers.

    YOU WRITE: brief paragraph on registration model, execution semantics,
    error categories (hard vs soft per Q7), provider-neutrality (Q5).
    """

    def __init__(self) -> None:
        # YOU WRITE: explain why this is a dict keyed by name and what
        # ordering / collision guarantees it offers.
        self._tools: dict[str, ToolSchema] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """Decorator. Register `name` against the decorated async handler.

        YOU WRITE: full docstring — args, returns, raises, example usage,
        what validation runs at registration time.
        """
        raise NotImplementedError()

    def gemini_function_schemas(self) -> list[dict[str, Any]]:
        """Translate registered tools into Gemini's function-declaration shape.

        YOU WRITE: docstring — exact output structure, why provider-specific
        translation is here vs in the provider.
        """
        raise NotImplementedError()

    async def execute(self, name: str, args: dict[str, Any]) -> Any:
        """Dispatch `name` to its handler with `args`.

        YOU WRITE: docstring covering hard vs soft error handling (Q7), what
        gets re-raised, what gets wrapped into `{"error": ...}`, why logging
        happens here.
        """
        raise NotImplementedError()

    def __contains__(self, name: str) -> bool:
        """Allow `if name in registry:` checks."""
        raise NotImplementedError()

    def __len__(self) -> int:
        """Number of registered tools."""
        raise NotImplementedError()
```

**Important:** every method needs a CLAUDE.md-style explanation comment
block above it before you finish today. Those comments are what your
future self will read in 4 weeks. Don't skip them.

### B-5 — Write end-of-day project status (~45 min)

Mirror the Day 18 status doc. File: `PROJECT_STATUS_DAY_19.md` (top-level
or wherever Day 18's lives — keep the convention consistent). Length target
~150–200 lines; Day 18 was 214.

Use the same 8 sections as Day 18:

| § | Section | What goes here for Day 19 |
|---|---|---|
| 1 | What has been done | Table of A-1 through B-4 with status. Same shape as Day 18 §1. |
| 2 | Implementation strategy (the *why*) | One subsection per Q1–Q8 design decision. Capture the rationale you used, not just the answer. Day 18 §2 is the bar. |
| 3 | Problems faced and how they were handled | Anything that surprised you during B-1 (SDK shape) or B-2 (code re-read). If everything was smooth, write "no problems faced today" — do not invent drama. |
| 4 | Heads-up: downstream complications to watch | What will be tricky in Day 20 implementation, given today's decisions. E.g., "Q2's typed-union shape will require touching `BaseProvider`, `GeminiProvider`, and `_run_pipeline` together — keep the commit atomic." |
| 5 | How to verify Day 19 | Copy from §7 of *this* plan; the 7 verification checks. |
| 6 | Open items before Day 20 | Copy from §8 of this plan. |
| 7 | Files changed this day | `NEW:` and `EDIT:` lists. Should match the commit plan in §10. |
| 8 | Commits | Copy from §10 of this plan, with actual commit hashes once made. |

Write this **at the end of the day**, after the first three commits in §10
are made (the fourth commit *is* this status doc). The doc serves two
purposes: a record for future-you, and morning fuel for Day 20 (the
open-items section becomes Day 20's starting point).

**Rule going forward (one Day 18 borrowed):** if a "problems faced" section
would force you to fabricate a problem you didn't actually hit, leave it
empty. The status doc is a record, not a performance.

---

## 5. Explicitly Out of Scope Today

- Any working tool-calling logic.
- Gemini SDK integration code in `backend/llm/gemini.py`.
- Building the `get_current_time` test tool.
- Wiring the registry into `backend/services/conversation.py`.
- Writing `.claude/skills/tool-calling-pattern/SKILL.md` (Day 20 deliverable
  per the plan — written *after* the pattern is proven by working code, not
  before).
- Implementing `_handle_error` changes for tool-call failures.
- Any new `BaseSettings` fields beyond what's needed for Q8's
  `max_tool_calls` (default 5).

If you finish B-1 through B-4 with time to spare, the right move is to read
the current Gemini function-calling docs against the SDK version you
identified in B-1 — not to start writing registry logic. Tomorrow morning
is the right moment for the first real line.

---

## 6. End-of-Day Deliverables Checklist

- [ ] `v0.3.0-blob` tag pushed to GitHub.
- [ ] `docs/journal.md` updated with Day 18 entry.
- [ ] `docs/plans/day_19_plan.md` (this file) committed under `docs/plans/`.
- [ ] `docs/plans/day_20_plan.md` written, containing:
  - [ ] Installed Gemini SDK package name and version.
  - [ ] Current `BaseProvider.generate()` return shape.
  - [ ] Answers to Q1 through Q8 with rationale.
  - [ ] Notes on existing `router.py` and `conversation.py` shape (B-2 findings).
- [ ] `backend/tools/__init__.py` committed (singleton).
- [ ] `backend/tools/registry.py` committed (stub with signatures, docstrings,
      `raise NotImplementedError()` bodies).
- [ ] `PROJECT_STATUS_DAY_19.md` written and committed, mirroring the 8-section
      structure of `PROJECT_STATUS_DAY_18_.md`.
- [ ] You can answer all 8 design questions out loud without looking at the doc.

---

## 7. Verification Criteria

Day 19 is "done" when:

1. `git tag --list` shows `v0.3.0-blob`.
2. `git log --oneline -5` shows the three commits from §10.
3. `docs/plans/day_20_plan.md` exists and is at least 100 lines.
4. `python -c "from backend.tools import registry; print(type(registry))"`
   prints `<class 'backend.tools.registry.ToolRegistry'>` (the stub imports
   cleanly).
5. `python -c "from backend.tools import registry; registry.register"`
   does not raise on import or attribute access — the method exists, it
   just raises `NotImplementedError` when called.
6. Running the existing voice loop still works end-to-end (the stub package
   exists but is not yet imported by anything in the runtime path).
7. `PROJECT_STATUS_DAY_19.md` exists with all 8 sections present (even if
   §3 "Problems faced" is empty).

---

## 8. Open Items Going Into Day 20

The Day 20 plan should explicitly track:

- [ ] Implement `ToolRegistry.register()` against the stub docstring.
- [ ] Implement `ToolRegistry.gemini_function_schemas()` using the SDK shape
      identified in B-1.
- [ ] Implement `ToolRegistry.execute()` with the Q7 error split.
- [ ] Update `backend/llm/base.py`: change `generate()` return to the typed
      union from Q2.
- [ ] Update `backend/llm/gemini.py` to produce the typed union — text vs
      tool-call branch.
- [ ] Add `max_tool_calls: int = 5` to `backend/config/settings.py`.
- [ ] Add the tool-call loop to `conversation.py` THINKING stage with the
      lock/MUTED re-check pattern documented in `voice-pipeline/SKILL.md`.
- [ ] Build `get_current_time` as the first real tool — smoke test via voice.
- [ ] Once working: write `.claude/skills/tool-calling-pattern/SKILL.md`.

---

## 9. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Gemini SDK version is wildly different from what blog posts describe (Q1 answer changes). | Medium — the new `google-genai` SDK reshaped the function-calling API. | B-1 is mandatory before any other afternoon work. Read the installed source, not external docs. |
| Q1/Q2 decisions feel wrong once you start writing the stub. | Low if B-1 and B-2 are done thoroughly. | Stop and re-decide. Day 20 implementation against wrong design is far more expensive than re-deciding now. |
| Afternoon block runs over budget. | Medium — design days tend to expand. | If B-3 hits 2.5 hrs and is not done, write down what's settled, mark the rest as "open in day_20_plan.md", move on. Better to start Day 20 with 6 of 8 questions answered than to skip B-4 entirely. |
| Tagging `v0.3.0-blob` reveals an issue from the regression sweep that wasn't caught yesterday. | Low — Day 18 had all 17 checks passing. | If something is genuinely broken, tag anyway (it represents real Week 3 state), then file an issue and decide whether to fix today or defer to Day 30 polish. |

---

## 10. Commit Plan

Four logical commits:

```
chore: tag v0.3.0-blob; week 3 close-out

docs: day 20 plan with design decisions for tool-calling registry

feat(tools): registry stub with type signatures and docstrings
  - backend/tools/__init__.py: global registry singleton
  - backend/tools/registry.py: ToolRegistry stub with NotImplementedError
  - Day 20 will implement against these signatures

docs: Day 19 project status document
```

---

## 11. Notes for Future-Self

- The 8 design questions are the actual value of this day. The stub file is
  the deliverable that proves the questions were settled.
- If on Day 20 a question's answer feels wrong, that is fine — change it.
  But change it *deliberately*, with a one-line note in `day_20_plan.md`
  recording what shifted and why. Drift without record is how design erodes.
- The `voice-pipeline/SKILL.md` §"Adding tool calls inside THINKING (Day 20)"
  is the binding contract for how the tool-call loop interacts with the
  state machine. Re-read it before writing the loop tomorrow.
- The Gemini SDK shape findings in B-1 will be referenced repeatedly over
  Days 21–26. Make those notes good — at least one paragraph each on (a)
  how to register tools, (b) how to read a function-call response, (c) how
  to send a function result back.
