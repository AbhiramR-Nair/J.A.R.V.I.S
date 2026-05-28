# Skill: Tool-Calling Pattern (research-jarvis)

## When this applies

Read this skill before:

- Adding a new tool to the registry (Days 21–26 and beyond)
- Modifying `backend/tools/registry.py` or `backend/tools/__init__.py`
- Changing the tool-call loop in `backend/services/conversation.py`
- Debugging a tool that the LLM isn't calling, or is calling incorrectly
- Adding a new provider that needs tool support (Month 2)

Do NOT add a tool without reading §"The 4-step pattern" and §"JSON Schema rules".
Getting the schema wrong causes a cryptic Gemini API 400 error at call time, not at
registration — the validation in `_validate_schema()` catches the common mistakes, but
only if you read what it checks for.

## One-line description

Every assistant capability is a tool: an async Python function decorated with
`@registry.register(name, description, parameters)` that the LLM can invoke by name
with structured arguments, and whose result is fed back to the LLM before it replies.

## The 4-step pattern to add a new tool

This is the pattern used for every tool in Days 21–26. Follow it exactly.

### Step 1 — Create `backend/tools/<tool_name>.py`

```python
"""One-line description of what this tool does."""

from backend.tools import registry


@registry.register(
    name="tool_name",
    description=(
        "Plain-English description written for the LLM, not the developer. "
        "Include: WHEN to call it ('Use this when the user asks X'), "
        "what it does ('Returns Y'), and any constraint ('Do not guess — call this')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "arg_name": {
                "type": "string",        # or "integer", "boolean", "number"
                "description": "What this arg means to the LLM.",
            },
        },
        "required": ["arg_name"],        # list required args; omit if none
    },
)
async def tool_name(arg_name: str) -> str:
    """Handler docstring (for developers). Must be async."""
    ...
```

### Step 2 — Write the description so the LLM knows WHEN to call it

The description is the LLM's only signal for deciding whether to call the tool.
Vague descriptions → LLM hallucinates instead of calling. Directive descriptions → LLM
calls reliably.

**Pattern that works:**
> "Use this when the user asks [specific trigger phrase]. Do not [wrong approach]."

**Pattern that fails:**
> "Gets the current time." — LLM may not know when to use it.

**Working example (from `get_current_time.py`):**
> "Get the current local date and time. Use this when the user asks what time it is,
> what day it is, or what the current date is. Do not guess — always call this tool."

The "Do not guess" / "always call this tool" is load-bearing — without it, Gemini 2.5
Flash will hallucinate times and dates rather than calling.

### Step 3 — Add the import to `backend/main.py` lifespan

Find the tool registration block in the lifespan function and add one line:

```python
import backend.tools.get_current_time  # noqa: F401  <- existing
import backend.tools.your_new_tool     # noqa: F401  <- add this
```

The import IS the side effect — it runs the `@registry.register` decorator and stores
the tool in the singleton. The `# noqa: F401` suppresses "imported but unused" linting.
Do not import the tool module anywhere else — the decorator fires on first import,
so double-import is safe but unnecessary.

### Step 4 — Smoke test via voice

1. Restart backend. Confirm startup log: `tools registered: N` (N incremented).
2. Ask the tool's trigger phrase via PTT.
3. Check `data/logs/jarvis.log` for:
   ```
   tool_call iter=0: <tool_name>({...args...})
   tool_result: <tool_name> -> <result>
   ```
4. Hear the LLM incorporate the result in its spoken reply.

If the LLM doesn't call the tool: strengthen the description (Step 2).
If `ToolNotFoundError`: the lifespan import is missing or wrong (Step 3).
If `ToolSchemaError` at startup: the `parameters` dict violates the schema rules below.

---

## JSON Schema rules

The `parameters` dict is passed directly to Gemini's `FunctionDeclaration` via
`parameters_json_schema=` (NOT `parameters=` — see Gotchas). Gemini rejects schemas
that use features it doesn't support.

### Required structure

