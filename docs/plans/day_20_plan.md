# Day 20 Plan — Tool-Calling Implementation

**Period:** Day 20 (Week 4, Day 1)
**Shape:** Full implementation day — LLM layer → Registry → Orchestrator → First tool → Docs
**Budget:** ~6 hrs (1 hr LLM + 1.5 hrs registry + 1.5 hrs orchestrator + 1 hr tool/smoke + 1 hr docs)
**Environment:** Windows 11, Python 3.13.5, google-genai==2.6.0, Gemini 2.5 Flash

> Day 20 is the most architecturally important day of Week 4. Every Week 4 feature
> (memory tools, PDF summariser, web search, app launcher, timers) hangs off the
> registry you ship today. The design work was settled yesterday — today is
> straight implementation against the stubs and decisions captured in the
> existing `day_20_plan.md` design doc and `PROJECT_STATUS(DAY_19).md`.
>
> The execution order matters. Build bottom-up: LLM layer first (it has no
> upward dependencies), registry second (depends on LLM types), orchestrator
> third (depends on both), first tool fourth (validates the whole stack via
> voice), documentation last. **Do not skip ahead.** Wiring the orchestrator
> before the LLM union is migrated will break the voice loop and leave you
> debugging four files at once.

---

## 1. Goal

Ship a working tool-calling architecture end-to-end: hold Alt+Space, ask
"what time is it?", and hear the assistant call the `get_current_time` tool
and speak the result. Logs must show the full path: LLM → tool_call →
tool_result → LLM → text → TTS.

Three artefacts ship by end of day:

1. Working tool-calling stack across `base.py`, `gemini.py`, `router.py`,
   `registry.py`, `conversation.py`, `get_current_time.py`, and `main.py`
   lifespan.
2. `.claude/skills/tool-calling-pattern/SKILL.md` — the reference doc you
   will use 5+ times in Days 21–26 to add new tools quickly.
3. `PROJECT_STATUS(DAY_20).md` — end-of-day status mirroring Days 18/19.

The explicit non-goals are Q21 work (project memory tools), wake-word, and
any tool beyond `get_current_time`. Today is the chassis, not the cargo.

---

## 2. Prerequisites (verify before starting)

Quick sanity check at the very start. ~5 minutes.

- [ ] `git status` is clean.
- [ ] On `main` branch, up to date with remote.
- [ ] `git log --oneline -5` shows the Day 19 commits (close-out tag,
      design doc, registry stub, status doc).
- [ ] `python -c "from backend.tools import registry; print(type(registry))"`
      prints `<class 'backend.tools.registry.ToolRegistry'>`.
- [ ] Backend boots: `python -m backend.desktop` opens the window, no errors
      in `data/logs/jarvis.log`.
- [ ] Voice loop still works: hold Alt+Space, ask anything, hear reply.
- [ ] `docs/plans/day_20_plan.md` (the design doc) is at hand. You'll
      reference §1 (SDK shape) and §3 (Q1-Q8 decisions) repeatedly today.

If any of these fail, stop and fix before writing implementation code.

---

## 3. Design summary (cite-only — full rationale in design doc)

These are settled — do not re-litigate during implementation. If a decision
genuinely needs to change, edit the design doc with a one-line note and
proceed. Drift without record is how design erodes.

| Q | Decision | Reference |
|---|---|---|
| Q1 | Tool-call loop lives in `conversation.py` THINKING stage, between lines 395 and 417 | design §3.Q1 |
| Q2 | `LLMResponse` becomes discriminated union: `TextResponse \| ToolCallResponse` | design §3.Q2 |
| Q3 | All tool handlers are `async def`; sync work wraps in `asyncio.to_thread` | design §3.Q3 |
| Q4 | Hand-written JSON Schema dicts; `_validate_schema()` rejects `$ref`/`$defs`/`allOf`/`oneOf`/`anyOf` | design §3.Q4 |
| Q5 | Provider-neutral schema stored; `gemini_function_schemas()` translates to `list[types.Tool]` | design §3.Q5 |
| Q6 | Decorator registration; singleton in `backend/tools/__init__.py`; explicit imports in lifespan | design §3.Q6 |
| Q7 | Hard errors (`ToolNotFoundError`, `ToolSchemaError`) re-raise; soft errors return `{"error": ..., "type": ...}` as tool result | design §3.Q7 |
| Q8 | After 5 iterations, force a final LLM call with `tools=None` and an appended note | design §3.Q8 |

**Three binding rules from voice-pipeline SKILL.md** (must hold in Block C):

