# Day 6 Plan — ChromaDB Semantic Memory

**Day:** 6 of 30
**Week:** 1 (Foundation + PyWebView Shell)
**Estimated time budget:** 6 hours
**Prerequisite:** Day 5 committed (`feat: sqlite schema with project-scoped storage`)

---

## What this day is about

Day 5 gave Jarvis a relational memory — every message is saved in SQLite and survives
restarts. Day 6 gives Jarvis a *semantic* memory: the ability to recall information
by meaning rather than exact match. When you later ask "what was my project about?",
the assistant retrieves the most relevant facts you stored earlier, even if you phrase
the question differently. This is what separates a stateful chatbot from something that
feels genuinely intelligent over time.

The technical mechanism is **vector search via ChromaDB**, backed by **Gemini
embeddings** (`text-embedding-004`). You write a note; Jarvis converts it to a
768-dimensional vector and stores it alongside SQLite metadata. On the next conversation,
before calling the LLM, Jarvis searches ChromaDB for the top 3 semantically relevant
memories for your current message and injects them as context. The LLM sees relevant
history it couldn't have reconstructed from the most recent turns alone.

**Why this matters for the portfolio:** project-scoped semantic memory over custom
domain knowledge (kinase mutations, protein structures, experimental results) is the
specific feature that makes this assistant useful for computational biology work — and
clearly distinguishable from "a ChatGPT wrapper" to any interviewer.

---

## Before you start — pre-flight checklist

Work through these before writing any code. They take 15 minutes and prevent wasted
hours.

### Pre-flight 1 — Commit Day 5

If Day 5 is still uncommitted, do this first:

```powershell
git add backend/database/ backend/memory/sqlite_store.py backend/api/chat.py
git add backend/services/cost_tracker.py backend/config/settings.py
git add backend/models/chat.py scripts/set_project.py
git commit -m "feat: sqlite schema with project-scoped storage

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
  schema, otherwise Python implicit transactions make it a no-op"
```

### Pre-flight 2 — ChromaDB install sanity check (Python 3.13 risk)

This is the highest-risk step of the day. ChromaDB's Python package has historically
had native C extension components. Python 3.13 is new enough that wheels may not exist
yet, requiring a local compile — which needs a C++ build toolchain. Find out now, not
after 2 hours of other work.

```powershell
# With your venv activated
pip install chromadb
```

**Outcome A (happy path):** installs cleanly, no red text. Move on.

**Outcome B (build error, missing wheels):** You'll see something like
`error: Microsoft Visual C++ 14.0 or greater is required`. Resolution options:

1. Install Build Tools for Visual Studio 2022 (free):
   https://visualstudio.microsoft.com/visual-cpp-build-tools/
   — select "C++ build tools" workload only. Then retry `pip install chromadb`.

2. If that's too slow or fails, try pinning an older ChromaDB that has pre-built wheels:
   `pip install "chromadb==0.4.24"` — this version has broad Python support and
   all the features this project needs.

3. Last resort: open an issue in the build log and check
   https://github.com/chroma-core/chroma/releases for the most recent version
   with a Python 3.13 wheel.

After a successful install, freeze the version immediately:
```powershell
pip freeze | Select-String "chromadb" >> backend/requirements.txt
# Or update manually in requirements.txt — but pin the exact version number.
```

### Pre-flight 3 — Verify Gemini embeddings work

Before writing any app code, confirm that the embedding API call you're about to rely
on actually works with your key and SDK version:

```python
# Run this from a Python REPL (not a .py file) to keep it throwaway
import google.generativeai as genai
import os
from dotenv import load_dotenv
load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
result = genai.embed_content(
    model="models/text-embedding-004",
    content="ABL1 kinase T315I resistance mutation",
    task_type="retrieval_document"
)
print(type(result["embedding"]))   # should be list
print(len(result["embedding"]))    # should be 768
```

If this returns a 768-element list, you're ready. If it raises, fix the API key or
SDK call before proceeding.

---

## Architecture overview — what you're building

