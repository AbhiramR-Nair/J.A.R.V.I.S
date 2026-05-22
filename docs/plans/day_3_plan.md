# Day 3 Plan — FastAPI Base

**Period:** Day 3 of 30 (Week 1 — Foundation + PyWebView Shell)
**Theme of the day:** Turn the "hello world" FastAPI app from Day 2 into a real, well-shaped backend skeleton. By end of day, every endpoint Jarvis will ever need is stubbed and reachable; settings load from `.env`; logs have request IDs; the React app can call HTTP and open a WebSocket; nothing actually does any *work* yet, but the shape is locked in.
**Time budget:** 4 hours focused work (+ 30 min loose ends from Days 1-2)
**Locked stack reminders:** FastAPI, Pydantic v2 + Pydantic Settings, loguru, WebSockets via FastAPI native, async-first. No new top-level folders.

---

## 0. Read-me-first: state of the project entering Day 3

From the Day 1-2 status report:

- ✅ Folder structure matches the architecture skill; venv exists; Day-2 deps installed and frozen.
- ✅ `backend/main.py` boots FastAPI with `/health` + CORS; `backend/desktop.py` opens a transparent PyWebView window.
- ✅ Two commits landed. Tailwind v3 pinned. Vite app scaffolded.
- ⚠️ **Python 3.13.5** instead of 3.12. Watch for ChromaDB / onnxruntime / pymupdf issues later. Continue for now.
- ⚠️ **`.env` is missing `GROQ_API_KEY` and `TAVILY_API_KEY`** — settings.py must tolerate empty values today (those services aren't called yet) but the model needs slots for them.
- ⚠️ Boot test from Day 2 was *pending* a 3-terminal manual run. **Do that before writing any new code today** — Day 3 builds directly on top of it.
- ⚠️ VS Code interpreter likely still pointed at system Python (P5 in status). Switch to `.venv` before starting; it will save a lot of "import not resolved" noise.

If the boot test fails, **stop and fix it before proceeding** — Day 3 expects the Day 2 setup to be solid.

---

## 1. Goals for the day (Definition of Done)

By end-of-day, all of these must be true:

- [ ] `GET /health` → `{"status": "ok"}` (already exists; now also returns app version + timestamp)
- [ ] `POST /chat` accepts `{"message": str, "project_id"?: str}` and returns a stubbed `{"reply": "...", "request_id": "..."}`. No real LLM call yet — that's Day 4.
- [ ] `GET /memory` returns an empty list `[]` with a 200. No DB hit yet — that's Day 5.
- [ ] `GET /voice-state` returns `{"state": "idle"}`. Static for now; state machine is Day 11.
- [ ] `WS /ws/voice` accepts a WebSocket connection, echoes back a `{"type": "connected"}` event on open, and accepts/logs incoming messages. No real voice events yet — those start Day 7.
- [ ] `backend/config/settings.py` loads **all five API keys** from `.env` via Pydantic Settings, with empty defaults for the two we don't have. Missing-key errors are surfaced **lazily** (only when something tries to *use* the key), never on import.
- [ ] Every HTTP request gets a `request_id` (UUID), included in every log line emitted during that request, and returned in the response header `X-Request-ID`.
- [ ] CORS allows `http://localhost:5173` (Vite dev) — refined from Day 2 to specifically list methods and headers, not wildcard.
- [ ] **From the React app:** clicking a temporary "ping backend" button hits `/health` and shows the result. Opening the WebSocket connection logs "connected" in DevTools console.
- [ ] All five endpoints reachable via `curl` from a terminal.
- [ ] Logs at `data/logs/jarvis.log` (rotating) contain request IDs threading through related lines.
- [ ] Git commit: `feat: fastapi endpoints, settings, structured logging, websocket`

---

## 2. Pre-work (do this first, ~30 minutes)

Don't skip these. They unblock the rest of the day.

### 2.1 Run the pending Day 2 boot test
Three terminals from repo root, exactly as in the status doc §6:

```powershell
# Terminal 1
cd frontend ; npm run dev

# Terminal 2
.venv\Scripts\activate ; uvicorn backend.main:app --port 8000 --reload

# Terminal 3
.venv\Scripts\activate ; python backend/desktop.py
```

**Pass:** `http://localhost:8000/health` returns `{"status":"ok"}` AND a transparent always-on-top window shows the React "J.A.R.V.I.S — online" content. If it fails, fix it before continuing.

### 2.2 Point VS Code at the venv
`Ctrl+Shift+P` → *Python: Select Interpreter* → `.venv\Scripts\python.exe`. Clears the spurious "import could not be resolved" warnings for `webview`, `loguru`, etc.

### 2.3 Fill the two missing API keys (if you have them)
Open `.env` and add:
```
GROQ_API_KEY=...
TAVILY_API_KEY=...
```
If you don't have accounts yet, leave them blank — today's code must work with empty values. Sign up before Day 9 (Groq) and Day 25 (Tavily).

### 2.4 (Optional, ~5 min) Push to GitHub
The Day 1 plan listed a private GitHub repo, but it's not blocking. If you want it done:
```powershell
gh repo create research-jarvis --private --source=. --remote=origin --push
```
(Requires GitHub CLI. If not installed, do this whenever.)

---

## 3. Task breakdown (in execution order)

Each task lists: what to do, the *decisions* you'll need to make with Claude Code, the explanation comments you want in the code (per CLAUDE.md rule #1), and how to verify.

### Task 1 — Pydantic Settings (`backend/config/settings.py`)
**Time:** ~30 min

**Why this first:** Everything else (logging level, CORS origins, log file path, future API keys) reads from this. Get the foundation right, then build on it.

**Write yourself first (signatures + docstrings):**
```python
class Settings(BaseSettings):
    """Loads config from .env via Pydantic Settings.

    Empty defaults are intentional for keys we don't have yet (Groq, Tavily).
    Code that *uses* a key checks emptiness and errors clearly; importing this
    module must never fail because a key is missing.
    """
    # API keys (some may be empty in early weeks)
    gemini_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    tavily_api_key: str = ""

    # Server config
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_dev_url: str = "http://localhost:5173"

    # Logging
    log_level: str = "INFO"
    log_file: str = "data/logs/jarvis.log"

    # Misc
    app_version: str = "0.1.0"
    request_id_header: str = "X-Request-ID"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
```

**Decision points to discuss with Claude before implementing:**
- **Cached singleton vs new instance per call?** Standard pattern is `@lru_cache def get_settings() -> Settings`. Use it — re-reading `.env` on every request is wasteful and causes inconsistent reads.
- **`extra="ignore"` vs `"forbid"`?** Use `"ignore"` so adding a new key to `.env` doesn't crash the app before you've added the field. Re-evaluate when the project is stable.

**Explanation comment to add above the class:**
```python
# Pydantic Settings reads from .env automatically based on field names.
# Empty string defaults are deliberate — we don't have Groq/Tavily keys yet,
# and we don't want imports to fail. The *callers* of those services (Day 9, 25)
# will check `settings.groq_api_key` for emptiness and error there with a clear message.
```

**Verify:**
- `python -c "from backend.config.settings import get_settings; s = get_settings(); print(s.gemini_api_key[:8], s.log_level)"` prints something like `AIzaSyAB INFO`.
- Temporarily rename `.env` → `.env.bak`, re-run the same line: it should still work (all defaults), not crash.

---

### Task 2 — loguru setup with request IDs (`backend/config/logging.py` — new file)
**Time:** ~40 min

**Why a new file:** Logging setup is more than one line and will get reused (by main, by background tasks later). One small module keeps `main.py` clean.

**What it must do:**
1. Configure loguru: console handler + rotating file handler at `data/logs/jarvis.log`.
2. Define a `request_id_var: ContextVar[str]` for carrying the ID across the request lifecycle.
3. Patch loguru's record dict so every log line within a request automatically includes the `request_id` (so you don't have to pass it through every function call).
4. Expose a `configure_logging()` function that `main.py` calls once at startup.
5. Ensure `data/logs/` exists (create it if not).