1. Tool execution runs **outside** `self._lock`.
2. Re-check `MUTED` before **each** LLM call inside the loop — not just at THINKING entry.
3. Hard errors propagate up to `_process_turn`, which re-acquires the lock before calling `_handle_error`.

---

## 4. Block A — LLM Layer Foundation (~1 hr)

Why first: registry imports `types.FunctionDeclaration` types that depend on
having the SDK working. Conversation switches on `response.type` which
depends on the union being defined. Everything upward depends on the LLM
layer being in its Day 20 shape.

### A-1 — Add `max_tool_calls` to settings (~5 min)

**File:** `backend/config/settings.py`

Add one field to the `Settings` class:

```python
max_tool_calls: int = 5  # Q8 — safety cap on tool-call loop iterations
```

Verify load: `python -c "from backend.config.settings import settings; print(settings.max_tool_calls)"` prints `5`.

### A-2 — Refactor `LLMResponse` to discriminated union (~20 min)

**File:** `backend/llm/base.py`

What to do:
- Replace the existing `LLMResponse` dataclass with two Pydantic models and
  a union type, per design §3.Q2.
- Both models share fields: `provider: str`, `model: str`,
  `prompt_tokens: int | None`, `completion_tokens: int | None`, `raw: Any`.
- `TextResponse` adds `type: Literal["text"] = "text"` and `text: str`.
- `ToolCallResponse` adds `type: Literal["tool_call"] = "tool_call"`,
  `tool_name: str`, `tool_args: dict[str, Any]`.
- `LLMResponse = TextResponse | ToolCallResponse` (type alias at module level).
- Keep `model_config = {"arbitrary_types_allowed": True}` on both — `raw`
  holds the SDK response object which isn't a Pydantic type.

How to verify:
- `python -c "from backend.llm.base import LLMResponse, TextResponse, ToolCallResponse; print('ok')"` imports cleanly.
- A throwaway test: `TextResponse(text="hi", provider="g", model="g", prompt_tokens=None, completion_tokens=None, raw=None)` constructs.

**Do not commit yet.** This change is atomic with A-3 and A-4 — see §14.

### A-3 — Update Gemini provider to branch on function_calls (~30 min)

**File:** `backend/llm/gemini.py`

What to do:
- Add `tools: list[dict] | None = None` parameter to `generate()`.
- When `tools` is provided, translate to Gemini's expected shape (the
  registry will do this via `gemini_function_schemas()` — for now, accept
  whatever the registry produces and pass through).
- After the SDK call, inspect `response.function_calls`:
  - If `response.function_calls` is non-empty: take `[0]`, return
    `ToolCallResponse(tool_name=fc.name, tool_args=fc.args, raw=response, ...)`.
  - Else: return `TextResponse(text=response.text, raw=response, ...)`.
- Keep the `raw=response` on both branches — the orchestrator needs
  `response.candidates[0].content` to preserve the multi-turn contents list
  (design §5, the most-flagged complication).

How to do the tool-pass-through (design §1a):

```python
# Inside generate(), when tools is not None:
config_kwargs = {"system_instruction": system_prompt}
if tools is not None:
    config_kwargs["tools"] = tools  # registry produces list[types.Tool] already
config = types.GenerateContentConfig(**config_kwargs)
```

The translation from JSON-Schema dicts to `types.Tool` happens inside the
registry's `gemini_function_schemas()`, not here. This keeps the provider
ignorant of the schema format — it just passes `tools=` through.

How to verify (after A-4 also done): see A-5.

### A-4 — Update router to accept `tools` kwarg (~10 min)

**File:** `backend/llm/router.py`

What to do:
- Add `tools: list | None = None` parameter to `LLMRouter.generate()`.
- Thread `tools` to `self.primary.generate(..., tools=tools)`.
- Fallback path: pass `tools=None` to the fallback provider (Groq LLM can't
  call tools per design §5). The fallback returns `TextResponse`, which the
  orchestrator handles normally.
- Update return type annotation to `LLMResponse` (the union from A-2).

How to verify (after A-5).

### A-5 — Smoke test: existing chat still works (~10 min)

The union change touches four files. Before moving to Block B, prove the
existing voice loop has not regressed:

1. Restart backend (`python -m backend.desktop`).
2. Hold Alt+Space, ask "what is two plus two?" — should still get a spoken
   reply. This call uses `tools=None` (no registry wired yet), so it must
   return `TextResponse`, which `conversation.py` line 403 still reads
   `.text` from.
3. **If `.text` raises `AttributeError`**: `conversation.py` line 403 needs
   gating now, not later. Add `if isinstance(llm_response, TextResponse)`
   defensively before reading `.text`. The full switch goes in during Block
   C; this is just a guard.

**Commit checkpoint:** A-1 through A-5 ship as one atomic commit:

```
feat(llm): discriminated union for LLMResponse to support tool calls
```

---

## 5. Block B — Registry Implementation (~1.5 hrs)

The stubs already exist with signatures and docstrings (Day 19 B-4). This
block fills in the bodies. Read each method's docstring before writing its
body — your future self wrote those docstrings yesterday for a reason.

### B-1 — Implement `_validate_schema()` helper (~15 min)

**File:** `backend/tools/registry.py` (new private method)

What to do:
- Method signature: `def _validate_schema(self, name: str, schema: dict) -> None`
- Raise `ToolSchemaError` if `schema` contains any top-level forbidden key:
  `$ref`, `$defs`, `allOf`, `oneOf`, `anyOf`.
- Also check that `schema.get("type") == "object"` and `"properties"` exists
  — Gemini requires both for function declarations.

How to do it:

```python
def _validate_schema(self, name: str, schema: dict) -> None:
    """Reject schemas Gemini cannot parse. Fail loud at registration time."""
    forbidden = {"$ref", "$defs", "allOf", "oneOf", "anyOf"}
    found = forbidden & schema.keys()
    if found:
        raise ToolSchemaError(
            f"tool '{name}' has forbidden top-level keys {found}; "
            f"Gemini's parameters_json_schema does not accept these."
        )
    if schema.get("type") != "object":
        raise ToolSchemaError(
            f"tool '{name}' schema must have type='object' at top level."
        )
    if "properties" not in schema:
        raise ToolSchemaError(
            f"tool '{name}' schema must have a 'properties' key."
        )
```

### B-2 — Implement `register()` decorator (~30 min)

What to do:
- Method signature already in stub: returns a decorator that wraps an async
  handler.
- Steps inside `register()`:
  1. Call `self._validate_schema(name, parameters)`.
  2. Check `name` is not already in `self._tools` — raise `ToolError` if so
     (collision means a programming mistake — fail loud).
  3. Define the inner decorator that takes the handler, builds a
     `ToolSchema(name=..., description=..., parameters=..., handler=...)`,
     stores it in `self._tools[name]`, and returns the original handler
     unchanged (so the function can still be called directly in tests).

How to do it:

```python
def register(self, name, description, parameters):
    self._validate_schema(name, parameters)
    if name in self._tools:
        raise ToolError(f"tool '{name}' already registered")
    def decorator(handler):
        if not asyncio.iscoroutinefunction(handler):
            raise ToolError(
                f"tool '{name}' handler must be async (per CLAUDE.md async-first)"
            )
        self._tools[name] = ToolSchema(
            name=name, description=description,
            parameters=parameters, handler=handler,
        )
        logger.info(f"registered tool: {name}")
        return handler
    return decorator
```

The `asyncio.iscoroutinefunction` check enforces Q3 at registration time —
a sync handler fails loud instead of silently blocking the event loop later.

### B-3 — Implement `gemini_function_schemas()` (~20 min)

What to do:
- Per design §3.Q5 and §1a: translate the stored neutral JSON Schema dicts
  into a `list[types.Tool]` Gemini can accept.
- Lazy-import `google.genai.types` inside the method (per design status §2.6
  — keeps registry importable without SDK in minimal test contexts).

How to do it:

```python
def gemini_function_schemas(self) -> list:
    """Translate registered tools to Gemini's Tool format. Lazy SDK import."""
    from google.genai import types  # lazy import — see status §2.6
    declarations = [
        types.FunctionDeclaration(
            name=t.name,
            description=t.description,
            parameters_json_schema=t.parameters,  # design §1a — NOT parameters=
        )
        for t in self._tools.values()
    ]
    return [types.Tool(function_declarations=declarations)]
```

Note: returns a list containing a single `Tool` with all declarations, not
N `Tool` objects each with one declaration. Gemini accepts both shapes; the
single-Tool shape is the recommended pattern in the SDK examples.

### B-4 — Implement `execute()` with hard/soft error split (~20 min)

What to do per design §3.Q7:

```python
async def execute(self, name: str, args: dict) -> Any:
    if name not in self._tools:
        raise ToolNotFoundError(f"unknown tool: {name}")
    tool = self._tools[name]
    try:
        return await tool.handler(**args)
    except (ToolNotFoundError, ToolSchemaError):
        raise  # hard errors propagate to orchestrator
    except TypeError as exc:
        # missing/extra args from LLM — treat as schema error (hard)
        raise ToolSchemaError(f"args mismatch for '{name}': {exc}") from exc
    except Exception as exc:
        logger.warning(f"tool '{name}' soft error: {exc.__class__.__name__}: {exc}")
        return {"error": str(exc), "type": exc.__class__.__name__}
```

