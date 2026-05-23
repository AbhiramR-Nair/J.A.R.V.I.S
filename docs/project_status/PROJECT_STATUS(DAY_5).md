# Project Status — Day 5

**Period covered:** Day 5 (SQLite Schema & Persistent Storage)
**Status:** Complete — all 10 completion criteria met. Uncommitted at time of writing.
**Environment:** Windows 11, Python 3.13.5, Node 24.15.0, Git 2.52.0

> Checkpoint summary for Day 5: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 6.

---

## 1. What has been done

Day 5 gave Jarvis a memory that survives restarts. Before today every `/chat` request
started from a blank slate; now every user message and assistant reply lands in
`data/jarvis.db`, scoped to a project, and the `cost_log` table records every paid API
call for the first time. A standalone CLI script (`scripts/set_project.py`) lets you
switch the active project from outside the running server — useful for manual testing
and a precursor to Day 21's project-memory voice tools.

The whole point of Day 5 is enforcing the **project-scoping rule from Version_1_plan.md
§Non-Negotiable Rules #5** at the storage layer. Every row in `messages`, `memory`, and
`tasks` now carries a `project_id`. ChromaDB (Day 6) and tool calling (Day 20) inherit
this structure for free.

| Task | What landed | Status |
|---|---|---|
| 1 — `backend/database/schema.sql` | 6 tables: `projects`, `conversations`, `messages`, `memory`, `tasks`, `cost_log`. All idempotent (`IF NOT EXISTS`) | Done, verified |
| 2 — `backend/database/db.py` | Connection singleton, `check_same_thread=False`, `row_factory=sqlite3.Row`, schema init, `PRAGMA foreign_keys ON` | Done, verified |
| 3 — `backend/memory/sqlite_store.py` | 7 sync functions: `get_active_project`, `set_active_project`, `list_projects`, `save_message`, `get_conversation_messages`, `create_conversation`, `get_or_create_session_conversation` | Done, verified |
| 4 — Seed "general" project | Folded into `db.py` `_seed_defaults()` — `INSERT OR IGNORE` runs only on first boot | Done, verified |
| 5 — Wire `/chat` to persist exchanges | `backend/api/chat.py` saves user message → calls LLM → saves assistant message. `project_name` added to response | Done, verified |
| 6 — `cost_tracker.py` writes to `cost_log` | SQL `INSERT` added after the existing `logger.info()`. Wrapped in `try/except` so a DB failure can't break chat | Done, verified |
| 7 — `scripts/set_project.py` | Standalone CLI, 50 lines, direct `sqlite3` (no FastAPI/uvicorn import) | Done, verified |
| Settings | `db_path: str = "data/jarvis.db"` added to `settings.py` | Done |
| Startup hook | `@app.on_event("startup")` in `main.py` calls `get_db()` so DB init runs on boot, not on first request | Done |
| Model update | `ChatResponse` extended with `project_name: str \| None = None` | Done |

**Completion criteria verified:**

| Criterion | Result |
|---|---|
| C1 — DB file created at `data/jarvis.db` on boot | ✅ Log line: `database: created new DB at data\jarvis.db` |
| C2 — All 6 tables present with correct columns | ✅ `.tables` returns all six |
| C3 — Default "general" project seeded with `is_active=1` | ✅ `1\|general\|1` |
| C4 — Foreign keys ON in Python connection | ✅ Returns `1` when queried from Python (CLI shows `0` — see P1) |
| C5 — Messages saved with correct `project_id` | ✅ `project_id=1` for general, `project_id=2` for kinase |
| C6 — Both user and assistant messages per `/chat` call | ✅ 2 rows per call confirmed |
| C7 — `cost_log` has one row per LLM API call | ✅ `gemini\|gemini-2.5-flash\|6\|0.000216` |
| C8 — `set_project.py kinase` switches active project | ✅ general→0, kinase→1 |
| C9 — Data survives backend restart | ✅ 4 messages before and after restart; log line: `opened existing DB` |
| C10 — Can explain `PRAGMA foreign_keys = ON` and why it matters | User self-check (see §2.1 for reference) |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. Plain sync functions in `sqlite_store.py`, not async

`sqlite3` is a synchronous standard-library module. The plan offered two options: wrap
every call in `asyncio.run_in_executor()` to free the event loop, or keep functions
plain `def`. For a single-user local app where DB calls take sub-millisecond, the
executor option is pure boilerplate with no observable benefit. I went with plain sync.
A header comment in `sqlite_store.py` documents the choice so future-me doesn't wonder.