**Decision points to discuss with Claude:**
- **ContextVar vs middleware-injected logger?** ContextVar is the right call for async FastAPI — it survives `await` boundaries cleanly. Confirm Claude uses `contextvars.ContextVar`, not threadlocal.
- **Log format?** Suggest: `{time:HH:mm:ss.SSS} | {level:<7} | {extra[request_id]} | {name}:{function}:{line} | {message}`. Picks dev-friendly over machine-parseable; we can switch to JSON later if/when needed.
- **Rotation policy?** 10 MB rotation, keep last 5 files. Cheap and bounded.

**Explanation comment to add at top of file:**
```python
# Logging strategy:
#   - One configure_logging() call at app start, idempotent.
#   - request_id_var is a ContextVar holding the current request's UUID.
#     loguru is patched so every log record auto-includes it under `extra.request_id`.
#   - Middleware (next file) sets request_id_var at request start and clears at end.
#   - Background tasks (later: voice loop, wake word) will set their own IDs.
```

**Verify:**
- Boot backend; `data/logs/jarvis.log` is created.
- Console output is colored and shows the format above. No code path raises.

---

### Task 3 — Request-ID middleware (in `backend/main.py`)
**Time:** ~20 min

**What it does:** for every incoming HTTP request, generate a UUID, set the ContextVar, attach it as response header `X-Request-ID`, log request-start and request-end with timing.