```
/chat request (user message)
       │
       ▼
[1] vector_store.search(query=user_message, project_id=active, k=3)
       │  returns: list of relevant strings from past conversations
       ▼
[2] LLM call with injected memory context
       │  system prompt now includes: "Relevant context: <top 3 memories>"
       ▼
[3] LLM response
       │
       ▼
[4] importance.score(user_message + assistant_response)
       │  returns: integer 1-10
       ▼
[5] if score >= 4:
       vector_store.add(text, project_id, metadata)   → ChromaDB
       sqlite_store.save_memory(text, project_id, chroma_id, score)  → SQLite
```

Three new files today:
- `backend/memory/vector_store.py` — ChromaDB wrapper
- `backend/memory/importance.py` — LLM-based scoring
- Update `backend/api/chat.py` — inject memory into LLM call, store after

One rule that must hold throughout: **every ChromaDB operation is tagged with
`project_id` as metadata**, so search results can be filtered to the active project.
Cross-project leakage would be a silent correctness bug that only surfaces when you
switch between your kinase project and a fitness project and start getting confused
answers.

---

## Task 1 — `backend/memory/vector_store.py`

**Time estimate:** 90 minutes
**File to create:** `backend/memory/vector_store.py`

This file wraps ChromaDB. The rest of the app never imports ChromaDB directly — it
only calls these functions. That isolation means you could swap ChromaDB for another
vector DB later without touching a single other file.

### What to build

A module with four things:

1. **`_get_client()`** — returns a ChromaDB persistent client pointing to
   `data/chroma/` (from settings). Created once; subsequent calls return the same
   instance. Same singleton pattern as `db.py`.

2. **`_get_collection(project_id: int)`** — returns (or creates) a ChromaDB
   collection named `"project_{project_id}"`. Separate collection per project is the
   cleanest way to enforce isolation — ChromaDB's metadata filtering also works, but
   separate collections are simpler to reason about and simpler to delete if a project
   is ever purged.

3. **`add(text: str, project_id: int, metadata: dict | None = None) -> str`** —
   embeds `text` using Gemini `text-embedding-004`, inserts into the project's
   collection, returns the ChromaDB document ID (a UUID). The `metadata` dict should
   always include at minimum `{"project_id": project_id, "created_at": <iso timestamp>}`.
   Callers can pass additional metadata (e.g. `{"role": "assistant", "importance": 7}`).

4. **`search(query: str, project_id: int, k: int = 5) -> list[str]`** — embeds
   `query`, queries the project's collection for the `k` nearest neighbours, returns
   a plain list of strings (the original texts). If the collection is empty or has fewer
   than `k` documents, return however many exist — do not raise.

### Key implementation details to discuss with Claude before writing

Before asking Claude to write this file, you need to make one architectural decision:

> **How do we call the Gemini embedding API from inside ChromaDB?**
>
> Option A — **Custom Gemini embedding function** passed to ChromaDB: ChromaDB accepts
> a callable `embedding_function` argument when you create/get a collection. You write
> a class that wraps `genai.embed_content()` and ChromaDB calls it automatically on
> every `add()` and `query()`. Pro: clean API, ChromaDB handles the plumbing. Con:
> ChromaDB's embedding function interface requires a specific class shape.
>
> Option B — **Embed outside ChromaDB** and pass raw vectors: call
> `genai.embed_content()` yourself, get the 768-float list, and pass it to
> `collection.add(embeddings=[...])` directly. Pro: explicit, easy to debug, no
> magic class interface. Con: slightly more code per call.
>
> **Recommended for this project:** Option B. More transparent, easier to debug,
> and the extra code is just a helper function `_embed(text: str) -> list[float]`.
> You can see exactly what's happening at each step. Tell Claude to use Option B
> and write a `_embed()` private helper.

### Embedding task types — important detail

Gemini `text-embedding-004` has task types that affect embedding quality:

| Call type | `task_type` to use |
|---|---|
| Storing a document (in `add()`) | `"retrieval_document"` |
| Querying for similar documents (in `search()`) | `"retrieval_query"` |

Using the wrong task type gives slightly worse results. It's a one-word difference
in the API call — make sure both `add()` and `search()` use the right one.

### Error handling requirements

```python
# Every external call must be wrapped. Template:
try:
    embedding = _embed(text, task_type="retrieval_document")
except Exception as e:
    logger.error(f"vector_store.add failed for project {project_id}: {e}")
    raise   # let the caller decide whether to surface or swallow
```

Do NOT silently swallow errors here. The caller (`chat.py`) will decide whether to
surface the failure to the user or skip semantic memory for this turn.