```python
parameters={
    "type": "object",        # REQUIRED — must be "object" at top level
    "properties": {          # REQUIRED — even if empty ({}) for no-arg tools
        "arg": {
            "type": "string",
            "description": "...",
        },
    },
    "required": ["arg"],     # optional — list the args the LLM must always provide
}
```

### Forbidden at top level (`_validate_schema()` catches these at registration time)

| Forbidden key | Why it appears | What to do instead |
|---|---|---|
| `$ref` | Pydantic `.model_json_schema()` | Write the schema by hand |
| `$defs` | Pydantic `.model_json_schema()` | Write the schema by hand |
| `allOf` | Pydantic inheritance | Flatten to a single object |
| `oneOf` | Pydantic union types | Pick one type for v1 |
| `anyOf` | Pydantic optional fields | Use `"required": []` instead |

Never use Pydantic's `.model_json_schema()` to generate tool parameters. It emits
`$defs` and `$ref` freely. Hand-write the schema — it takes 5 minutes and it works.

### Supported property types

`"string"`, `"integer"`, `"number"`, `"boolean"`, `"array"` (with `"items"`),
`"object"` (nested, without `$ref`). Keep schemas flat for v1.

### No-argument tools

```python
parameters={
    "type": "object",
    "properties": {},
    "required": [],
}
```

The empty `properties: {}` is required — Gemini rejects a schema without the key.

---

## Hard vs soft errors

`registry.execute()` splits errors into two categories (Q7 decision):

### Hard errors — re-raised, orchestrator routes to ERROR state

| Exception | Cause |
|---|---|
| `ToolNotFoundError` | LLM called a name not in the registry (hallucinated tool) |
| `ToolSchemaError` | `TypeError` from handler — wrong or missing args from LLM |

Hard errors propagate out of the tool-call loop, caught by `_process_turn`'s existing
catch block, which re-acquires the lock and calls `_handle_error` → ERROR state →
3-second auto-recover to IDLE.

### Soft errors — caught, returned as dict to LLM

Any other exception from the handler is caught and returned as:

```python
{"error": str(exc), "type": exc.__class__.__name__}
```

The LLM receives this as the tool result and can respond gracefully:
> "I wasn't able to reach the search API. Try again in a moment."

Use soft errors for: network failures, file not found, API rate limits, timeouts.
The handler decides which category — raise `ToolSchemaError` for structural failures
you want to route to ERROR state; let anything else propagate naturally.

---

## The MUTED re-check rule

The tool-call loop re-checks `MUTED` before each LLM call, not just at THINKING entry.
This is the voice-pipeline rule for any stage that takes significant time:

```python
for _iteration in range(settings.max_tool_calls):
    async with self._lock:          # MUTED re-check on every iteration
        if self._state == VoiceState.MUTED:
            return
    # lock released — LLM call happens here, outside the lock
    llm_response = await self._llm.generate(...)
    ...
    tool_result = await registry.execute(fc_name, fc_args)
    # no MUTED check here — can't cancel mid-tool; next iteration catches it
```

PDF summarisation (Days 22–24) and web search (Day 25) each take 5–15 seconds.
Without this guard, hitting Ctrl+Alt+J during tool execution has no effect until the
tool finishes. With it, each LLM call boundary is a clean exit point.

See also: `voice-pipeline/SKILL.md §"Adding tool calls inside THINKING (Day 20)"`.

---

## The multi-turn `contents` rule

After a tool call, the next Gemini call must include the full conversation history as a
`contents` list. The model's turn must be included exactly as returned by the SDK.

```python
from google.genai import types

# Start: user message only
contents = [
    types.Content(role="user", parts=[types.Part(text=user_text)])
]

# After ToolCallResponse — CRITICAL: use raw SDK object, not a reconstruction
contents.append(llm_response.raw.candidates[0].content)

# Append the tool result
contents.append(types.Content(
    role="user",
    parts=[types.Part.from_function_response(
        name=fc_name,
        response={"result": tool_result},  # {"result": ...} wrapper is required
    )],
))

# Next LLM call gets the full history
response = await llm.generate(contents, system_prompt=..., tools=...)
```