**Decision point to discuss with Claude:**
- **Custom middleware function vs `BaseHTTPMiddleware` subclass?** For one piece of middleware, a plain `@app.middleware("http")` function is simpler. Use that.

**Pattern to follow (write the docstring yourself, ask Claude to fill body):**
```python
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Generate a request ID, bind it to logs via ContextVar, time the request,
    attach the ID as a response header. Logs one line at start, one at end."""
    ...
```

**Explanation comment to add:**
```python
# This middleware runs once per HTTP request. It does three jobs:
#   1) Mint a UUID and stash it in the ContextVar so every log inside this
#      request automatically carries it.
#   2) Time the request (rough perf log).
#   3) Echo the ID back as X-Request-ID so the frontend can correlate.
# WebSocket connections don't go through HTTP middleware — they get their own
# request_id set inside the ws handler.
```

**Verify:**
- `curl -v http://localhost:8000/health` shows `X-Request-ID: <uuid>` in response headers.
- Logs show two lines per request (start + done with duration), both tagged with the same ID.

---

### Task 4 — Pydantic models (`backend/models/`)
**Time:** ~20 min

**Files to create (still mostly stubs, but with the *real* request/response shapes wired in):**

`backend/models/chat.py`:
```python
class ChatRequest(BaseModel):
    message: str
    project_id: str | None = None  # None means "use active project" (Day 5)

class ChatResponse(BaseModel):
    reply: str
    request_id: str
```

`backend/models/memory.py`:
```python
class MemoryItem(BaseModel):
    id: str
    content: str
    project_id: str
    importance: int  # 1-10 (Day 6)
    created_at: datetime

class MemoryListResponse(BaseModel):
    items: list[MemoryItem]
```

`backend/models/voice.py`:
```python
VoiceStateLiteral = Literal["idle", "listening", "transcribing", "thinking", "speaking", "muted", "error"]

class VoiceStateResponse(BaseModel):
    state: VoiceStateLiteral
    message: str | None = None  # for "error" state human-readable note
```

**Decision point to discuss with Claude:**
- **`Literal` vs `Enum` for state?** `Literal` is simpler, JSON-serializable for free, and the state machine already lives elsewhere as a concept. Use `Literal`. Revisit if state grows beyond ~10 values.

**Explanation comment to add at top of each file:**
```python
# Pydantic models for {chat|memory|voice} request/response shapes.
# These define the API contract; FastAPI uses them for validation and OpenAPI docs.
# Fields marked Day-N comments are placeholders until that day's work fills them.
```

**Verify:**
- Each file imports without errors.
- `from backend.models.chat import ChatRequest; ChatRequest(message="hi")` works.

---

### Task 5 — API routes (`backend/api/`)
**Time:** ~50 min

Create four router files, one per concern, then mount them in `main.py`. Routers keep `main.py` clean and match the architecture-skill folder layout.

**`backend/api/health.py`:**
```python
router = APIRouter(tags=["health"])

@router.get("/health")
async def health() -> dict:
    """Liveness probe + version stamp. Cheap, no external deps."""
    return {
        "status": "ok",
        "version": get_settings().app_version,
        "timestamp": datetime.now(UTC).isoformat(),
    }
```

**`backend/api/chat.py`:**
```python
router = APIRouter(prefix="/chat", tags=["chat"])

@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Stub. Day 4 wires this to the LLM router. Day 5 saves to SQLite.
    For now: log the message, return a placeholder reply with the request_id."""
    rid = request_id_var.get()
    logger.info(f"Chat stub received: {req.message[:60]}...")
    return ChatResponse(
        reply=f"(stub) I received: {req.message}",
        request_id=rid,
    )
```

**`backend/api/memory.py`:**
```python
router = APIRouter(prefix="/memory", tags=["memory"])

@router.get("", response_model=MemoryListResponse)
async def list_memory() -> MemoryListResponse:
    """Stub. Day 5 wires SQLite, Day 6 wires Chroma."""
    return MemoryListResponse(items=[])
```

