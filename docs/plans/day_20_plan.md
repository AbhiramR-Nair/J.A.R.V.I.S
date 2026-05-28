# Day 20 Plan — Tool-Calling Architecture

**Period:** Day 20 (Week 4, Day 1)
**Shape:** Full implementation day — ToolRegistry + Gemini wiring + first real tool
**Environment:** Windows 11, Python 3.13.5, google-genai==2.6.0, Gemini 2.5 Flash

> This document was written on Day 19 after reading the installed SDK source and
> re-reading the existing code. Every design decision below was settled before any
> implementation code was written. If a decision changes during Day 20 implementation,
> record the change here with a one-line note explaining what shifted and why.

---

## 1. Installed SDK — B-1 Findings

**Package:** `google-genai==2.6.0`
**Not** `google-generativeai` — that package is deprecated as of late 2025. The installed
package is the new unified Gen AI SDK. These are different packages with different APIs.

**Source location:** `d:\J.A.R.V.I.S\.venv\Lib\site-packages\google\genai\`

### 1a. How to pass tools to a generate call

```python
from google.genai import types

tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="tool_name",
            description="What this tool does and when to call it.",
            parameters_json_schema={      # <-- use THIS field, not parameters=
                "type": "object",         # parameters= expects types.Schema (Gemini's own
                "properties": {           # schema type). parameters_json_schema= accepts a
                    "query": {            # plain dict following JSON Schema conventions.
                        "type": "string", # This is the field our hand-written schemas go into.
                        "description": "..."
                    }
                },
                "required": ["query"]
            }
        )
    ]
)

config = types.GenerateContentConfig(
    system_instruction=system_prompt,
    tools=[tool],                         # list[Tool] — can include multiple tools
)

response = await client.aio.models.generate_content(
    model=model_name,
    contents=prompt_or_contents_list,
    config=config,
)
```

**Critical gotcha:** `FunctionDeclaration` has two mutually exclusive fields:
- `parameters` — expects a `types.Schema` object (Gemini's own schema type; harder to build)
- `parameters_json_schema` — accepts a raw Python dict following JSON Schema conventions

Always use `parameters_json_schema` for our hand-written schemas. Never use `parameters`.

### 1b. How to detect a function call in the response

```python
# response.function_calls is a property on GenerateContentResponse.
# Returns list[FunctionCall] | None.
# None means the model chose to reply with text, not a tool call.

if response.function_calls:
    fc = response.function_calls[0]  # FunctionCall object
    tool_name = fc.name              # str — matches FunctionDeclaration.name
    tool_args = fc.args              # dict[str, Any] — the arguments to pass

    # response.text will raise or return empty when function_calls is set —
    # don't try to read text when the model called a tool.
```

### 1c. How to send a tool result back (multi-turn contents list)

The Gemini API expects a growing `contents` list for multi-turn conversations.
After a tool call, the list must include:
1. The original user turn
2. The model's function-call turn (`response.candidates[0].content`)
3. A user-role turn with the `FunctionResponse` part

```python
contents = [
    # Turn 1: original user message
    types.Content(role="user", parts=[types.Part(text=user_prompt)]),

    # Turn 2: model's response (contains the function_call part)
    response.candidates[0].content,

    # Turn 3: our tool result, sent as role="user" with FunctionResponse
    types.Content(
        role="user",
        parts=[
            types.Part.from_function_response(
                name=fc.name,
                response={"result": tool_result}  # dict — any JSON-serialisable value
            )
        ]
    ),
]

# Call generate again with the full contents list:
final_response = await client.aio.models.generate_content(
    model=model_name,
    contents=contents,
    config=config,  # keep tools= in config so the model can call more tools if needed
)
```

### 1d. Version-specific gotchas found in SDK source

- `GenerateContentConfig.tools` accepts `ToolListUnion` — in practice, pass `list[types.Tool]`.
- `response.function_calls` is a property that reads from `candidates[0].content.parts`.
  If the response has multiple candidates (it won't for our use case), it logs a warning
  and returns the first candidate's calls only. Single-candidate responses are safe.
- `FunctionCall.id` exists but is optional. Gemini API typically leaves it None for
  standard function calling (it's used in streaming/bidi contexts). Don't rely on it
  for matching — match on `fc.name` instead.
- There is no `response.function_call` (singular) shortcut — only `response.function_calls`
  (plural, a list). Always index with `[0]` for single-tool calls.

---

## 2. Current Code Shape — B-2 Findings

### 2a. `backend/llm/base.py`

`BaseProvider.generate()` returns `LLMResponse` — a dataclass with fields:
- `text: str` — required, the model's reply
- `provider: str`, `model: str` — which provider/model answered
- `prompt_tokens: int | None`, `completion_tokens: int | None` — usage (if reported)
- `raw: Any` — the original SDK response object (debugging only)

The `tools` parameter is already stubbed out with a comment:
```python
# tools: list[dict] | None = None,  # Day 20 — function calling
```

The seam is pre-cut. Day 20 uncomments and implements it.

**Problem:** `LLMResponse.text: str` is required. When the model returns a tool call
instead of text, there is no text. This is why Q2 (return type) matters — the current
shape can't represent a tool-call response without a text.

### 2b. `backend/llm/router.py`

- `LLMRouter.generate()` passes `prompt` and `system_prompt` through to providers unchanged.
- Returns `LLMResponse` as-is — no normalisation.
- Fallback logic catches: `LLMRateLimitError`, `LLMUnavailableError`, `LLMAuthError`.
  `LLMError` (bad prompt 400) is NOT caught — re-raises immediately.
- Natural seam: add `tools` kwarg to `LLMRouter.generate()` and thread it to
  `self.primary.generate(...)`. Fallback path can skip tools (acceptable degraded mode).
- Module-level singleton `_router` built on first `get_router()` call.

### 2c. `backend/services/conversation.py`

The LLM call is at **line 395**:
```python
llm_response = await self._llm.generate(stt_result.text, system_prompt=system_prompt)
```

The next lock acquisition (MUTED check + SPEAKING transition) is at **line 417**:
```python
async with self._lock:
    if self._state == VoiceState.MUTED:
        return
    await self._broadcast(AssistantMessageEvent(...).model_dump())
    await self._transition(VoiceState.SPEAKING)