Migration path if this ever changes: swap `sqlite3` for `aiosqlite`, add `async def` and
`await` to all function signatures. Callers in `chat.py` already expect async semantics
(they currently call without `await`, so a refactor would add the keyword).

### 2. Module-level singleton connection

`get_db()` opens the connection once on first call and reuses it. SQLite connections
are cheap (no network handshake) but reusing one keeps PRAGMAs consistent and gives a
single audit point for connection-level settings. `check_same_thread=False` is set
because FastAPI's async internals may dispatch sync calls from different threads — safe
here because the app is single-user with no concurrent writes.

### 3. `row_factory = sqlite3.Row` set once on the connection

This makes `row["name"]` work instead of `row[1]`. The helper `_row_to_dict()` then
returns plain `dict` objects to callers — none of the API layer or services see
SQLite-specific types. One line of setup, much cleaner downstream code.

### 4. Schema runs first, then `PRAGMA foreign_keys = ON` via `executescript()`

This ordering is the fix for P1 (see below). The schema runs first because
`executescript()` issues an implicit `COMMIT` and that's required *before* setting
`PRAGMA foreign_keys`. Then the PRAGMA itself is set via a second `executescript()`
call rather than `conn.execute()` — `executescript()` runs outside Python's implicit
transaction management, which is also required for the PRAGMA to actually take effect.

### 5. Denormalized `project_id` in `messages` table

`messages.project_id` is technically derivable via `messages.conversation_id →
conversations.project_id`. The denormalization (storing it on both rows) trades a
small storage cost for dramatic query simplicity: "give me everything in project X"
becomes `WHERE project_id = X` instead of a JOIN. Day 21 (project memory tools) will
hammer this query pattern; the denormalization makes those queries fast and the code
trivial.

### 6. `set_active_project()` wrapped in `with conn:` transaction

The function does three statements: `INSERT OR IGNORE` the project if new, `UPDATE
is_active = 0` on all rows, `UPDATE is_active = 1 WHERE name = ?`. If the process
crashes between statements 2 and 3, the DB would have zero active projects — the next
boot would crash on `get_active_project()`. The `with conn:` context manager treats all
three as one transaction: commit on clean exit, rollback on exception. Crash-safe.

### 7. Startup hook initializes DB before first request

Originally the connection was lazy (initialized on first call to `get_db()`). I added
`@app.on_event("startup")` calling `get_db()` so the schema and seed run during boot.
Cleaner logs (DB init lines appear with the startup banner, not interleaved with the
first request) and easier to diagnose DB problems before any traffic arrives.

### 8. `cost_tracker.record()` keeps the log line *and* writes to DB

The existing `logger.info("llm_call", ...)` was not removed — `data/logs/jarvis.log`
remains useful for quick `grep` queries that don't need a SQL prompt. The new
`INSERT INTO cost_log` is additive and wrapped in `try/except`: a DB write failure logs
an error and returns silently, never propagating. Cost tracking is best-effort by
design — losing a row of cost data is fine; breaking a chat response is not.

### 9. `set_project.py` uses direct SQL, not `sqlite_store` imports

The plan allowed either. Direct SQL keeps the script self-contained — no PYTHONPATH
gymnastics, no risk of triggering FastAPI middleware setup, no `loguru` config side
effects. The script is 50 lines and reads `data/jarvis.db` relative to the working
directory (always run from repo root). The transaction pattern matches
`set_active_project()` exactly so they can never disagree about what "switch project"
means.

---

## 3. Problems faced and how they were handled

### P1 — `PRAGMA foreign_keys = ON` silently a no-op *(impact: high, resolved)*

- **What:** Initial `db.py` used `conn.execute("PRAGMA foreign_keys = ON")` placed
  *before* `executescript(schema_sql)`. C4 verification returned `0` — foreign keys
  were not actually enforced, which would break referential integrity for the entire
  app silently. Any `INSERT INTO messages (... conversation_id=999 ...)` referencing a
  non-existent conversation would succeed without error.
- **Cause:** Python's `sqlite3` module wraps non-DDL statements in implicit
  transactions by default. SQLite's own documentation states `PRAGMA foreign_keys` is
  a **no-op when issued inside a transaction**. Python's transaction wrapping was
  causing the PRAGMA to be silently ignored. Worse, `executescript()` running after
  it would commit the transaction, but the PRAGMA was already lost.
