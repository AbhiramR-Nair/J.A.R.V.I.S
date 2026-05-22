# Project Status — Day 3

**Period covered:** Day 3 (FastAPI Base — the structural backend skeleton)
**Status:** Complete — Definition-of-Done met, three git commits landed on `master`.
**Environment:** Windows 11, Python 3.13.5, Node 24.15.0, Git 2.52.0

> Checkpoint summary for Day 3: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 4.

---

## 1. What has been done

Day 3 turned the Day-2 "hello world" FastAPI app into a real, well-shaped backend skeleton.
Every endpoint Jarvis will expose is now stubbed and reachable; settings load from `.env`;
logs carry request IDs; the React app can call HTTP and open a WebSocket. **Nothing does any
real work yet** — that's deliberate. Stubs only; LLM is Day 4, DB is Day 5.

| Task | What landed | Status |
|---|---|---|
| 1 — Pydantic Settings | `backend/config/settings.py`: cached `Settings` singleton, all 5 API keys + server/log/misc config, empty-key tolerant | Done, verified |
| 2 — loguru + request IDs | `backend/config/logging.py`: console + rotating file sinks, `ContextVar`-based patcher auto-injects `request_id` | Done, verified |
| 3 — Request-ID middleware | `backend/main.py`: per-request UUID, threaded through logs, returned as `X-Request-ID`, start/end timing | Done, verified |
| 4 — Pydantic models | `backend/models/{chat,memory,voice}.py`: real request/response shapes (`Literal` for voice state) | Done, verified |
| 5 — API routers | `backend/api/{health,chat,memory,voice}.py` mounted in `main.py`; WS `/ws/voice` stub; CORS tightened | Done, verified |
| 6 — Frontend wiring | `App.tsx` ping button + WS `useEffect`; `api/config.ts`; `websocket/client.ts` | Done (browser-verified by user) |
| 7 — Commits | Three logical commits | Done |

**Endpoints now reachable** (all verified in-process via ASGI test client, and `/health` + WS
verified live in the browser):

| Endpoint | Behaviour today |
|---|---|
| `GET /health` | `{"status":"ok","version":"0.1.0","timestamp":<UTC ISO>}` |
| `POST /chat` | Stub echo: `{"reply":"(stub) I received: ...","request_id":"..."}` (LLM = Day 4) |
| `GET /memory` | `{"items": []}` (SQLite = Day 5, Chroma = Day 6) |
| `GET /voice-state` | `{"state":"idle","message":null}` (state machine = Day 11) |
| `WS /ws/voice` | Accepts, sends `{"type":"connected","request_id":"..."}`, logs inbound frames (events = Day 7+) |

---

## 2. Implementation strategy (the *why* behind the choices)

1. **Settings is a cached singleton, not a per-call read.**
   `@lru_cache def get_settings()` reads `.env` once. Re-parsing the file per request is wasteful
   and risks inconsistent reads. Empty-string defaults for all keys mean **importing the module
   never fails** because Groq/Tavily keys are missing — the *callers* (Day 9, 25) will check for
   emptiness and error there with a clear message. Used `model_config = SettingsConfigDict(...)`
   (the Pydantic **v2** form), not an inner `class Config:` (v1 style, silently does nothing in v2).

2. **Request IDs flow via a `ContextVar` + a loguru patcher, not manual argument-passing.**
   A `request_id_var: ContextVar[str]` (default `"-"`) holds the current request's UUID. loguru is
   patched so every record auto-includes it under `extra.request_id`. This means no function in the
   request path has to accept or forward a `request_id` — it's ambient. `ContextVar` (not threadlocal)
   was chosen because it survives `await` boundaries cleanly in async FastAPI. The middleware
   `set`s the var at request start and `reset`s it in a `finally` so the ID can't leak into the next
   request on a reused worker.

3. **Stubs return the *real* response shapes, not placeholders.**
   Even though no endpoint does real work, each returns its true Pydantic model. This locks the API
   contract now, makes `/docs` (Swagger) immediately useful as a free schema smoke test, and means
   Day 4/5/6 fill in *logic* without touching *shape*.

4. **One router file per concern, mounted in a thin `main.py`.**
   `health/chat/memory/voice` each get their own `APIRouter`. Matches the architecture skill and
   keeps the entry point readable. CORS was tightened from the Day-2 wildcard to an explicit
   allow-list, with `expose_headers=["X-Request-ID"]` — that last bit is what lets browser JS
   actually *read* the request ID off a `fetch` response (without it the browser hides custom headers).