**`backend/api/voice.py`:**
```python
router = APIRouter(tags=["voice"])

@router.get("/voice-state", response_model=VoiceStateResponse)
async def voice_state() -> VoiceStateResponse:
    """Stub. Day 11 wires this to services/conversation.py state machine."""
    return VoiceStateResponse(state="idle")

@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    """WebSocket for voice events. Day 7 attaches hotkey events,
    Day 11 attaches state-machine transitions, Day 16 attaches amplitude.
    For now: accept, send a 'connected' hello, log inbound messages, no broadcast."""
    await ws.accept()
    rid = str(uuid.uuid4())
    logger.bind(request_id=rid).info("WS /ws/voice connected")
    try:
        await ws.send_json({"type": "connected", "request_id": rid})
        while True:
            msg = await ws.receive_json()
            logger.bind(request_id=rid).debug(f"WS recv: {msg}")
    except WebSocketDisconnect:
        logger.bind(request_id=rid).info("WS /ws/voice disconnected")
```

**Decision points to discuss with Claude:**
- **One file per router vs all in one `api/routes.py`?** One file per concern matches the architecture skill and scales better. Keep them split.
- **WebSocket auth?** None for v1 — backend binds to 127.0.0.1 only, so the frontend (same machine) is the only client. Document that assumption in a comment.
- **`receive_json` vs `receive_text` + parse?** `receive_json` is fine; malformed JSON raises `JSONDecodeError` which FastAPI converts into a clean disconnect. Wrap in try/except to log gracefully.

**Update `backend/main.py` to mount these:**
```python
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(voice.router)
```

**Tighten CORS** (was wildcard-ish in Day 2):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_dev_url],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-Request-ID"],  # so JS can read it from fetch responses
    allow_credentials=False,
)
```

**Verify each endpoint via curl:**
```powershell
curl http://localhost:8000/health
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"message\":\"hello\"}"
curl http://localhost:8000/memory
curl http://localhost:8000/voice-state
```
Plus visit `http://localhost:8000/docs` — FastAPI's auto-generated Swagger UI should show all four routes with correct request/response schemas. This is a free smoke test of your Pydantic models.

---

### Task 6 — Frontend wiring (light touch)
**Time:** ~30 min

Two small pieces. Don't go beyond these — the React app gets its real face in Week 3.

**6a. A "ping" button that calls `/health`:**
- In `App.tsx`, add a button labelled "Ping backend".
- On click, `fetch('http://localhost:8000/health')`, parse JSON, render the result + the `X-Request-ID` header below.
- Confirms CORS works and that you can read the request-ID header from JS (proves `expose_headers` is set right).

**6b. WebSocket connection on mount:**
- Create `frontend/src/websocket/client.ts` with a thin `connectVoiceWS(): WebSocket` helper that returns a connected WebSocket to `ws://localhost:8000/ws/voice`.
- In `App.tsx`'s `useEffect`, open the connection, log `connected` events to console, close on unmount.

**Decision points to discuss with Claude:**
- **Native `WebSocket` vs a library (e.g. `useWebSocket` hook)?** Native is fine for one connection. The proper hook lives at `frontend/src/hooks/useWebSocket.ts` per the architecture skill — but **don't build the hook today**; build the bare client helper and ad-hoc `useEffect` in `App.tsx`. The hook is a Day 7 / Week 2 thing once you know what events you need.
- **Where to keep the backend base URL?** Hardcoded constant `http://localhost:8000` in a single `frontend/src/api/config.ts` file. Wiring up `.env` for Vite is overkill until we have a non-dev build.

**Explanation comment to add in `client.ts`:**
```typescript
// Thin WebSocket wrapper. Returns a connected socket; caller handles events.
// Backend is bound to 127.0.0.1, so no auth. URL is hardcoded for v1.
// A proper React hook (useWebSocket) lands later when the event surface is bigger.
```

**Verify:**
- Click ping button → see `{"status":"ok", ...}` rendered + an X-Request-ID value.
- Open browser DevTools → Network → WS tab → see `/ws/voice` connection, with one `connected` frame received.
- Backend log shows: HTTP request with ID + WS connect/disconnect lines.

---

### Task 7 — Commit
**Time:** ~10 min

Stage and commit logical chunks (per CLAUDE.md "commit per logical change"). One pragmatic split:

```powershell
git add backend/config/settings.py backend/config/logging.py
git commit -m "feat: pydantic settings + loguru with request-id contextvar"

git add backend/main.py backend/api/ backend/models/
git commit -m "feat: api routers, request-id middleware, ws stub, pydantic models"

git add frontend/src/
git commit -m "feat: frontend can ping /health and open /ws/voice"
```