```

**The tool-call loop slots between lines 395 and 417**, outside the lock, checking MUTED
before each iteration. This is exactly what `voice-pipeline/SKILL.md` §"Adding tool calls
inside THINKING (Day 20)" specifies.

Lock pattern rules (binding — do not violate):
- Tool execution runs OUTSIDE the lock
- Re-check `MUTED` between each tool call (not just at THINKING entry)
- Raise on fatal tool errors so `_handle_error` can route to ERROR state
- The lock is NOT held during the LLM call or tool execution

---

## 3. Design Decisions — Q1 through Q8

### Q1 — Where does the tool-call loop live?

**Decision:** `conversation.py` THINKING stage (between the LLM call and the SPEAKING
transition, around lines 395–417).

**Rationale:** The state machine, lock, MUTED re-check, and `_handle_error` all live here.
Putting the loop where state management already lives keeps cancellation semantics correct.
Putting it in `router.py` would couple the router to the registry. A new `tool_loop.py`
module adds a file without adding clarity for this scale.

**Tradeoff:** `conversation.py` will grow. After Day 20, measure file length and decide
whether to split in a future polish day.

### Q2 — What does `BaseProvider.generate()` return when tools are in play?

**Decision:** Typed union — `LLMResponse` becomes a Pydantic discriminated union:
```python
class TextResponse(BaseModel):
    type: Literal["text"] = "text"
    text: str
    provider: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: Any