5. **Frontend stays a deliberate "light touch."**
   A native `WebSocket` (no library) and an ad-hoc `useEffect` in `App.tsx` — *not* the proper
   `useWebSocket` hook. The hook is a Day 7 / Week 2 task once the event surface is known. Backend
   URLs are a hardcoded constant in `api/config.ts`; wiring Vite env vars is overkill until a
   non-dev build exists.

6. **Verify in-process before declaring done.**
   Every backend task was verified by booting the app through an in-process ASGI/Starlette test
   client (no live server needed) and asserting on real responses, headers, and log output. The
   live 3-terminal run + `/docs` + browser click remained the user's manual confirmation.

---

## 3. Problems faced & how they were handled

### P1 — loguru patcher clobbered explicit `bind()` on the WebSocket  *(impact: medium, resolved)*
- **What:** The WS handler sets its own request ID via `logger.bind(request_id=rid)` because
  WebSockets bypass the HTTP middleware (and thus the `ContextVar`). But the WS log lines came out
  showing `request_id = -` instead of the bound ID — caught during Task 5 verification.
- **Cause:** loguru merges `bind()` extras into the record *first*, then runs the patcher. The
  patcher was doing `record["extra"]["request_id"] = request_id_var.get()` — an **unconditional
  assignment** that overwrote the bound value with the ContextVar default (`-`).
- **Handled:** Changed the patcher to `record["extra"].setdefault("request_id", request_id_var.get())`.
  `setdefault` only supplies the ID when a caller hasn't already bound one, so explicit binds win.
  Re-verified: WS log lines now show the same UUID as the `connected` frame, and HTTP request
  threading (which relies on the ContextVar fallback) still works.
- **Why it mattered:** This is the exact subtlety that was flagged *before* implementing — the fix
  was anticipated, but only the verification step proved which way the precedence actually fell.

### P2 — WebSocket "closed before connection established" warning in the browser  *(impact: cosmetic, not a bug)*
- **What:** DevTools console showed `WebSocket connection to 'ws://localhost:8000/ws/voice' failed:
  WebSocket is closed before the connection is established` at `App.tsx:23` (the effect cleanup).
- **Cause:** React 18 **StrictMode** (in `main.tsx`) double-invokes effects in dev: mount → open
  socket #1 → immediate unmount → cleanup closes socket #1 *mid-handshake* (this warning) → mount
  again → open socket #2 (the real one). Confirmed via the Network → WS tab: two `voice` rows, one
  at **101 Switching Protocols** with the `connected` frame received.
- **Handled:** Diagnosed, confirmed harmless, **left as-is by decision.** It's dev-only — production
  builds don't double-invoke effects, and PyWebView loads a built bundle. A guard fix (only close
  if not `CONNECTING`) was offered but declined, since this `useEffect` is replaced by the proper
  `useWebSocket` hook in Week 2 anyway.

### P3 — Commit message mangled by shell-syntax mismatch  *(impact: trivial, resolved)*
- **What:** The first commit's message came out with stray `@` characters.
- **Cause:** Used PowerShell here-string syntax (`@'...'@`) inside the **Bash** tool, which doesn't
  understand it — the `@` chars were taken literally.
- **Handled:** `git commit --amend -F -` with a bash heredoc fixed the message before any push.
  Subsequent commits used the correct heredoc form.

### P4 — Stale auto-memory referencing "Day 10"  *(impact: low, noted not yet resolved)*
- **What:** Session auto-memory references "Day 10 voice/STT pipeline bugs," but the actual git
  tree and commit history place the project at the *start of Day 3*.
- **Cause:** Unknown — likely a memory written during a different/exploratory session that doesn't
  reflect the committed reality.
- **Handled:** Treated the **git tree as source of truth** (verified all Day-3 target files were
  genuine 0-line stubs before writing). The memory was left untouched this session.
- **Action pending:** Correct or remove that memory so it doesn't mislead a future session into
  assuming Day 10 work exists.

---

## 4. Heads-up: downstream complications to watch

