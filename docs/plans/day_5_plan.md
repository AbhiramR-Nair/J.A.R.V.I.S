# Day 5 Plan — SQLite Schema & Persistent Storage

**Day:** 5 of 30
**Theme:** Give Jarvis a memory that survives restarts.
**Estimated time:** 5 hours
**Git commit target:** `feat: sqlite schema with project-scoped storage`

---

## Context: Why This Day Matters

Right now Jarvis can think (Day 4 — LLM), but it forgets everything the moment the server restarts. Every conversation starts from zero. Day 5 fixes that by introducing the **relational backbone**: a SQLite database that stores projects, conversations, messages, and API costs.

This day is also the inflection point where the project-scoping rule becomes real. From Day 5 forward, every message, memory item, and cost log entry is attached to a `project_id`. Getting this right now means ChromaDB (Day 6), tool calling (Day 20), and PDF summarization (Days 22–24) never have to revisit the schema.

One concrete carry-over from Day 4: `cost_tracker.py` already calls `record()` on every LLM call but currently only logs to disk. Today you wire the actual SQL `INSERT` that makes those records permanent.

---

## Pre-Coding Reading (do this first, ~15 min)

Before writing a line, read these three things:

1. **`backend/services/cost_tracker.py`** — find the `TODO(Day 5)` comment. This is where your Day 5 SQL insert lands. Read the existing `record()` signature so you know what data is already being passed.
2. **`backend/api/chat.py`** — understand the current `/chat` handler. After Day 5, it must save both the user message and the assistant reply before returning the response.
3. **`backend/config/settings.py`** — confirm `DB_PATH` or equivalent is either already there or needs adding today. All file paths are settings, never magic strings.

---

## Schema Design Decision (ask yourself before coding)

The `messages` table needs to know which `conversation` it belongs to. A conversation belongs to a `project`. This three-level hierarchy (`project → conversation → message`) means:

- You can recall the full history of a single conversation.
- You can recall all conversations in a project.
- Switching projects truly isolates context.

**Before you ask Claude to write the schema, decide:**

> Should `/chat` auto-create a new `conversation` row on every session start, or should conversations be explicitly named and created by the user?

**Recommended answer for v1:** Auto-create a conversation on each backend boot (or on the first message of a session), with a timestamp-based name like `"session_2026-05-22_14:30"`. The user can rename later. This keeps Day 5 simple while leaving the door open for Day 21's project-memory tools.

---

## Tasks

Work through these in order. Each builds on the previous.

---

### Task 1 — Write `backend/database/schema.sql`

**What to build:** The SQL file that defines every table Jarvis will ever use in v1. This runs once on first boot to create the database.

**Tables to define:**

