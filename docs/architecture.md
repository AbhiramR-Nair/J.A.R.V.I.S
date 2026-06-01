# Architecture — research-jarvis

Technical reference for the internals. See README.md for setup, first run, and user-facing features.

---

## Process model

Two processes run simultaneously:

```
Terminal 1: npm run dev          → Vite dev server on :5173
Terminal 2: python -m backend.desktop
              ├── uvicorn (daemon thread) → FastAPI on :8000
              └── webview.start() (main thread, blocks) → PyWebView window
```

The PyWebView window loads `http://localhost:5173`. The React app connects back to `ws://127.0.0.1:8000/ws/voice` and `http://127.0.0.1:8000/*`. Note `127.0.0.1` not `localhost` — Edge WebView2 resolves `localhost` to IPv6 `::1`, but uvicorn binds IPv4 only.

---

## Conversation state machine

`backend/services/conversation.py` (`ConversationOrchestrator`) is the core. All state transitions flow through here.

```
         Alt+Space ↓
IDLE ──────────────────→ LISTENING
                              │ Alt+Space ↑ (or recording cap hit)
                              ↓
                        TRANSCRIBING  ← Groq Whisper-large-v3
                              │
                              ↓
                          THINKING    ← LLM tool-calling loop (max 5 iterations)
                              │
                              ↓
                          SPEAKING    ← Piper TTS subprocess
                              │
                              ↓
                            IDLE

    Ctrl+Alt+J at any point → MUTED (toggles back to IDLE)
    Any exception in pipeline → ERROR → auto-recover to IDLE after 3s
```

State transitions are broadcast via WebSocket as `state_changed` events. The React blob and StatusBar read these.

---

## Hotkey → asyncio bridge

`pynput` runs an OS-level keyboard listener on a **non-asyncio thread**. Bridging to the FastAPI event loop:

```
pynput Listener (OS thread)
    └── loop.call_soon_threadsafe(queue.put_nowait, event)
            ↓
    asyncio.Queue (capped at 100 events)
            ↓
    _dispatch_events() task (asyncio, runs for server lifetime)
            ├── ConversationOrchestrator (PTT/mute state machine)
            └── ws_manager.broadcast() (React frontend)
```

The `app.state.ready` flag gates event routing. Events arriving in the ~1s window before lifespan finishes constructing all subsystems are silently dropped.

---

## Tool-calling loop

Each voice turn that goes through `_process_turn` runs a multi-iteration loop:

```python
contents = [user_message]   # grows with each tool round-trip

for iteration in range(max_tool_calls):   # default: 5
    response = await llm.generate(contents, tools=tool_schemas)
    
    if isinstance(response, TextResponse):
        break                             # final answer; exit loop
    
    # ToolCallResponse: execute tool, append both the call and result
    tool_result = await registry.execute(response.name, response.args)
    contents.append(response.raw_content)   # the function_call Part
    contents.append(tool_result_part)       # the function_response Part
    # loop continues → LLM sees the result and decides next action

# If loop exhausts without a TextResponse:
# final fallback call with tools=None forces a text answer
```

**Hard vs soft errors:**
- `ToolNotFoundError`, `ToolSchemaError` → re-raise (bug in registration, not user error)
- All other exceptions → return `{"error": "..."}` (soft error — LLM tells the user what went wrong)

**Groq fallback:** Groq does not support function calling. When Gemini falls back to Groq, `tools=None` is passed. Groq flattens `function_response` Parts into text using `[tool result from name]: ...` notation so the LLM still sees prior tool results.

---

## Memory architecture

Two stores are written in parallel after every turn with importance ≥ 4.0:

```
                         ┌── SQLite (jarvis.db)
turn (score ≥ 4.0) ──────┤   table: memory
                         │   columns: project_id, role, content, importance, chroma_id
                         │
                         └── ChromaDB (data/chroma/)
                             collection: project_{id}
                             embedding: gemini-embedding-001 (3072-dim)
                             metadata: role, importance, timestamp
```

**Importance scoring** is heuristic by default (`use_llm_importance_scorer=False`):
- "log this", "remember this" → 10
- Domain identifiers (gene names, compound IDs) → 7
- Questions/answers → 5
- Greetings, filler → 2

LLM scoring path is available via `USE_LLM_IMPORTANCE_SCORER=true` in `.env` (adds one LLM call per turn).

**Context injection** before each LLM call:
```
[recency context: last 4 messages from SQLite]
[semantic context: top 3 ChromaDB results for the query, deduplicated, capped at 6000 chars]
```