### From P1 (logging precedence) — applies to all future background work
The `setdefault` pattern means **any code path that runs outside the HTTP middleware must bind its
own `request_id`** (like the WS handler does) or its logs show `-`. This will matter for:
- The **voice loop / hotkey listener (Day 7)** and **wake word (Week 4)** — background tasks with no
  HTTP request. Each needs its own `logger.bind(request_id=...)` or a `request_id_var.set(...)` at
  the start of its work unit.
- **`BackgroundTasks` (FastAPI):** these run *after* the response is sent, by which point the
  middleware has already `reset` the ContextVar — so background-task logs will show `-` unless they
  bind explicitly. Not a Day-3 problem; flagged for Week 2.

### WebSocket lifecycle (Day 7 territory)
- The current WS handler is a bare accept/echo loop. When real events attach (hotkey → Day 7, state
  machine → Day 11, amplitude → Day 16), the **StrictMode double-mount (P2)** means the frontend may
  briefly hold two sockets during dev. The proper `useWebSocket` hook should handle reconnection and
  dedupe; don't build it until the event surface is known.
- **WS has no auth by design** — the backend binds to `127.0.0.1`, so the same-machine frontend is
  the only possible client in v1. If the bind address ever changes, this assumption breaks.

### CORS is now strict, not permissive
`allow_origins` is exactly `http://localhost:5173`, methods limited to `GET`/`POST`. If a future
frontend call uses a different method (e.g. `DELETE` for memory management) or the Vite URL changes
(e.g. Vite picks `127.0.0.1` instead of `localhost`, or a different port), **the request will be
CORS-blocked**. Update the allow-list in `main.py` deliberately when that happens — don't revert to
wildcard.

### `data/logs/` is created at runtime and gitignored
`configure_logging()` does `mkdir(parents=True, exist_ok=True)` on first boot. On a fresh clone the
directory won't exist until the app runs once — that's intentional (logs are runtime data). Just
don't expect the folder in a clean checkout.

### Python 3.13 (carried over from Days 1-2)
None of Day 3's dependencies are 3.13-risky. The risk still lives later: **ChromaDB (Day 6)**,
onnxruntime/Piper (Day 10), pymupdf (Days 22-24), openWakeWord (Week 4). No action today.

### Gemini SDK pin (Day 4 — next)
Per the standing watch-out: once the Gemini SDK is installed and working on Day 4, **pin its exact
version in `requirements.txt` immediately** — that SDK changes shape often. Re-freeze after the day.

---

## 5. Open items before Day 4

- [ ] Run the live 3-terminal boot + the four `curl` commands (verified in-process; live curls still owed)
- [ ] Visit `http://localhost:8000/docs` — confirm 4 routes show correct request/response schemas
- [ ] (Carried from Day 1-2) Fill `GROQ_API_KEY` + `TAVILY_API_KEY` in `.env` (not blocking until Day 9/25)
- [ ] (Optional) Correct/remove the stale "Day 10" auto-memory (P4)
- [ ] (Optional, non-blocking) Create + push the private GitHub repo — still local-only

---

## 6. How to verify Day 3

Live 3-terminal run from repo root, then:

```powershell
curl http://localhost:8000/health
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"message\":\"hello\"}"
curl http://localhost:8000/memory
curl http://localhost:8000/voice-state
```

**Pass criteria:**
- All four return the expected JSON; every HTTP response has an `X-Request-ID` header.
- `http://localhost:8000/docs` shows all four routes with correct schemas.
- `data/logs/jarvis.log` contains lines with request IDs that match the response headers.
- React "Ping backend" button renders `/health` JSON + the `X-Request-ID` value.
- DevTools → Network → WS tab shows `/ws/voice` at 101 with a `connected` frame.
  *(A "closed before connection established" warning on the throwaway StrictMode socket is expected
  in dev — see P2. Confirmed harmless.)*
- Renaming `.env` → `.env.bak` does NOT crash the backend on boot (graceful empty defaults).

---

## 7. Commit log for this period

```
566d7ea feat: frontend can ping /health and open /ws/voice
f07a0aa feat: api routers, request-id middleware, ws stub, pydantic models
2afb969 feat: pydantic settings + loguru with request-id contextvar
```

> Note: `backend/desktop.py` remains modified from a prior session (unrelated to Day 3) and was
> intentionally left out of these commits. The planning docs under `docs/plans/` and
> `docs/project_status/` are untracked and left for the user to add when desired.