### ChromaDB distance metric

When creating a collection, pass `metadata={"hnsw:space": "cosine"}`. Cosine
similarity is the right metric for sentence embeddings — it measures angular distance
(semantic direction) rather than magnitude. The default (L2) would give worse recall
for this use case.

### What the completed file should look like (structure sketch)

```python
# backend/memory/vector_store.py

# [module-level docstring explaining what this file does]

import uuid
from datetime import datetime, timezone
from loguru import logger
import chromadb
import google.generativeai as genai

from backend.config.settings import get_settings

settings = get_settings()
_client: chromadb.PersistentClient | None = None


def _get_client() -> chromadb.PersistentClient: ...   # singleton
def _get_collection(project_id: int) -> chromadb.Collection: ...   # per-project
def _embed(text: str, task_type: str) -> list[float]: ...   # Gemini embed_content
def add(text: str, project_id: int, metadata: dict | None = None) -> str: ...
def search(query: str, project_id: int, k: int = 5) -> list[str]: ...
```

### Completion check for Task 1

Run this from a Python REPL after the file is written (backend uvicorn does not need
to be running):

```python
from backend.memory import vector_store

# Add three facts to project 1
id1 = vector_store.add("ABL1 kinase T315I mutation causes 40-fold resistance to imatinib", project_id=1)
id2 = vector_store.add("The gatekeeper residue T315 blocks the binding pocket for most TKIs", project_id=1)
id3 = vector_store.add("Ponatinib is effective against T315I because it avoids the gatekeeper", project_id=1)

# Add an unrelated fact to project 2
vector_store.add("Bench press 3 sets of 8 at 70 kg", project_id=2)

# Search project 1 for T315I-related info
results = vector_store.search("what do we know about T315I?", project_id=1, k=2)
print(results)
# Should return the two most relevant of the three kinase facts

# Confirm project isolation
results_p2 = vector_store.search("what do we know about T315I?", project_id=2, k=3)
print(results_p2)
# Should return the bench press fact (unrelated) or empty list — NOT kinase facts
```

If this passes, Task 1 is done.

---

## Task 2 — `backend/memory/importance.py`

**Time estimate:** 45 minutes
**File to create:** `backend/memory/importance.py`

Not every message is worth storing. "Hi", "ok", "thanks" are noise. A conversation
where you explain a research insight is worth keeping. This file asks the LLM to score
a piece of text on a 1-10 scale, and the caller uses that score to decide whether to
store it.

### What to build

A single async function:

```python
async def score(text: str) -> int:
    """
    Ask the LLM to score how worth storing this text is, on a 1-10 scale.
    Returns an integer. Returns 0 on any failure (fail-safe: don't store).
    """
```

### Scoring prompt

The prompt matters a lot here. A vague prompt returns inconsistent scores. Use something
precise like this (tell Claude to use this prompt exactly):

```
You are a memory importance scorer. Rate the following text on a scale of 1-10
for how worth storing it is as a long-term memory for a research assistant.

Scoring guide:
1-3: Trivial (greetings, acknowledgements, simple yes/no, filler)
4-6: Somewhat useful (general facts, loose questions, minor decisions)
7-9: Highly useful (domain-specific facts, decisions, named entities with relationships,
     experimental results, key insights, commitments)
10: Critical (irreplaceable context — project goals, major conclusions, key constraints)

Text to score:
{text}

Respond with ONLY a single integer from 1 to 10. No explanation.
```

### Parsing the response

The LLM might return "7", or "7\n", or "Score: 7", or occasionally something
unexpected. Write a robust parser:

```python
import re

def _parse_score(raw: str) -> int:
    """Extract the first integer 1-10 from the LLM's raw response."""
    match = re.search(r'\b([1-9]|10)\b', raw.strip())
    if match:
        return int(match.group(1))
    return 0   # fail-safe: don't store if unparseable
```

### Error handling

Importance scoring is **best-effort**. If the LLM call fails (network error, quota),
return `0` and log a warning. The chat flow must not crash because scoring failed.

```python
try:
    score_val = await score(combined_text)
except Exception as e:
    logger.warning(f"importance.score failed: {e} — defaulting to 0, will not store")
    score_val = 0
```

### What text to score?