**Project scoping:** every read/write takes a `project_id`. ChromaDB uses one collection per project (`project_1`, `project_2`, …). SQLite queries always filter `WHERE project_id = ?`. Cross-project data never surfaces.

---

## LLM provider abstraction

```
BaseProvider (base.py)
    ├── GeminiProvider (gemini.py)     — primary; supports tools + response_schema
    └── GroqLLMProvider (groq_llm.py) — fallback; text-only, no function calling

LLMRouter (router.py)
    └── try primary → on RateLimitError/UnavailableError → try fallback
        └── if response_schema is not None: skip fallback (Groq can't do JSON mode)
```

`LLMResponse` is a discriminated union:
- `TextResponse` — final answer; `.text` and optional `.parsed` (Pydantic model from structured output)
- `ToolCallResponse` — function call request; `.name` and `.args`

The router is a module-level singleton (`get_router()`). Constructed once; both providers share the same process lifetime.

---

## Tool registry

`backend/tools/__init__.py` exports the singleton `registry`. Each tool module registers itself on import via the `@registry.register` decorator. Registration happens in `main.py`'s lifespan — the import IS the side effect.

```python
# Adding a new tool:
# 1. Create backend/tools/my_tool.py with @registry.register(...)
# 2. Add one line to main.py lifespan: import backend.tools.my_tool  # noqa: F401
```

`registry.gemini_function_schemas()` builds a single `types.Tool` containing all `FunctionDeclaration` objects. This is passed to every Gemini call. Schema validation at registration time rejects `$defs`, `$ref`, `allOf`, `oneOf`, `anyOf` — Gemini's inline schema format does not support JSON Schema references.

---

## WebSocket event contract

All real-time communication between backend and frontend flows through `ws://127.0.0.1:8000/ws/voice`.

| Event type | Direction | Payload |
|---|---|---|
| `state_changed` | server → client | `{state: VoiceState}` |
| `assistant_message` | server → client | `{text: string}` |
| `speaking_started` | server → client | `{}` |
| `speaking_ended` | server → client | `{}` |
| `speaking_failed` | server → client | `{error: string}` |
| `transcription_complete` | server → client | `{text: string, latency_ms: number}` |
| `transcription_failed` | server → client | `{error: string}` |
| `amplitude_broadcast` | server → client | `{mic: number, tts: number}` (0–1) |
| `search_results` | server → client | `{sources: [{title, url}]}` |
| `timer_fired` | server → client | `{label: string}` |
| `project_changed` | server → client | `{project_name: string}` |
| `ptt_start` / `ptt_end` | server → client | `{}` (echoed from hotkey) |
| `mute_toggle` | server → client | `{}` (echoed from hotkey) |

The frontend maintains a reconnect loop with exponential-style retry. 5 consecutive failures escalates the connection-state dot from amber to red.

---

## Amplitude pipeline (audio reactivity)

```
sounddevice callback (C thread, 16kHz int16)
    └── convert chunk to float32 → compute RMS → normalize against mic_calibration_max
        └── loop.call_soon_threadsafe → asyncio queue

Piper PCM output (bytes, 22050 Hz int16)
    └── convert to float32 → RMS → normalize against tts_calibration_max
        └── broadcast immediately (no queue — latest value wins)

Orchestrator broadcast loop (20 Hz):
    └── EMA(α=0.3) smoothing → ws_manager.broadcast({type: amplitude_broadcast, mic, tts})

React: amplitudeRef (useRef, never triggers re-render)
    └── Blob rAF loop reads ref each frame → drives morphAmount + scale CSS vars
    └── StatusBar waveform bars read ref each frame
```

---

## PDF summarization pipeline

```
Drag-and-drop (browser) → multipart POST /pdf/drop
    └── saves to data/dropped/ → sets pending_drop singleton

Voice: "summarize this paper"
    └── summarize_paper tool → _resolve_path() checks pending_drop, data/test_pdfs/, data/arxiv/
            ↓
        pymupdf parse → ParsedPaper (title, abstract, sections, chunks)
            ↓
        len(full_text) ≤ 12000 chars → single-pass Gemini call (JSON mode, PaperSummary)
        len(full_text) > 12000 chars → map-reduce:
            ├── map: chunk summaries in parallel (Semaphore=3, gemini-flash-lite-latest)
            └── reduce: one structured call (gemini-2.5-flash, JSON mode → PaperSummary)
```

`PaperSummary` fields: `title`, `key_claims`, `methods`, `results`, `limitations`, `tldr`.

Arxiv papers: `fetch_arxiv` downloads PDF via `httpx.AsyncClient` to `data/arxiv/<id>.pdf`, then `summarize_paper` picks it up via `_resolve_path`.