The `TypeError` branch catches the case where Gemini hallucinates an arg
the handler doesn't accept (or omits a required one). That's structurally
the same as a schema mismatch — hard error, route to ERROR state.

### B-5 — Implement dunder methods (~5 min)

```python
def __contains__(self, name: str) -> bool:
    return name in self._tools

def __len__(self) -> int:
    return len(self._tools)
```

### B-6 — Smoke test (~10 min)

Throwaway script at `scripts/smoke_registry.py` (do not commit, or commit
under `scripts/` with `# debug` comment):

```python
import asyncio
from backend.tools import registry

@registry.register(
    name="echo",
    description="Echo back the input.",
    parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
)
async def echo(msg: str) -> str:
    return f"echoed: {msg}"

async def main():
    print(f"registered: {len(registry)} tool(s)")
    print(f"'echo' in registry: {'echo' in registry}")
    schemas = registry.gemini_function_schemas()
    print(f"schemas: {schemas}")
    result = await registry.execute("echo", {"msg": "hello"})
    print(f"execute result: {result}")
    # hard error path
    try:
        await registry.execute("nonexistent", {})
    except Exception as e:
        print(f"expected hard error: {type(e).__name__}: {e}")

asyncio.run(main())
```

Run: `python -m scripts.smoke_registry`. Should print four lines and one
expected error. If any step fails, fix before moving to Block C.

**Commit checkpoint:**

```
feat(tools): implement ToolRegistry with hand-written JSON Schema validation
```

---

## 6. Block C — Conversation Orchestrator Wiring (~1.5 hrs)

This is the most delicate block. The voice-pipeline SKILL.md rules are
binding: lock pattern, MUTED re-check, error routing. Re-read that section
before editing `conversation.py`. Do not rush.

### C-1 — Re-read the binding rules (~10 min)

Open `.claude/skills/voice-pipeline/SKILL.md` and read:
- §"The Lock pattern (critical — read before editing the orchestrator)"
- §"Adding tool calls inside THINKING (Day 20)"

Then open `backend/services/conversation.py` and find:
- Line 395: the LLM call inside THINKING.
- Line 417: the next lock acquisition (SPEAKING transition).
- Line 353–356: the `_process_turn` catch block that re-acquires the lock
  before calling `_handle_error`.

You will modify lines 395–417 and nothing else in this file.

### C-2 — Refactor the LLM call site for the typed union (~15 min)

What to do:
- Replace `assistant_text = llm_response.text` (line 403 area) with a
  switch on `response.type`:
  - If `TextResponse`: extract `.text` as before.
  - If `ToolCallResponse`: enter the tool-call loop (C-3 implements this).
- The switch uses `isinstance(response, TextResponse)` or `response.type == "text"`.
  Either works; `isinstance` is more type-safe.

For now, when `tools=None` is passed (which it is until C-3 wires the
registry), only the `TextResponse` branch is exercised. The voice loop
should still work end-to-end after C-2 alone.

How to verify: hold Alt+Space, ask "hi". Spoken reply. No regression.

### C-3 — Implement the tool-call loop (~40 min)

This is the heart of the day. Pseudocode first, then specifics.

The loop sits between the lock release after THINKING transition and the
lock re-acquisition before SPEAKING transition. The model's `contents` list
grows with each iteration. Each iteration starts with a MUTED re-check.

```python
# After THINKING transition; lock is released.
from backend.tools import registry
from google.genai import types

contents = [
    types.Content(role="user", parts=[types.Part(text=user_text_with_context)])
]
final_text: str | None = None

for iteration in range(settings.max_tool_calls):
    # MUTED re-check — voice-pipeline SKILL.md rule
    async with self._lock:
        if self._state == VoiceState.MUTED:
            return
    # lock released for the LLM call

    response = await self._llm.generate(
        contents,  # multi-turn list, not a plain prompt
        system_prompt=system_prompt,
        tools=registry.gemini_function_schemas() if len(registry) > 0 else None,
    )

    if response.type == "text":
        final_text = response.text
        break

    # Tool call branch
    fc_name = response.tool_name
    fc_args = response.tool_args
    raw = response.raw  # Gemini response object — needed for contents list

    # Preserve model's turn EXACTLY (design §5 — critical)
    contents.append(raw.candidates[0].content)

    # Execute the tool — outside the lock
    try:
        tool_result = await registry.execute(fc_name, fc_args)
    except (ToolNotFoundError, ToolSchemaError):
        raise  # hard error — propagates to _process_turn → _handle_error

    # Append tool response to contents for the next iteration
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_function_response(
            name=fc_name,
            response={"result": tool_result},
        )],
    ))

else:
    # Loop completed without break — max iterations hit (Q8)
    fallback_contents = contents + [types.Content(
        role="user",
        parts=[types.Part(text="[max tool calls reached; reply in text only]")]
    )]
    fallback = await self._llm.generate(
        fallback_contents, system_prompt=system_prompt, tools=None,
    )
    final_text = fallback.text if fallback.type == "text" else "I'm not sure how to help with that."

# Continue with existing flow — re-acquire lock, MUTED check, SPEAKING transition
async with self._lock:
    if self._state == VoiceState.MUTED:
        return
    await self._broadcast(AssistantMessageEvent(role="assistant", text=final_text).model_dump())
    await self._transition(VoiceState.SPEAKING)
# ... continues to TTS
```