- **Handled:** Two fixes layered:
  1. Run the schema first via `executescript(schema_sql)`. This commits any pending
     implicit transaction as a side effect.
  2. Set the PRAGMA via a *second* `executescript("PRAGMA foreign_keys = ON;")` call.
     Because `executescript()` always runs outside transaction control, the PRAGMA
     takes effect.
  Comment in `db.py` documents the *why* so this isn't accidentally "fixed back".
- **False-alarm twist:** After applying the fix, `sqlite3 data/jarvis.db "PRAGMA
  foreign_keys;"` from the command line *still* returned `0`. This is not a bug —
  the `sqlite3` CLI opens its own connection with foreign keys OFF by default, so it
  reports its own state, not the Python app's. Verified the actual fix by querying
  the PRAGMA from inside Python (`get_db().execute("PRAGMA foreign_keys;").fetchone()`
  returns `1`).
- **Verified:** C4 passes when queried from Python. The CLI false negative is now a
  documented gotcha (see §4 below).

### P2 — `datetime.utcnow()` deprecation warning *(impact: low, resolved)*

- **What:** IDE flagged `datetime.utcnow()` as deprecated in Python 3.12+. Uses live
  in `sqlite_store.py:get_or_create_session_conversation()` and `cost_tracker.py:record()`.
- **Cause:** Python 3.12 deprecated `utcnow()` in favour of timezone-aware
  `datetime.now(timezone.utc)`. Naive UTC datetimes are an ongoing source of bugs
  across the ecosystem; the standard library is pushing everyone toward explicit tz.
- **Handled:** Replaced both call sites with `datetime.now(timezone.utc)`. Added
  `timezone` to the import line. Behaviour is identical for our use (we only use
  these to build ISO strings); the returned object is now timezone-aware, which is a
  correctness upgrade.
- **Verified:** No more deprecation hints in IDE; ISO strings still parse correctly.

---

## 4. Heads-up: downstream complications to watch

### From P1 — every future SQLite connection must follow this PRAGMA pattern

If any future code opens a new `sqlite3.connect()` (e.g. a background worker, a
test harness, a migration script), it must use the same `executescript("PRAGMA
foreign_keys = ON;")` pattern. Using `conn.execute()` for the PRAGMA will silently
fail and foreign keys won't be enforced — and there will be no error to catch it.
`scripts/set_project.py` does NOT need this because it doesn't rely on FK enforcement
(its only writes are to `projects`, which has no foreign keys *out*), but anything that
inserts into `messages`/`memory`/`tasks`/`conversations` does. **Watch for:** rows
inserted with invalid `conversation_id` or `project_id` going undetected.

### From P1 — the sqlite3 CLI is misleading for diagnostics

The CLI's `PRAGMA foreign_keys;` will always show `0` regardless of what the app is
doing. Anyone debugging foreign-key issues by querying from the CLI will be misled.
The diagnostic that actually answers the question is: from a Python REPL, import
`get_db` and run `get_db().execute("PRAGMA foreign_keys;").fetchone()`. Consider
adding this as a one-liner in `docs/setup.md` when the day's notes are folded in.

### `is_active` uniqueness is enforced in Python, not SQL

The "exactly one project is active" rule is enforced only by `set_active_project()`
and `scripts/set_project.py`. Any code that does a direct `UPDATE projects SET
is_active = ...` outside those paths can leave 0 or >1 rows active. Day 21 will add
voice tools that switch projects; those must go through `set_active_project()`, not
raw SQL. `get_active_project()` raises `RuntimeError` if zero active projects, which
will at least surface the corruption loudly. **Watch for:** voice-command paths Day 21
that bypass the helper.

### `messages.project_id` denormalization breaks if conversations move

The denormalized `project_id` on every message assumes a conversation never changes
projects. v1 has no "move conversation to a different project" feature, so this is
safe. If we ever add one, both `conversations.project_id` AND every
`messages.project_id` for that conversation need updating in a single transaction.
**Watch for:** Day 21 features that re-categorize conversations.

### Persistent connection has no shutdown handler

`_conn` lives for the app's lifetime; there's no `@app.on_event("shutdown")` calling
`_conn.close()`. SQLite handles process termination gracefully (WAL flushed, file
released), so this isn't a bug, but a clean shutdown path may be wanted later. Not
urgent.

### UTC vs local time in `session_*` conversation names