class ToolCallResponse(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool_name: str
    tool_args: dict[str, Any]
    provider: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: Any

LLMResponse = TextResponse | ToolCallResponse
```

The orchestrator switches on `response.type`. Providers stay stateless.

**Rationale:** Single entry point, typed union the orchestrator switches on. Option (b)
— a new `generate_with_tools()` method — doubles the interface surface. Option (c) —
provider owns the loop — couples providers to the registry, making fallback and testing
painful.

**Implementation note:** `base.py` replaces the current `LLMResponse` dataclass.
`gemini.py` gains a branch: if `response.function_calls`, return `ToolCallResponse`;
else return `TextResponse`. This change touches `base.py`, `gemini.py`, `router.py`
(return type annotation), and `conversation.py` (switch on `response.type`). Keep the
Day 20 commit atomic — all four files in one commit.

### Q3 — Sync or async tool handlers?

**Decision:** All async. Per `CLAUDE.md` "async-first" rule.

Sync work (`subprocess.Popen`, blocking file I/O) wraps in `asyncio.to_thread()`.
Trivial tools (`get_current_time`) are `async def` even though they do no I/O — the
consistency is worth the minor verbosity.

### Q4 — JSON Schema source-of-truth?

**Decision:** Hand-written JSON Schema dicts, stored in the registration call.

Pydantic `.model_json_schema()` emits `$defs`, `title`, `anyOf`, and other keys that
the Gemini API rejects or ignores. For 6–8 tools, hand-writing is small and keeps the
call site readable.

Add a small `_validate_schema()` helper that asserts no `$ref`, `$defs`, `allOf`,
`oneOf`, `anyOf` at the top level. Fail loud at registration time, not at the first
tool call.

**B-1 note:** Use `parameters_json_schema=` (dict) not `parameters=` (types.Schema)
in `FunctionDeclaration`. The registry's `gemini_function_schemas()` method handles
this translation.

### Q5 — Provider-neutral or Gemini-only schemas in registry?

**Decision:** Store provider-neutral OpenAPI-style JSON Schema in the registry.
`gemini_function_schemas()` translates to `list[types.Tool]` on demand.

For Day 20: only wire Gemini. If Gemini is down and the Groq fallback is hit, the
fallback runs without tools (text-only degraded mode). An `openai_tool_schemas()`
translator is an hour's work when OpenAI fallback needs tools.

### Q6 — How are tools registered?

**Decision:** Decorator pattern with a module-level singleton.

```python
# backend/tools/__init__.py
registry = ToolRegistry()

# backend/tools/web_search.py
from backend.tools import registry

@registry.register(
    name="web_search",
    description="Search the web for current information.",
    parameters={...}
)
async def web_search(query: str) -> str:
    ...
```

FastAPI lifespan imports tool modules explicitly:
```python
import backend.tools.web_search
import backend.tools.timer
# etc.
```

This triggers the decorators at import time. A tool that is never imported is never
registered — the lifespan's explicit list is the canonical "which tools are active" list.

**Tradeoff:** Requires an explicit import list in the lifespan. Acceptable — the side
effect is explicit, not magic.

### Q7 — Tool errors: fatal or recoverable?

**Two categories:**

**Hard errors** — re-raise to the orchestrator, route to `_handle_error`, state → ERROR:
- `ToolNotFoundError`: LLM hallucinated a tool name not in the registry.
- `ToolSchemaError`: arguments don't match the registered schema (type mismatch, missing required).
- Max iterations exceeded (Q8 handling).

**Soft errors** — catch inside `execute()`, return as tool result, feed back to LLM:
- File not found, network timeout, subprocess non-zero exit.
- Any other exception from the handler.

`execute()` implementation:
```python
try:
    result = await tool.handler(**args)
    return result
except (ToolNotFoundError, ToolSchemaError):
    raise  # hard errors propagate
except Exception as exc:
    logger.warning(f"tool '{name}' raised soft error: {exc}")
    return {"error": str(exc), "type": exc.__class__.__name__}
```

### Q8 — Safety cap of 5 iterations — what happens at iteration 6?

**Decision:** After the 5th tool call, force a final text-only LLM call with a
`[max tool calls reached; reply in text only]` note appended to the prompt. This
ensures the user always gets a spoken reply.

```python
MAX_TOOL_CALLS = settings.max_tool_calls  # default 5, lives in settings.py

for iteration in range(MAX_TOOL_CALLS):
    response = await llm.generate(prompt, tools=registry.gemini_function_schemas())
    if response.type == "text":
        return response.text
    # execute tool, append result to contents list, loop

# Cap hit — force text reply
final = await llm.generate(
    prompt + "\n[max tool calls reached; reply in text only]",
    tools=None,
)
return final.text
```

Add `max_tool_calls: int = 5` to `backend/config/settings.py`.

---

## 4. Day 20 Implementation Checklist

These are the open items from Day 19 §8. Tick them off as you go.

- [ ] `backend/config/settings.py` — add `max_tool_calls: int = 5`
- [ ] `backend/llm/base.py` — replace `LLMResponse` dataclass with discriminated union
      (`TextResponse`, `ToolCallResponse`, `LLMResponse = TextResponse | ToolCallResponse`)
- [ ] `backend/llm/gemini.py` — implement tool-aware branch: if `response.function_calls`,
      return `ToolCallResponse`; else return `TextResponse`
- [ ] `backend/llm/router.py` — add `tools` kwarg; thread to primary; fallback skips tools
- [ ] `backend/tools/registry.py` — implement `register()`, `gemini_function_schemas()`,
      `execute()`, `__contains__`, `__len__` (all currently `raise NotImplementedError`)
- [ ] `backend/services/conversation.py` — add tool-call loop between lines 395 and 417;
      MUTED re-check before each iteration; hard errors → `_handle_error`
- [ ] `backend/tools/get_current_time.py` (new) — first real tool; smoke test via voice:
      "what time is it?" should call the tool and speak the result
- [ ] `backend/main.py` lifespan — import tool modules explicitly to register them
- [ ] `.claude/skills/tool-calling-pattern/SKILL.md` — write AFTER the pattern is proven
      by working code (not before)

---

## 5. Downstream Complications to Watch on Day 20

**`LLMResponse` type change breaks every call site that reads `.text` directly.**
`conversation.py` at line 403 does `assistant_text = llm_response.text`. After the
union change, this needs to be gated: only read `.text` on a `TextResponse`. The switch
on `response.type` is the correct pattern. Grep for `llm_response.text` before committing.

**Multi-turn `contents` list must preserve the model's turn exactly.**
When sending a tool result back, the second element of `contents` must be
`response.candidates[0].content` — not a reconstructed string. If you lose the
model's original content object, Gemini returns an API error. Keep a reference to the
raw response between iterations.

**The fallback provider (Groq LLM) cannot call tools.**
`router.py` will pass `tools=None` to the fallback. This means: if Gemini is down during
a tool-heavy query, the user gets a text-only reply. This is acceptable degraded behavior
and must not raise — the orchestrator treats the fallback's `TextResponse` normally.

**`_handle_error` requires `self._lock` to be held (asserted at line 315).**
Hard tool errors propagate out of the tool-call loop (which runs outside the lock) up to
`_process_turn`. The `_process_turn` catch block at line 353–356 re-acquires the lock
before calling `_handle_error`. This is the correct pattern — don't acquire the lock
inside the tool-call loop before raising.