Three things this code does that matter:

1. **`tools=` is only set when `len(registry) > 0`.** Early development won't
   have tools registered yet; this guard means the loop degrades to a
   single text response naturally.
2. **`contents.append(raw.candidates[0].content)` preserves the model's
   exact turn.** Design §5 calls this out as the #1 gotcha — reconstructing
   from strings will cause a Gemini API error.
3. **MUTED re-check is at the top of each iteration**, not just at the
   start. A slow tool (web search, PDF parse) is exactly when the user will
   hit mute.

### C-4 — Verify hard error path routes to `_handle_error` (~10 min)

What to do:
- The hard-error `raise` in C-3 propagates out of `_run_pipeline`.
- `_process_turn`'s existing catch block at line 353–356 catches the
  exception, re-acquires the lock, and calls `_handle_error`.
- **Do not add a lock acquisition inside the tool-call loop** before raising.
  The catch block already handles that.

How to verify: temporarily edit `get_current_time.py` (which you'll create
in Block D) to `raise ToolNotFoundError("test")` at the start. Run the
voice flow. Backend should:
1. Log the hard error.
2. Broadcast ERROR state.
3. Auto-recover to IDLE after 3s.

Revert the test edit.

### C-5 — Verify existing voice loop still works (~15 min)

Before committing Block C:
1. Restart backend.
2. Voice query that does **not** trigger a tool (no tools are wired yet, so
   any query): "what is the capital of France?" → spoken "Paris" or similar.
3. Check `data/logs/jarvis.log` — the THINKING stage logs should show one
   LLM call and zero tool calls.
4. Mute hotkey works during all states.

**Commit checkpoint:**

```
feat(conversation): tool-call loop in THINKING stage with MUTED re-check
```

---

## 7. Block D — First Tool + End-to-End Smoke Test (~1 hr)

This block validates the whole stack via voice. If anything is broken in
Blocks A–C, you find out here.

### D-1 — Create `get_current_time.py` (~10 min)

**File:** `backend/tools/get_current_time.py`

```python
"""First real tool — returns the current time. Validates the whole tool stack."""
from datetime import datetime

from backend.tools import registry


# Why this tool: trivially correct (datetime.now() can't fail meaningfully),
# proves the LLM → tool_call → tool_result → LLM → text path end-to-end via
# voice, and gives a smoke test for every subsequent tool added in Days 21-26.
@registry.register(
    name="get_current_time",
    description=(
        "Get the current local date and time. Use this when the user asks "
        "what time it is, what day it is, or what the current date is."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def get_current_time() -> str:
    """Return the current local time as ISO-8601."""
    return datetime.now().isoformat(timespec="seconds")
```

The description is precise about *when* to call the tool — "what time/day/
date is it." Vague descriptions confuse the LLM into not calling, or into
calling for unrelated queries.

### D-2 — Import the tool module in lifespan (~5 min)

**File:** `backend/main.py`

Find the FastAPI lifespan function. Add the import inside it (not at module
top — keeps the side effect explicit):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing startup ...

    # Tool registration — import each tool module to trigger its decorator.
    # Adding a new tool: add a line here. Removing: delete the line.
    import backend.tools.get_current_time  # noqa: F401

    logger.info(f"registered {len(registry)} tool(s)")

    yield
    # ... existing shutdown ...
```

The `# noqa: F401` suppresses the "imported but unused" warning — the
import IS the side effect.

### D-3 — Update system prompt (~10 min)

**File:** wherever `system_prompt` is built (likely `conversation.py` or
`backend/llm/prompts.py`).

Add a sentence telling the LLM to use tools:

```
You have tools available for live data the model can't know. When the user
asks for the current time or date, call get_current_time. Do not guess.
```

Keep it short. Long tool instructions in system prompts cause the LLM to
either over-call (every query triggers a tool) or under-call (model thinks
the prompt is documentation, not direction). One sentence per tool category
is enough.