#### `projects`
```
id          INTEGER PRIMARY KEY AUTOINCREMENT
name        TEXT NOT NULL UNIQUE
is_active   INTEGER NOT NULL DEFAULT 0   -- boolean; exactly one row = 1 at all times
created_at  TEXT NOT NULL DEFAULT (datetime('now'))
```
Business rule: only one project can have `is_active = 1`. This is enforced in Python, not the schema (SQLite doesn't support `CHECK` across multiple rows natively).

#### `conversations`
```
id          INTEGER PRIMARY KEY AUTOINCREMENT
project_id  INTEGER NOT NULL REFERENCES projects(id)
name        TEXT NOT NULL
created_at  TEXT NOT NULL DEFAULT (datetime('now'))
```

#### `messages`
```
id               INTEGER PRIMARY KEY AUTOINCREMENT
conversation_id  INTEGER NOT NULL REFERENCES conversations(id)
project_id       INTEGER NOT NULL REFERENCES projects(id)
role             TEXT NOT NULL            -- 'user' or 'assistant'
content          TEXT NOT NULL
provider         TEXT                     -- 'gemini', 'groq', NULL for user messages
model            TEXT                     -- e.g. 'gemini-2.5-flash', NULL for user messages
created_at       TEXT NOT NULL DEFAULT (datetime('now'))
```
Note: `project_id` is denormalized here (you can get it via `conversation_id`) but it makes project-scoped queries dramatically simpler — no join needed to filter by project.

#### `memory`
```
id           INTEGER PRIMARY KEY AUTOINCREMENT
project_id   INTEGER NOT NULL REFERENCES projects(id)
content      TEXT NOT NULL
importance   REAL NOT NULL DEFAULT 5.0    -- 1.0–10.0; ChromaDB mirrors this
source       TEXT                         -- 'user', 'assistant', 'log_to_project'
chroma_id    TEXT                         -- populated Day 6 when ChromaDB is wired
created_at   TEXT NOT NULL DEFAULT (datetime('now'))
```

#### `tasks`
```
id           INTEGER PRIMARY KEY AUTOINCREMENT
project_id   INTEGER NOT NULL REFERENCES projects(id)
description  TEXT NOT NULL
status       TEXT NOT NULL DEFAULT 'pending'  -- 'pending', 'done', 'cancelled'
due_at       TEXT                             -- ISO 8601, nullable
created_at   TEXT NOT NULL DEFAULT (datetime('now'))
```
Not wired to anything on Day 5 — the schema is created now so Day 26 (timers) can use it without a migration.

#### `cost_log`
```
id                  INTEGER PRIMARY KEY AUTOINCREMENT
provider            TEXT NOT NULL     -- 'gemini', 'groq', 'openai'
model               TEXT NOT NULL
prompt_tokens       INTEGER NOT NULL DEFAULT 0
completion_tokens   INTEGER NOT NULL DEFAULT 0
estimated_usd       REAL NOT NULL DEFAULT 0.0
request_id          TEXT              -- links to HTTP request; nullable for non-HTTP calls
created_at          TEXT NOT NULL DEFAULT (datetime('now'))
```

**Things to tell Claude when you ask for this file:**
- "Enable foreign keys with `PRAGMA foreign_keys = ON;` — this must be in the connection setup, not in the schema file itself."
- "Use `INTEGER` for booleans (SQLite doesn't have a native bool type). 0 = false, 1 = true."
- "All `created_at` columns use `TEXT` in ISO 8601 format (`datetime('now')` default)."
- "Add a comment block above each `CREATE TABLE` explaining what the table is for."

**Completion check:** Open the file and confirm all 6 tables are present. Read each column and make sure you could explain what it stores.

---

### Task 2 — Write `backend/database/db.py`

**What to build:** A module that manages the SQLite connection, runs the schema on first boot, and exposes a `get_connection()` function the rest of the codebase uses.

**Key design points to discuss with Claude before it writes the code:**

> **Option A:** One persistent connection opened at app startup and reused.
> **Option B:** New connection per request (or per operation), opened and closed.

For a single-user local app, **Option A** is simpler and fine. The risk is thread safety — FastAPI runs async but SQLite connections aren't thread-safe by default. The fix is `check_same_thread=False` in `sqlite3.connect()`. Ask Claude to explain what that flag does before writing the code.

**The module must:**
- Call `sqlite3.connect(settings.db_path, check_same_thread=False)`
- Execute `PRAGMA foreign_keys = ON;` immediately on every connection open
- Read and execute `schema.sql` on first boot (use `CREATE TABLE IF NOT EXISTS` in the schema so this is safe to call repeatedly)
- Return the connection object for use in other modules
- Use `loguru` to log when the DB is first created vs. already exists

**Where this file lives:** `backend/database/db.py`

**Update `settings.py`** to add:
```python
db_path: str = "data/jarvis.db"
```

**Completion check:** Delete `data/jarvis.db` if it exists, restart the backend, and confirm a new `jarvis.db` appears in `data/`. Open it with any SQLite viewer (VS Code has extensions, or use `sqlite3 data/jarvis.db ".tables"` in terminal) and verify all 6 tables are present.

---

### Task 3 — Write `backend/memory/sqlite_store.py`

**What to build:** A clean wrapper around the database operations the rest of the codebase needs. Nothing in `backend/api/` or `backend/services/` should write SQL directly — they go through this module.

**Functions to implement (write the docstring for each before asking Claude to implement):**

```python
async def get_active_project() -> dict:
    """Return the currently active project row as a dict {id, name}.
    Raises RuntimeError if no active project exists."""

async def set_active_project(name: str) -> dict:
    """Set the named project as active (create it if it doesn't exist).
    Deactivates all other projects. Returns the now-active project dict."""

async def list_projects() -> list[dict]:
    """Return all projects as [{id, name, is_active, created_at}], ordered by name."""

async def save_message(
    conversation_id: int,
    project_id: int,
    role: str,           # 'user' or 'assistant'
    content: str,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """Insert a message row. Returns the new message id."""

async def get_conversation_messages(conversation_id: int) -> list[dict]:
    """Return all messages for a conversation, ordered by created_at ASC."""

async def create_conversation(project_id: int, name: str) -> int:
    """Create a new conversation row. Returns the new conversation id."""

async def get_or_create_session_conversation(project_id: int) -> int:
    """Get the most recent conversation for this project started today,
    or create a new one with a timestamp name. Returns conversation_id."""
```

**Important:** These functions use `sqlite3` which is synchronous. FastAPI is async. The right approach for a single-user local app is to run the sync SQLite calls in an executor so they don't block the event loop. Ask Claude to show you `asyncio.get_event_loop().run_in_executor(None, ...)` and explain what it does before implementing. Alternatively, for v1 simplicity, you can make these regular (non-async) functions and call them directly — the performance difference is imperceptible for a single user. Decide before coding; document your choice in a comment.

**Completion check:** In a Python REPL or small test script, call `list_projects()` — it should return one row: the default "general" project. Call `set_active_project("kinase")` then `get_active_project()` — it should return `{"name": "kinase", ...}`.

---

### Task 4 — Seed the Default "general" Project

**What to build:** On first boot, if the `projects` table is empty, insert a default project named `"general"` with `is_active = 1`.

**Where this logic lives:** In `backend/database/db.py`, in the initialization function that already runs the schema. After `CREATE TABLE IF NOT EXISTS` runs, check if `projects` is empty and seed if so.

**Tell Claude:** "This must be idempotent — if the server restarts and the project already exists, do not insert again and do not crash. Use `INSERT OR IGNORE` or a `SELECT COUNT(*)` guard."

**Completion check:** Delete `data/jarvis.db`, restart backend, open DB. `SELECT * FROM projects;` should return one row: `(1, 'general', 1, <timestamp>)`.

---

### Task 5 — Wire `/chat` to Save Messages

**What to build:** The `/chat` handler currently returns a response without saving anything. Today it saves the user message and the assistant reply to the `messages` table, both under the active project.

**The updated flow in `backend/api/chat.py`:**

```
1. Receive {message: str} from frontend
2. Get active project → project_id
3. Get or create session conversation → conversation_id
4. Save user message (role='user', content=message, provider=None, model=None)
5. Call LLM router → LLMResponse
6. Save assistant message (role='assistant', content=response.text, provider=response.provider, model=response.model)
7. Return ChatResponse (same as before, plus project_name for UI display)
```

**Minimal diff rule (from CLAUDE.md §3):** only change what's necessary in `chat.py`. Don't reorganize the file, don't rename variables. The diff should show ~15–20 lines added, nothing removed except the bare-minimum that needs replacing.

**Update `ChatResponse` model** in `backend/models/chat.py` to add:
```python
project_name: str | None = None   # so the frontend can show which project is active
```

**Completion check:**
1. Send a message via curl or the React frontend.
2. Open `data/jarvis.db` and run: `SELECT role, content, provider FROM messages ORDER BY created_at;`
3. You should see two rows: one with `role='user'`, one with `role='assistant'` and the correct provider name.

---

### Task 6 — Wire `cost_tracker.py` to Actually Insert into `cost_log`

**What to build:** `cost_tracker.record()` currently logs a structured line to disk. Today it also `INSERT`s into the `cost_log` table. The call site in `router.py` does not change — only the implementation of `record()` changes.

**The updated `record()` function must:**
- Keep the existing `logger.info(...)` line (do not remove it — log file is still useful)
- Add an `INSERT INTO cost_log (provider, model, prompt_tokens, completion_tokens, estimated_usd, request_id, created_at)` call
- Use `datetime.utcnow().isoformat()` for `created_at`
- Wrap in try/except — if the DB write fails, log the error but do NOT propagate the exception (a cost tracking failure should never break a chat response)

**Completion check:** Make a `/chat` call, then query: `SELECT provider, model, prompt_tokens, estimated_usd FROM cost_log;` — you should see one row per API call made.

---

### Task 7 — Create `scripts/set_project.py`

**What to build:** A tiny standalone script (not a FastAPI endpoint) that switches the active project from the command line. Used for quick manual testing and Day 21 project-memory tools.

**Usage:**
```powershell
python scripts/set_project.py kinase
# Output: Active project set to "kinase" (created new project)

python scripts/set_project.py general
# Output: Active project set to "general" (existing project)
```

**The script:**
- Imports `sqlite_store` directly (no FastAPI, no async — just plain sync SQLite for simplicity)
- Accepts one positional argument: the project name
- Calls the equivalent of `set_active_project(name)` via direct SQL (or imports the function)
- Prints a clear confirmation message
- Exits with code 1 and a helpful error if no argument is given

**Note for Claude:** "This script should not import FastAPI or uvicorn. It's a standalone maintenance script. Keep it under 40 lines."

**Completion check:** Run `python scripts/set_project.py kinase`. Then open the DB and confirm: only the `kinase` row has `is_active = 1`. All others have `is_active = 0`.

---

## Completion Criteria

Run through every checkbox before committing.

| # | Criterion | How to verify |
|---|---|---|
| C1 | DB file created at `data/jarvis.db` on backend boot | Delete DB, restart server, check `data/` |
| C2 | All 6 tables present with correct columns | `sqlite3 data/jarvis.db ".schema"` |
| C3 | Default "general" project seeded with `is_active=1` | `SELECT * FROM projects;` |
| C4 | Foreign keys are ON | `PRAGMA foreign_keys;` → returns `1` |
| C5 | Chat messages saved with correct `project_id` | `SELECT role, project_id FROM messages;` |
| C6 | Both user and assistant messages persisted per `/chat` call | Two rows per call in `messages` |
| C7 | `cost_log` has one row per LLM API call | `SELECT * FROM cost_log;` |
| C8 | `set_project.py kinase` switches active project | `SELECT name, is_active FROM projects;` |
| C9 | DB survives backend restart (data persists) | Restart uvicorn; query `messages` again |
| C10 | You can explain what `PRAGMA foreign_keys = ON` does and why it matters | Explain it out loud before committing |

---

## Heads-Up: Things That Will Trip You Up

**SQLite `check_same_thread=False`:** FastAPI's async handling can call SQLite from different threads internally. Without this flag you'll get cryptic `ProgrammingError: SQLite objects created in a thread can only be used in that same thread` errors. You won't hit this immediately, but you will hit it once the voice loop (Day 11) starts calling the DB from background threads.

**`is_active` uniqueness:** SQLite has no native "only one row can be 1" constraint. Your `set_active_project()` must do two statements in a transaction: first `UPDATE projects SET is_active = 0 WHERE 1`, then `UPDATE projects SET is_active = 1 WHERE name = ?`. If you forget the transaction, a crash mid-update leaves no active project and the app breaks silently on next startup. Tell Claude to use `conn.execute("BEGIN")` + `conn.commit()` or a `with conn:` context manager.

**`CREATE TABLE IF NOT EXISTS` is your safety net:** The schema initialization runs on every startup. The `IF NOT EXISTS` clause makes it idempotent. Do not use `CREATE TABLE` without it — you'll get an error on second boot.

**Don't chase Day 6 today:** ChromaDB will mirror some of what goes into the `memory` table. The `chroma_id` column in `memory` is intentionally `NULL` today — Day 6 will populate it. Do not pre-wire ChromaDB today even if it seems close. The `memory` table exists in the schema so the column is ready; that's all.

**Python 3.13 risk window opens on Day 6:** `sqlite3` is stdlib and works fine on 3.13. But ChromaDB (next day) is the first high-risk third-party install. If you have any time left today, try `pip install chromadb` in your venv and confirm it installs without errors. If it fails, you want to know now, not mid-Day-6. Don't wire it — just install and note the result in `docs/journal.md`.

---

## How to Verify End-to-End

After completing all tasks, do this manual walkthrough in order:

```powershell
# 1. Fresh start
# Delete data/jarvis.db if it exists
Remove-Item data/jarvis.db -ErrorAction SilentlyContinue

# 2. Boot the backend
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 3. Confirm DB created and seeded
# (new terminal)
sqlite3 data/jarvis.db "SELECT name, is_active FROM projects;"
# Expected: general|1

# 4. Send a chat message
curl -X POST http://localhost:8000/chat `
  -H "Content-Type: application/json" `
  -d "{\"message\": \"What is a kinase?\"}"
# Expected: {"reply": "...", "provider": "gemini", "model": "...", "project_name": "general", ...}

# 5. Confirm messages saved
sqlite3 data/jarvis.db "SELECT role, substr(content,1,40), provider FROM messages;"
# Expected: Two rows — user (no provider) and assistant (gemini or groq)

# 6. Confirm cost logged
sqlite3 data/jarvis.db "SELECT provider, model, prompt_tokens, estimated_usd FROM cost_log;"
# Expected: One row with real token counts

# 7. Switch project
python scripts/set_project.py kinase
sqlite3 data/jarvis.db "SELECT name, is_active FROM projects;"
# Expected: general|0 and kinase|1

# 8. Send another message — should save under kinase project_id
curl -X POST http://localhost:8000/chat `
  -H "Content-Type: application/json" `
  -d "{\"message\": \"Tell me about T315I mutation\"}"
sqlite3 data/jarvis.db "SELECT project_id, role, substr(content,1,30) FROM messages ORDER BY created_at;"
# Expected: first two rows have project_id=1 (general), last two have project_id=2 (kinase)

# 9. Restart and confirm persistence
# Ctrl+C the uvicorn server, restart it
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
sqlite3 data/jarvis.db "SELECT COUNT(*) FROM messages;"
# Expected: 4 (your messages survived the restart)
```

---

## Git Commit

Once all 10 completion criteria pass:

```
feat: sqlite schema with project-scoped storage

- Add schema.sql with 6 tables: projects, conversations, messages,
  memory, tasks, cost_log
- Add db.py: connection setup, PRAGMA foreign_keys ON, schema init,
  default general project seeding
- Add sqlite_store.py: get/set_active_project, list_projects,
  save_message, get_or_create_session_conversation
- Wire /chat to persist user + assistant messages under active project
- Add project_name to ChatResponse model
- Complete cost_tracker.py TODO: INSERT into cost_log on every LLM call
- Add scripts/set_project.py for CLI project switching
```

---

## Glance at Day 6 (Tomorrow)

Day 6 introduces ChromaDB for semantic memory. It uses the `memory` table you created today and populates `chroma_id`. Importance scoring (1–10 via LLM) decides what gets stored — trivial messages like "hi" will score below the threshold and skip storage. The `/chat` flow will grow two steps: search ChromaDB before calling the LLM, and score + store the result after.

The `project_id` on every memory row (designed today) is what makes Day 6's cross-project isolation work without any schema changes. That's the payoff for getting the schema right on Day 5.