Two lines most likely to be reconstructed wrong:

1. `contents.append(llm_response.raw.candidates[0].content)` — use the raw SDK object.
   Reconstructing with `types.Content(role="model", parts=[...])` causes a Gemini API
   INVALID_ARGUMENT error that is very hard to diagnose.

2. `response={"result": tool_result}` — the `{"result": ...}` wrapper is required.
   Passing `response=tool_result` directly raises a type error in the SDK.

---

## Worked example — complete `get_current_time.py`

This is the canonical template. Copy it, rename everything, replace the body.

```python
"""First real tool — returns the current local time."""

from datetime import datetime
from backend.tools import registry


@registry.register(
    name="get_current_time",
    description=(
        "Get the current local date and time. "
        "Use this when the user asks what time it is, what day it is, "
        "or what the current date is. Do not guess — always call this tool."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def get_current_time() -> str:
    """Return the current local time as an ISO-8601 string."""
    return datetime.now().isoformat(timespec="seconds")
```

Key details to carry into every new tool:

- `from backend.tools import registry` — the singleton, not a local instance
- `@registry.register(...)` immediately above `async def` — decorator fires on import
- `async def` — mandatory; sync handlers raise `ToolError` at registration time
- Description ends with a directive ("Do not guess — always call this tool")
- Return value is a plain `str` — JSON-serialisable, no conversion needed

---

## Gotchas

**`parameters_json_schema=` not `parameters=`**
`FunctionDeclaration` has two fields. `parameters=` expects a `types.Schema` object.
`parameters_json_schema=` accepts a raw Python dict. The registry's
`gemini_function_schemas()` uses `parameters_json_schema=` correctly. If you call
`FunctionDeclaration` directly in a test, use the same field. Wrong field → Gemini 400.

**Lazy SDK import in `gemini_function_schemas()`**
`registry.py` imports `google.genai.types` inside the method, not at the module top.
This keeps the registry importable without the SDK (e.g. unit tests). Do not move the
import to the top of `registry.py`.

**`asyncio.iscoroutinefunction` check fires at import time**
`register()` enforces async at decoration time. A sync handler raises `ToolError` when
the module is first imported (i.e. at backend startup), not at the first voice query.
A startup `ToolError` means you forgot `async def`.

**`max_tool_calls` cap and the Q8 fallback**
When the loop runs all 5 iterations without a text response, it fires one final
`tools=None` LLM call. If you see `tool-call loop hit max_tool_calls=5` in logs, the
LLM is looping — check the tool result for errors and strengthen the system prompt.

**Singleton identity**
The registry lives in `backend/tools/__init__.py`. Always `from backend.tools import
registry`. Never instantiate `ToolRegistry()` directly elsewhere — you'll get an empty
instance with no tools registered, and `execute()` will raise `ToolNotFoundError`.

**Tool result must be JSON-serialisable**
Return `str`, `dict`, `list`, `int`, `float`, `bool`, or `None`. The SDK serialises the
result for `Part.from_function_response`. Returning a `Path`, `datetime`, or Pydantic
model raises a serialisation error. Convert before returning.

**Update `50_tools.md` when adding new tools**
Each new tool needs a one-sentence directive in
`backend/prompts/system/50_tools.md` telling the LLM when to call it. The system
prompt is loaded once at backend start. Without the directive, Gemini may ignore the
tool entirely for queries it should handle.

---

## When to update this file

Update when:

- A new tool reveals a gotcha not listed above.
- `registry.py` implementation changes (new validation rules, new error types).
- The tool-call loop in `conversation.py` changes structure.
- A new LLM provider with a different function-calling protocol is added.

Do NOT update for:

- Adding a new tool that follows the 4-step pattern — it's already documented.
- Bug fixes in individual tool handlers that don't affect the pattern.
- UI or voice changes that don't touch the registry or orchestrator.