Or, if you prefer one commit per the plan, do the single `feat: fastapi endpoints, settings, structured logging, websocket` message. Either is fine. Don't squash into "wip".

---

## 4. Verification checklist (run before declaring Day 3 done)

End-to-end sanity, in order:

- [ ] All four `curl` commands above return the expected JSON.
- [ ] `http://localhost:8000/docs` shows four endpoints with correct request/response schemas.
- [ ] Response headers contain `X-Request-ID` on every HTTP response.
- [ ] `data/logs/jarvis.log` contains lines with request IDs; the IDs match what came back in headers.
- [ ] React "Ping backend" button works end-to-end and displays the request ID.
- [ ] WebSocket connection visible in DevTools Network → WS tab; logs show connect + disconnect.
- [ ] Renaming `.env` → `.env.bak` does NOT crash the backend on boot (graceful empty defaults).
- [ ] Committed.

---

## 5. Watch-outs for today

- **CORS pitfall:** if the React fetch fails with a CORS error, double-check `allow_origins` exactly matches `http://localhost:5173` (no trailing slash, no `127.0.0.1` mismatch). Vite's printed URL is the source of truth.
- **WebSocket + CORS:** WebSockets don't use the CORS middleware. They have their own same-origin checks via the browser. If WS connect fails, look for a 403 in the backend log — usually means an Origin-header mismatch unrelated to CORS middleware.
- **`request_id_var` outside a request:** if you `logger.info(...)` from module-import-time code (or from `configure_logging()` itself), the ContextVar isn't set yet. Give it a sentinel default like `"-"` so logs don't blow up.
- **ContextVar + `BackgroundTasks`:** FastAPI's `BackgroundTasks` runs *after* the response — the ContextVar may be cleared by then. Not a Day 3 problem, but flag it for when you add background work in Week 2.
- **Pydantic v2 gotcha:** `model_config = SettingsConfigDict(...)` (note the class-level attribute), not `class Config:` — that's v1 style and silently does nothing in v2.
- **Don't add real LLM/DB code today.** If you find yourself filling in `chat.py` with a Gemini call, stop. That's Day 4. Stubs only today.
- **Python 3.13 note:** none of today's dependencies are 3.13-risky. The risk is later (Day 6 / Day 10). No action needed today.

---

## 6. If you finish early

Optional polish — only pick from this list, don't invent new work:

1. Add a `/version` endpoint returning the same data as `/health` minus the status (sometimes useful for ops).
2. Write a tiny `backend/tests/test_smoke.py` with two requests-based tests (`test_health_ok`, `test_chat_stub_echoes`). No pytest fixtures, just plain `def` tests using `httpx`. Run with `python -m pytest backend/tests/`.
3. Add a `Makefile` (or `tasks.ps1`) with `make dev` that prints the three terminal commands so you stop typing them by hand.
4. Update `README.md`'s "running locally" section so it reflects the four endpoints, not just `/health`.

Anything beyond these — defer. Day 4 (LLM provider abstraction) is the next substantive task and deserves a fresh head.

---

## 7. End-of-day journal entry (one line for `docs/journal.md`)

Pre-filled template — edit as appropriate:

```
Day 3 — wired FastAPI: settings, loguru with request IDs, four endpoints stubbed (/health, /chat, /memory, /voice-state, /ws/voice), Pydantic models, frontend ping + WS smoke. Tomorrow: LLM abstraction.
```

---

## 8. Quick map of what you'll touch today

```
backend/
├── main.py                   [edit] — mount routers, middleware, CORS tightened
├── config/
│   ├── settings.py           [new]
│   └── logging.py            [new]
├── api/
│   ├── __init__.py           [new, empty]
│   ├── health.py             [new]
│   ├── chat.py               [new]
│   ├── memory.py             [new]
│   └── voice.py              [new]
└── models/
    ├── __init__.py           [new, empty]
    ├── chat.py               [new]
    ├── memory.py             [new]
    └── voice.py              [new]

frontend/src/
├── App.tsx                   [edit] — ping button + WS useEffect
├── api/
│   └── config.ts             [new]
└── websocket/
    └── client.ts             [new]

data/logs/                    [auto-created at runtime]
```

Nine new files, two edits. Should fit comfortably in 4 hours with the AI-pair-programming rhythm.

---

**Bottom line for Day 3:** quiet, structural day. The product still does nothing visible — but every interface Jarvis will ever expose is now sketched, validated, and reachable. Day 4 fills in the LLM. Day 5 the DB. By Day 7, this skeleton carries real voice events.