### D-4 — Voice smoke test (~15 min)

1. Restart backend.
2. Confirm log line: `registered 1 tool(s)`.
3. Hold Alt+Space, ask "what time is it?", release.
4. Expected sequence in logs:
   - STT transcription complete.
   - THINKING transition.
   - LLM call (iteration 0) → ToolCallResponse with `tool_name=get_current_time`.
   - `registry.execute("get_current_time", {})` returns ISO timestamp.
   - LLM call (iteration 1) → TextResponse with the time spoken naturally.
   - SPEAKING transition → Piper TTS.
5. You hear the time spoken.

If any step fails, jump to D-5.

### D-5 — Debug + log inspection (~20 min)

Common failure modes and where to look:

| Symptom | Likely cause | Fix |
|---|---|---|
| LLM doesn't call the tool — replies with hallucinated time | System prompt too weak, or description not specific enough | Strengthen D-3 prompt, make D-1 description more directive |
| `AttributeError: 'TextResponse' has no attribute 'tool_name'` | Switch on `response.type` missing somewhere | Grep `conversation.py` for `tool_name` / `.text` access without isinstance |
| Gemini API error after first tool call | `contents.append(raw.candidates[0].content)` missing or wrong | Verify C-3 line; the model's content object must be preserved exactly |
| Tool result not returned — second LLM call says "I couldn't find that" | `Part.from_function_response` wrapping wrong | Check `response={"result": tool_result}` — the dict wrapping is required |
| Loop runs to 5 iterations then text fallback fires for "what time is it?" | LLM is ignoring the tool result and re-calling | Check `tool_result` value — if it's an error dict, the LLM keeps trying. Means Q7 soft-error path fired incorrectly |
| `ToolNotFoundError` raised | Tool not registered — lifespan didn't import module, OR singleton mismatch (Day 18 `__main__` gotcha) | Check log for `registered N tool(s)` at startup; verify `from backend.tools import registry` everywhere |

**Commit checkpoint:**

```
feat(tools): get_current_time tool + lifespan registration + system prompt
```

---

## 8. Block E — Documentation (~1 hr)

Both deliverables must ship today. The skill file in particular gets used
on Days 21, 22, 25, 26 — every time you add a new tool. Quality matters.

### E-1 — Write `tool-calling-pattern/SKILL.md` (~40 min)

**File:** `.claude/skills/tool-calling-pattern/SKILL.md`

Use `voice-pipeline/SKILL.md` as the structural template. Required sections:

1. **When this applies** — bullet list of triggers (adding a tool, modifying
   the registry, changing the loop, debugging tool calls).
2. **One-line description** — what the registry does, in a sentence.
3. **The 4-step pattern to add a new tool**:
   - Create `backend/tools/<tool_name>.py` with an `@registry.register`
     decorator.
   - Write the description so it directs the LLM when to call (cite D-1's
     "use this when the user asks ..." pattern).
   - Add the import line in `backend/main.py` lifespan.
   - Smoke test via voice; verify logs show the LLM → tool → LLM path.
4. **JSON Schema rules** — `type: object` at top level, `properties`
   required, no `$ref`/`$defs`/`allOf`/`oneOf`/`anyOf`. Cite
   `_validate_schema()` which enforces this.
5. **Hard vs soft errors** — when to raise what; reference Q7.
6. **The MUTED re-check rule** — every iteration of the tool-call loop must
   re-check MUTED. Cite voice-pipeline SKILL.md.
7. **The multi-turn `contents` rule** — `raw.candidates[0].content` must be
   preserved exactly. Cite design §5.
8. **Worked example** — full `get_current_time.py` content as a "this is
   the template" reference.
9. **Gotchas** — list with at least: `parameters_json_schema=` not
   `parameters=`; lazy SDK import; `asyncio.iscoroutinefunction` check at
   register time; max_tool_calls cap behaviour.
10. **When to update this file** — same shape as voice-pipeline SKILL.md
    §"When to update this file".

Aim for ~150-200 lines. Less than 100 means it won't be useful enough on
Day 22 when adding the PDF tool; more than 250 means it bloats and you
won't re-read it.

### E-2 — Write `PROJECT_STATUS(DAY_20).md` (~20 min)

Same 8-section structure as Days 18 and 19. Specifics for Day 20:

| § | Content |
|---|---|
| 1 | Table of A-1 through E-1 with status (done/blocked). |
| 2 | Implementation strategy — focus on the *why* behind any non-obvious choice you made during implementation (e.g. how you handled the system-prompt phrasing, why you picked `isinstance` over `.type` check, what surprised you about the SDK). |
| 3 | Problems faced — anything that surprised you in Block D especially. If none, write "no problems faced today" — do not invent drama. |
| 4 | Heads-up for Day 21 — what makes adding the project-memory tools tomorrow tricky: `set_active_project`, `log_to_project`, `recall_from_project`, `list_projects` all touch SQLite + ChromaDB, all need to be project-scoped, all need a system-prompt update. |
| 5 | How to verify Day 20 — copy from §11 of this plan. |
| 6 | Open items before Day 21 — implementing the four memory tools, voice grammar testing ("switch to X", "log this: ...", "what did we conclude about Y?"). |
| 7 | Files changed this day — NEW: `get_current_time.py`, `tool-calling-pattern/SKILL.md`, status doc itself. EDIT: `settings.py`, `base.py`, `gemini.py`, `router.py`, `registry.py`, `conversation.py`, `main.py`. |
| 8 | Commits — list of the 4 commits with hashes once made. |

**Commit:**

```
docs: tool-calling-pattern SKILL.md + Day 20 status document
```

---

## 9. Explicitly Out of Scope Today

- Any tool beyond `get_current_time` (Day 21 starts the real ones).
- OpenAI fallback function calling — design §3.Q5 defers to "an hour's work
  when needed", explicitly not Day 20.
- Streaming tool calls — the SDK supports it; v1 doesn't need it.
- Tool result caching — premature.
- Parallel tool calls (Gemini supports calling multiple tools in one turn).
  Single tool per iteration is enough for v1.
- Type-validating tool args against the schema before calling the handler —
  the `TypeError` catch in `execute()` is acceptable v1 behaviour.
- Refactoring `conversation.py` even if it grows past 500 lines — defer
  splitting to a polish day.

If you finish all five blocks with time to spare, the right move is to
strengthen E-1's SKILL.md with more worked examples — not to start Day 21
work.

---

## 10. End-of-Day Deliverables Checklist

- [ ] `max_tool_calls` setting added.
- [ ] `LLMResponse` is a discriminated union, both subtypes work.
- [ ] `gemini.py` returns the correct subtype based on `function_calls`.
- [ ] `router.py` accepts and threads `tools=`.
- [ ] `ToolRegistry` fully implemented; all 5 methods + 2 dunders work.
- [ ] Tool-call loop in `conversation.py` THINKING stage with MUTED re-check.
- [ ] `get_current_time.py` exists and is registered via lifespan import.
- [ ] System prompt mentions the tool category.
- [ ] Voice smoke test passes: "what time is it?" → tool call → spoken time.
- [ ] Existing voice loop unchanged (no regression for non-tool queries).
- [ ] `.claude/skills/tool-calling-pattern/SKILL.md` written.
- [ ] `PROJECT_STATUS(DAY_20).md` written with all 8 sections.
- [ ] You can explain the tool-call loop on paper without looking at code.

---

## 11. Verification Criteria

Day 20 is "done" when:

1. `python -c "from backend.tools import registry; print(len(registry))"`
   prints `1` (only `get_current_time` registered).
2. Backend startup log includes `registered 1 tool(s)`.
3. Voice query "what time is it?" produces the full path in logs:
   `LLMCall (tool) → registry.execute(get_current_time) → LLMCall (text) → TTS`.
4. Voice query "hello, how are you?" produces a single-LLM-call path (no
   tool call) — proves the LLM correctly distinguishes when tools apply.
5. Voice query during which you hit mute mid-tool-execution stops cleanly
   and goes to MUTED state.
6. `git log --oneline -6` shows 4 Day 20 commits + 2 prior tail commits.
7. `.claude/skills/tool-calling-pattern/SKILL.md` exists and is at least
   100 lines.
8. `PROJECT_STATUS(DAY_20).md` exists with all 8 sections.
9. `data/logs/jarvis.log` contains no `ERROR` lines from the day's smoke
   testing (except any deliberate hard-error tests in C-4 which you
   reverted).

---

## 12. Open Items Going Into Day 21

The Day 21 plan should track:

- [ ] Implement `set_active_project(name: str) -> str` tool.
- [ ] Implement `list_projects() -> list[str]` tool.
- [ ] Implement `log_to_project(content: str) -> str` tool (importance=10).
- [ ] Implement `recall_from_project(query: str) -> list[str]` tool
      (semantic search within active project).
- [ ] System prompt update covering all four commands.
- [ ] Voice tests:
  - "switch to kinase project"
  - "log this: T315I shows 40-fold resistance shift"
  - "what did we say about T315I?"
  - "what projects do I have?"
- [ ] UI update: show active project name somewhere visible (status bar).
- [ ] Verify cross-project isolation still holds (Day 6 invariant).