`get_or_create_session_conversation()` uses `datetime.now(timezone.utc)` for both the
session name and the `date('now')` SQL comparison. `date('now')` in SQLite is also
UTC. So the two agree — but they're both UTC, not local time. For a user in IST
(UTC+5:30), a conversation started at 04:00 local time would carry the previous day's
date stamp and might be grouped with yesterday's session. Probably fine for a personal
tool; flag if it becomes annoying.

### `cost_tracker` failures are silent (by design)

If a `cost_log` `INSERT` fails (disk full, DB locked, schema drift), the row is lost
and only `data/logs/jarvis.log` records the error. No alerting, no retry. This is
correct — cost tracking must never break chat — but it means cost numbers in any
future "monthly spend" report could undercount. **Watch for:** unexplained gaps in
`cost_log.created_at` sequences during heavy use.

### Day 6 ChromaDB will mirror this schema

The `memory` table already has `project_id` and a NULL `chroma_id` column waiting.
Day 6 must:
1. Use the same `project_id` values in ChromaDB metadata (so cross-store filtering
   agrees)
2. Populate `memory.chroma_id` after a successful ChromaDB insert
3. Handle the failure mode where SQLite insert succeeds but ChromaDB insert fails
   (orphan `memory` row with NULL `chroma_id` — acceptable, retry-able)

### Python 3.13 risk window — ChromaDB is tomorrow's install

Day 5 stayed within standard library (`sqlite3`), so no new compatibility risk. The
risk window opens on Day 6 with `pip install chromadb`. The Day 5 plan suggested
trying that install today as a sanity check. I did not do this — flagging here so it's
the first thing to verify Day 6.

---

## 5. Open items before Day 6

- [ ] Sanity-check `pip install chromadb` in the venv before Day 6 starts proper —
      surfaces any Python 3.13 incompatibility while there's still buffer time
- [ ] Commit Day 5 work — suggested message in §7 below
- [ ] (Optional) `http://localhost:8000/docs` — confirm `/chat` response schema now
      shows `project_name` field
- [ ] C10 comprehension self-check: explain out loud why `PRAGMA foreign_keys = ON`
      matters (referential integrity), why it must be set per-connection (it's not
      stored in the DB file), and why our specific call had to go through
      `executescript()` (implicit transactions + PRAGMA-in-transaction = no-op)

---

## 6. How to verify Day 5

```powershell
# 1. Fresh start
Remove-Item data/jarvis.db -ErrorAction SilentlyContinue
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 2. Confirm DB created and seeded
sqlite3 data/jarvis.db "SELECT name, is_active FROM projects;"
# Expected: general|1

# 3. Foreign keys actually ON (from Python, NOT the sqlite3 CLI)
python -c "from backend.database.db import get_db; print(get_db().execute('PRAGMA foreign_keys;').fetchone()[0])"
# Expected: 1

# 4. Send a chat message
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"message\": \"What is a kinase?\"}"
# Expected: response includes "project_name": "general"

# 5. Messages saved
sqlite3 data/jarvis.db "SELECT role, substr(content,1,40), provider FROM messages;"
# Expected: two rows — user (no provider) and assistant (gemini/groq)

# 6. Cost logged
sqlite3 data/jarvis.db "SELECT provider, model, prompt_tokens, estimated_usd FROM cost_log;"
# Expected: one row per LLM call

# 7. Switch project
python scripts/set_project.py kinase
sqlite3 data/jarvis.db "SELECT name, is_active FROM projects;"
# Expected: general|0 and kinase|1

# 8. Send another message — project_id should now be 2
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"message\": \"Tell me about T315I mutation\"}"
sqlite3 data/jarvis.db "SELECT project_id, role, substr(content,1,30) FROM messages ORDER BY created_at;"
# Expected: first two rows project_id=1, last two project_id=2

# 9. Restart and confirm persistence
# Ctrl+C the uvicorn server, restart it
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
sqlite3 data/jarvis.db "SELECT COUNT(*) FROM messages;"
# Expected: 4
```

---

## 7. Commit log for this period

Not yet committed at time of writing. Suggested message (from the Day 5 plan):

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
- Fix PRAGMA foreign_keys ordering: must run via executescript() after
  schema, otherwise Python's implicit transactions make it a no-op
```

> Note: `backend/desktop.py` remains modified and intentionally uncommitted
> (carried from pre-Day 3). `docs/plans/day_5_plan.md` and this file are
> untracked and left for the user to add when desired.