Score the *combination* of the user's message and the assistant's reply — not just
one. This gives the scorer full context. In `chat.py`, build the text like:

```python
combined = f"User: {user_message}\nAssistant: {assistant_reply}"
```

### LLM call for scoring

Use the LLM router (not Gemini directly). The scoring call should use a very low
`max_tokens` value (16 is plenty — you only need a single digit back). Some LLM
providers let you set this; if yours doesn't, it's fine — the response will just
have more tokens in cost_log, but the content will still be a single digit.

### Completion check for Task 2

```python
import asyncio
from backend.memory.importance import score

# Should be high (domain-specific facts)
print(asyncio.run(score("The T315I gatekeeper mutation in ABL1 causes 40-fold imatinib resistance")))
# Expected: 7-10

# Should be low (trivial)
print(asyncio.run(score("ok")))
# Expected: 1-3

# Should be medium
print(asyncio.run(score("What is the capital of France?")))
# Expected: 3-5
```

---

## Task 3 — Update `backend/memory/sqlite_store.py` — `save_memory()`

**Time estimate:** 20 minutes
**File to modify:** `backend/memory/sqlite_store.py`

The `memory` table already exists in the schema (from Day 5). You need a function that
writes to it. This is a **minimal diff** — add one function, touch nothing else.

```python
def save_memory(
    text: str,
    project_id: int,
    importance: int,
    chroma_id: str | None = None,
) -> int:
    """
    Persist a memory entry to SQLite. Returns the new row's rowid.
    chroma_id is NULL if ChromaDB storage failed — row is still saved so we
    know the text exists even if vector search can't find it.
    """
```

The SQL:

```sql
INSERT INTO memory (project_id, content, importance, chroma_id, created_at)
VALUES (?, ?, ?, ?, ?)
```

`created_at` should be `datetime.now(timezone.utc).isoformat()`.

No wrapping this in a transaction — it's a single insert.

### Completion check for Task 3

```python
from backend.memory.sqlite_store import save_memory
from backend.database.db import get_db

rowid = save_memory("Test memory", project_id=1, importance=8, chroma_id="some-uuid")
row = get_db().execute("SELECT * FROM memory WHERE id = ?", (rowid,)).fetchone()
print(dict(row))
# Expected: dict with content, project_id=1, importance=8, chroma_id="some-uuid"
```

---

## Task 4 — Wire semantic memory into `/chat`

**Time estimate:** 60 minutes
**File to modify:** `backend/api/chat.py`

This is where all four tasks connect. The flow you're adding:

```
Before LLM call:
  memories = vector_store.search(user_message, project_id, k=3)
  if memories:
      inject into system prompt

After LLM response:
  combined_text = f"User: {user_message}\nAssistant: {reply}"
  importance = await importance.score(combined_text)
  if importance >= settings.importance_threshold:
      chroma_id = vector_store.add(combined_text, project_id, metadata={...})
      sqlite_store.save_memory(combined_text, project_id, importance, chroma_id)
```

### Minimal diff rule

Do NOT rewrite `chat.py`. The changes are additive:
1. Import the three new modules at the top
2. Add the pre-LLM memory retrieval block
3. Add the post-LLM memory storage block

Everything else stays exactly as-is.

### Memory injection into system prompt

When memories are found, they should be injected into the LLM call as a system-level
context block. The exact format depends on how your LLM router currently handles system
prompts. Ask Claude to look at the current `gemini.py` implementation before writing
this — you want to add context without breaking the existing call structure.

A simple approach that avoids touching the LLM router at all: prepend memories to the
user message as a clearly labelled block:

```python
if memories:
    memory_block = "\n".join(f"- {m}" for m in memories)
    augmented_message = (
        f"[Relevant context from past conversations:]\n{memory_block}\n\n"
        f"[User's current message:]\n{user_message}"
    )
else:
    augmented_message = user_message
# Pass augmented_message to the LLM call instead of user_message
```

This is the simplest approach. It works well and doesn't require changing the router.

### Handling failures gracefully

Memory operations must never break chat. Wrap each block independently:

```python
# Retrieval (before LLM) — failure → proceed without memory, log warning
try:
    memories = vector_store.search(user_message, active_project_id, k=3)
except Exception as e:
    logger.warning(f"vector search failed, proceeding without memory: {e}")
    memories = []

# Storage (after LLM) — failure → log error, do not raise
try:
    importance_val = await importance.score(combined_text)
    if importance_val >= settings.importance_threshold:
        chroma_id = vector_store.add(combined_text, active_project_id, metadata={...})
        sqlite_store.save_memory(combined_text, active_project_id, importance_val, chroma_id)
        logger.info(f"memory stored: importance={importance_val}, chroma_id={chroma_id}")
    else:
        logger.debug(f"memory skipped: importance={importance_val} < threshold")
except Exception as e:
    logger.error(f"memory storage failed (non-fatal): {e}")
```

### Settings to add in `backend/config/settings.py`

Add these two lines (minimal diff):

```python
chroma_persist_dir: str = "data/chroma"
importance_threshold: float = 4.0
```

`importance_threshold` is the cutoff below which we skip storage. `4.0` means
scores of 4, 5, 6... are stored; 1-3 are discarded.

### Completion check for Task 4

After restarting the backend:

```powershell
# 1. Send an important message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"I am working on predicting ABL1 kinase inhibitor resistance using deep learning on mutant structures\"}"

# 2. Check that memory was stored in SQLite
sqlite3 data/jarvis.db "SELECT importance, substr(content,1,80) FROM memory;"
# Expected: one row with importance >= 4

# 3. Send a trivial follow-up
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"ok\"}"

# 4. Check that the trivial message was NOT stored
sqlite3 data/jarvis.db "SELECT COUNT(*) FROM memory;"
# Expected: still 1 (not 2)

# 5. Send a question that should trigger memory retrieval
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"What am I working on?\"}"
# Expected: response mentions ABL1 / kinase / resistance
# Check logs to see the memory retrieval log line
```

---

## Task 5 — Cross-project isolation test

**Time estimate:** 20 minutes
**No new files — manual verification only.**

This is the most important correctness test of the day. The guarantee is: memories
stored in project A must never appear in project B's search results.

```powershell
# 1. Store a kinase fact in project 1 (general is currently active)
# Send via chat with the kinase-related message above, or directly:
python -c "
from backend.memory import vector_store, sqlite_store
cid = vector_store.add('T315I gatekeeper mutation confers pan-TKI resistance except ponatinib', project_id=1)
sqlite_store.save_memory('T315I gatekeeper mutation confers pan-TKI resistance except ponatinib', project_id=1, importance=9, chroma_id=cid)
print('stored in project 1, chroma_id:', cid)
"

# 2. Switch to kinase project (project 2)
python scripts/set_project.py kinase

# 3. Store an unrelated fitness note in project 2
python -c "
from backend.memory import vector_store, sqlite_store
cid = vector_store.add('Morning run: 5 km in 28 minutes, felt strong', project_id=2)
sqlite_store.save_memory('Morning run: 5 km in 28 minutes, felt strong', project_id=2, importance=5, chroma_id=cid)
print('stored in project 2, chroma_id:', cid)
"

# 4. Search project 2 for kinase topics
python -c "
from backend.memory import vector_store
results = vector_store.search('what do we know about kinase mutations?', project_id=2, k=3)
print('Project 2 results:', results)
# Should NOT contain T315I or ponatinib — only the fitness note or empty
"

# 5. Search project 1 for kinase topics
python -c "
from backend.memory import vector_store
results = vector_store.search('what do we know about kinase mutations?', project_id=1, k=3)
print('Project 1 results:', results)
# Should contain the T315I fact
"
```

If project 2 returns kinase facts, isolation is broken — stop and fix before
committing. The most likely cause is using a single ChromaDB collection with metadata
filtering instead of separate per-project collections.

---

## Day 6 completion criteria

These map directly to the criteria in `Day_by_Day_Plan_v2.md`:

| # | Criterion | How to verify |
|---|---|---|
| C1 | ChromaDB persists in `data/chroma/`, survives restart | Stop backend, restart, re-run Task 4 search — same results |
| C2 | 3 stored facts, query retrieves the correct one | Task 1 completion check above |
| C3 | Trivial messages (≤ importance 3) are not stored | Task 4 step 3-4 above |
| C4 | Cross-project isolation: kinase facts don't appear in fitness queries | Task 5 above |
| C5 | Memory is injected into LLM context and affects the response | Task 4 step 5 — "What am I working on?" returns relevant answer |
| C6 | `memory` table in SQLite has rows with `chroma_id` populated | `SELECT * FROM memory;` shows non-NULL chroma_id values |
| C7 | Backend does not crash when ChromaDB or Gemini embedding fails | Manually break the Gemini API key briefly, send a chat message — should get "couldn't retrieve memories" warning in logs, not a 500 error |