---

## 13. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `LLMResponse` union change breaks `conversation.py` line 403 mid-day, leaving voice loop broken until C-2. | High — it's a real call-site change | A-5 smoke test catches it before Block B. Add a temporary `isinstance` guard immediately if hit. |
| Gemini SDK function-calling shape differs subtly from design §1 findings (e.g. parameter name typo, missed kwarg). | Medium — SDKs evolve | The design doc was written from reading installed source on Day 19, so this should be stable. If hit, re-open the SDK source — do not Google. |
| `raw.candidates[0].content` is None or missing for some response shape. | Low — but would block the loop | Add `assert raw.candidates`, log the raw response on assertion fail, debug. Don't try-except around it silently — that hides the real bug. |
| LLM refuses to call `get_current_time`, hallucinates a time instead. | Medium | Strengthen description (D-1) and system prompt (D-3). Gemini 2.5 Flash is generally good at tool routing. |
| Tool call loop runs to 5 iterations on a query that shouldn't loop. | Low for `get_current_time` (no recursion possible) | Inspect logs: if it loops, the issue is either system prompt confusion or LLM not understanding the tool result. Either way, the Q8 fallback ensures a spoken reply. |
| Block C-3 takes longer than 40 min. | Medium — it's the most subtle block | If at 60 min and not working: pause. Re-read voice-pipeline SKILL.md §"Adding tool calls inside THINKING (Day 20)". The bug is almost certainly in lock pattern or contents-list handling, not in your implementation. |
| Day 20 runs over budget (>7 hrs). | Medium | E-1 can compress to 100 lines, E-2 can be a stub completed Day 21 morning. The implementation (Blocks A-D) must ship — docs can defer. |

---

## 14. Commit Plan

Four logical commits, in order:

```
1. feat(llm): discriminated union for LLMResponse to support tool calls
   - backend/config/settings.py: add max_tool_calls
   - backend/llm/base.py: replace LLMResponse dataclass with TextResponse | ToolCallResponse
   - backend/llm/gemini.py: branch on response.function_calls
   - backend/llm/router.py: accept tools kwarg; fallback skips tools

2. feat(tools): implement ToolRegistry with hand-written JSON Schema validation
   - backend/tools/registry.py: register, gemini_function_schemas, execute, __contains__, __len__
   - backend/tools/registry.py: _validate_schema rejects $ref/$defs/allOf/oneOf/anyOf

3. feat(conversation): tool-call loop in THINKING stage with MUTED re-check
   - backend/services/conversation.py: tool-call loop between lines 395-417
   - multi-turn contents list preserves model's content turn exactly
   - hard errors propagate to _process_turn; soft errors return as tool result

4. feat(tools): get_current_time tool + lifespan registration + system prompt
   - backend/tools/get_current_time.py: first real tool
   - backend/main.py: import tool module in lifespan to trigger registration
   - system prompt: directive sentence for time-related queries

5. docs: tool-calling-pattern SKILL.md + Day 20 status document
   - .claude/skills/tool-calling-pattern/SKILL.md: 4-step add-a-tool pattern
   - PROJECT_STATUS(DAY_20).md: end-of-day status with 8 sections
```

(That's actually five — the docs commit was promoted from the
end-of-day-rollup style of Day 19. Five clean commits beats one
"end of Day 20" mega-commit.)

---

## 15. Notes for Future-Self

- The 4-step tool-add pattern in E-1's SKILL.md is the highest-leverage
  artefact of this day. Days 21–26 each add 1–4 tools. If the SKILL.md is
  good, you'll never re-think the pattern. If it's bad, you'll re-derive
  it five times. Spend the full 40 min.

- The MUTED re-check between each iteration is the kind of thing that
  works fine in testing because tests are fast. It only matters during a
  slow tool (PDF summarisation in Days 22-24, which can take 10+ seconds).
  If you skip it now, you will discover the bug on Day 22 with a much
  harder repro setup.

- `contents.append(raw.candidates[0].content)` is the single line most
  likely to be reconstructed wrong by Claude Code if it's asked to "make
  the tool loop work" without reading the design doc. Watch for it.

- The `parameters_json_schema=` vs `parameters=` distinction is in the
  registry's translation method. If you ever see "Schema validation failed"
  errors from the Gemini API, check that field name first.

- Don't write a second tool today. The temptation will be strong after
  `get_current_time` works — "I'll just add `list_projects` while I'm
  here." Don't. Day 21 has the system-prompt and UI work that goes with
  the memory tools. Adding one tool out of order will mean either rushing
  Day 21's polish or doing the polish on Day 20 and blowing the budget.