---

## Common problems to expect

### "Cannot find module chromadb" after pip install

Cause: installed into system Python instead of venv. Fix:

```powershell
# Confirm venv is active (prompt should show (.venv))
.\.venv\Scripts\Activate.ps1
pip install chromadb
```

### ChromaDB creates `data/chroma/` relative to wrong directory

ChromaDB's `PersistentClient(path=...)` resolves the path relative to the **working
directory at process start**. If you always start uvicorn from the repo root
(`python -m uvicorn backend.main:app`), `"data/chroma"` resolves correctly. If you
ever `cd backend` and run from there, it won't. Fix: use an absolute path derived
from settings:

```python
import os
chroma_path = os.path.abspath(settings.chroma_persist_dir)
client = chromadb.PersistentClient(path=chroma_path)
```

### Gemini embedding rate limits

`text-embedding-004` has a free-tier rate limit. During testing you'll call it many
times in quick succession. If you hit a `429 Resource Exhausted` error: wait 60
seconds, reduce your test data size, or add a `time.sleep(0.5)` between batch inserts
in your manual test scripts. In production (single user, one message at a time)
this won't be an issue.

### `collection.query()` returns empty results for obvious matches

Most likely cause: task_type mismatch. If you used `"retrieval_document"` for both
`add()` and `query()`, the embeddings are optimized for different semantics and
retrieval quality drops. Make sure `add()` uses `"retrieval_document"` and
`search()` uses `"retrieval_query"`.

### ChromaDB collection already exists with wrong distance metric

If you test, find the cosine setting is wrong, delete `data/chroma/`, and restart —
you'll start fresh. ChromaDB collection metadata (including `hnsw:space`) is set at
creation and cannot be changed retroactively. If the collection already exists, get
it without metadata; if it doesn't exist, create it with cosine. The
`get_or_create_collection()` API handles this:

```python
collection = client.get_or_create_collection(
    name=collection_name,
    metadata={"hnsw:space": "cosine"},   # ignored if collection already exists
)
```

---

## Git commit for today

After all 5 tasks pass their completion checks, commit:

```
feat: semantic memory with importance scoring

- Add vector_store.py: ChromaDB wrapper with project-scoped add/search
  using Gemini text-embedding-004 (task-typed embeddings)
- Add importance.py: LLM-scored 1-10 filter; skips storage below threshold
- Add save_memory() to sqlite_store.py; populates memory.chroma_id
- Update /chat to retrieve top-3 relevant memories before LLM call
  and store important exchanges after; memory failures are non-fatal
- Add chroma_persist_dir and importance_threshold to settings
- Verified: cross-project isolation, persistence across restart,
  trivial message filtering
```

---

## What's coming on Day 7

Day 7 is the most visually satisfying day of Week 1: the PyWebView shell gets real.
You'll configure the transparent, always-on-top frameless window and wire up the global
hotkeys (Alt+Space, Ctrl+Alt+J) using pynput. By end of Day 7 you'll have a floating
window on your desktop that responds to keyboard shortcuts globally.

Before Day 7, briefly re-read:
- The PyWebView transparency gotcha in `SKILL.md` (§Project-specific gotchas)
- Day 7's tasks in `Day_by_Day_Plan_v2.md`
- The pynput threading note: it must run on a background thread or you block the main loop

---

## Time budget breakdown

| Task | Estimate |
|---|---|
| Pre-flight (commit Day 5, ChromaDB install, embedding sanity check) | 30 min |
| Task 1 — `vector_store.py` | 90 min |
| Task 2 — `importance.py` | 45 min |
| Task 3 — `save_memory()` in sqlite_store | 20 min |
| Task 4 — Wire into `/chat` | 60 min |
| Task 5 — Cross-project isolation test | 20 min |
| Buffer (debugging ChromaDB install, rate limits, etc.) | 35 min |
| **Total** | **~6 hours** |
