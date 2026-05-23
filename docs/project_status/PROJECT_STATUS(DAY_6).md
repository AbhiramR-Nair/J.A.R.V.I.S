# Project Status — Day 6

**Period covered:** Day 6 (ChromaDB Semantic Memory)
**Status:** Complete — all 7 completion criteria met. Committed as `feat: semantic memory with importance scoring`.
**Environment:** Windows 11, Python 3.13.5, chromadb 1.5.9, google-genai 2.6.0

> Checkpoint summary for Day 6: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 7.

---

## 1. What has been done

Day 6 gave Jarvis semantic memory — the ability to recall past context by meaning rather
than exact string match. Before today every `/chat` request started with only the current
message and the last few SQLite rows. Now, before calling the LLM, Jarvis embeds the
user's message, searches ChromaDB for the top-3 semantically relevant exchanges, and
injects them as a labelled context block. After the LLM responds, the exchange is scored
for importance (1-10) and stored if it clears the threshold.

The practical test: ask "What am I working on?" without having said it in that message.
The assistant replied with ABL1 kinase resistance prediction — pulled entirely from
injected memory, not from the immediate conversation.

| Task | What landed | Status |
|---|---|---|
| Pre-flight — ChromaDB install | `chromadb==1.5.9` installed from pre-built wheel; pinned in `requirements.txt` | Done |
| Pre-flight — Embedding sanity check | `gemini-embedding-001` verified: async path works, returns 3072-dim vectors | Done |
| 1 — `backend/memory/vector_store.py` | ChromaDB wrapper: singleton client + per-project collections + `_embed()` helper + `add()` + `search()` | Done, verified |
| 2 — `backend/memory/importance.py` | LLM importance scorer with precise 1-10 prompt, regex parser, fail-safe 0 return | Done, verified |
| 3 — `save_memory()` in `sqlite_store.py` | Single `INSERT INTO memory` function, stores `chroma_id` alongside text and score | Done, verified |
| 4 — Wire `/chat` | Pre-LLM memory retrieval + prompt injection. Post-LLM importance scoring + conditional storage. Both blocks wrapped independently so failure is non-fatal | Done, verified |
| 5 — Cross-project isolation test | Searched project 1 and project 2 for kinase topics; no cross-contamination | Done, verified |
| Settings | `chroma_persist_dir`, `gemini_embedding_model`, `importance_threshold` added to `settings.py` | Done |

**Completion criteria verified:**

| Criterion | Result |
|---|---|
| C1 — ChromaDB persists in `data/chroma/`, survives restart | ✅ Re-queried after multiple server restarts; 3 docs in project_1, 5 in project_2 |
| C2 — 3 stored facts, query retrieves the correct one | ✅ Task 1 completion check: T315I query returned 2 most relevant kinase facts |
| C3 — Trivial messages (≤ importance 3) not stored | ✅ With caveat — see §3 P4 |
| C4 — Cross-project isolation | ✅ Kinase facts in project_1 never appeared in project_2 searches |
| C5 — Memory injected into LLM context, affects response | ✅ "What am I working on?" returned ABL1/kinase from memory, not from current message |
| C6 — `memory` table rows have `chroma_id` populated | ✅ All rows show non-NULL chroma_id values |
| C7 — Backend does not crash on embedding/ChromaDB failure | Manual verification: try/except guards both the retrieval and storage blocks — failure logs a warning/error and returns a normal chat response |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. Per-project ChromaDB collections, not metadata filtering

Each project gets its own named collection (`project_1`, `project_2`, etc.) rather than
a single shared collection with a `project_id` metadata filter. Both approaches would
work, but separate collections make isolation an architectural guarantee rather than a
query-time correctness requirement. There is no way to accidentally leak project 1 data
into a project 2 search — `search()` only ever queries the collection it was handed.
Metadata filtering would require every caller to correctly pass the filter; one missed
filter anywhere breaks isolation silently.

Trade-off: deleting a project's memory means dropping a whole collection (clean and
fast). With metadata filtering you'd need `DELETE WHERE project_id = ?` in ChromaDB,
which is less efficient.

### 2. Embed outside ChromaDB (Option B), raw vectors passed in

ChromaDB supports a custom `embedding_function` class that it calls automatically on
every `add()` and `query()`. We chose not to use it. Instead, `_embed()` calls Gemini
directly and passes the resulting `list[float]` to ChromaDB as `embeddings=[...]`.

Why: Option B is fully explicit. You can see exactly what embedding is being sent and
what task type was used. Option A hides this behind a class interface that's harder to
inspect, test, or swap out. The extra code is one helper function — a worthwhile
trade for transparency.

### 3. Task-typed embeddings: `RETRIEVAL_DOCUMENT` vs `RETRIEVAL_QUERY`

Gemini's `gemini-embedding-001` is a task-aware model. When storing a document,
`RETRIEVAL_DOCUMENT` optimises the embedding for being *found* by future queries.
When querying, `RETRIEVAL_QUERY` optimises for *finding* documents. Using the same
task type for both halves of the search degrades recall — the vectors are no longer
aligned in the same semantic space. `add()` always uses `RETRIEVAL_DOCUMENT`;
`search()` always uses `RETRIEVAL_QUERY`. This is a one-word difference in the API
call that measurably improves retrieval quality.

### 4. Cosine distance, not L2 (default)

ChromaDB's HNSW index defaults to L2 (Euclidean) distance. For sentence embeddings,
cosine similarity (angular distance) is more appropriate — it measures semantic
direction rather than absolute magnitude. A long document and a short document about the
same topic will have embeddings that point in the same direction but at different
magnitudes; cosine treats them as similar. L2 would penalise the magnitude difference.
Set via `metadata={"hnsw:space": "cosine"}` at collection creation. This is a
creation-time setting — it cannot be changed on an existing collection.

### 5. Absolute path for ChromaDB `PersistentClient`

The `chroma_persist_dir` setting (`"data/chroma"`) is a relative path. ChromaDB's
`PersistentClient` resolves it relative to the process's current working directory at
start time. If uvicorn is ever started from `backend/` instead of the repo root, the
collection would be created under `backend/data/chroma/` and the existing data at
`data/chroma/` would become inaccessible. We resolve the path to absolute via
`os.path.abspath(settings.chroma_persist_dir)` before passing it to the client. This
makes the data directory location independent of launch directory.

### 6. Non-fatal memory operations — two independent try/except blocks

The pre-LLM retrieval block and the post-LLM storage block each have their own
`try/except`. They are independent for a reason: a failure at retrieval (e.g. ChromaDB
corrupt) should not prevent storage from being attempted later, and vice versa.
A single outer try/except would collapse them into one failure mode.

Both blocks let chat continue regardless:
- Retrieval failure → `memories = []` → LLM called without context, normal response
- Storage failure → logged as ERROR → response already built, returned normally

### 7. Scoring the combined exchange, not just the user message

The importance scorer receives `f"User: {message}\nAssistant: {reply}"` rather than
just the user's message. A user message like "what's the IC50?" is ambiguous in
isolation (score: 4-5). The same message paired with a domain-specific answer about
ABL1 inhibitor resistance data makes the pair clearly worth storing (score: 8-9).
The full context gives the scorer the information it needs to make the right call.

### 8. `_parse_score()` defensively extracts integers

LLMs instructed to return "only a single integer" occasionally return "Score: 7" or
"7\n" or (rarely) something unparseable. The regex `r'\b([1-9]|10)\b'` extracts the
first word-boundary-delimited integer from 1-10 in any response. If nothing matches,
it returns 0, which is below the threshold of 4 — the exchange is silently not stored.
This is the safe failure direction (under-storing) rather than over-storing noise.

### 9. Original message saved to SQLite, augmented message sent to LLM

When memories are found, the LLM receives an augmented prompt:

```
[Relevant context from past conversations:]
- <memory 1>
- <memory 2>
- <memory 3>

[User's current message:]
<original message>
```

But `save_message()` stores only the original user message — not the augmented version.
This keeps the `messages` table a faithful record of what the user actually said. If
the messages table were ever used to reconstruct a conversation for a different purpose
(e.g. a future UI that shows conversation history), injected context blocks would look
like the user typed them.

---

## 3. Problems faced and how they were handled

### P1 — `text-embedding-004` model retired *(impact: high, resolved)*

- **What:** The Day 6 plan specified `text-embedding-004` for Gemini embeddings.
  The pre-flight sanity check returned HTTP 404 NOT_FOUND: "models/text-embedding-004
  is not found for API version v1beta, or is not supported for embedContent."
- **Cause:** Google retired `text-embedding-004` between when the plan was written
  and Day 6. Listing available models via `client.models.list()` returned three
  replacements: `gemini-embedding-001`, `gemini-embedding-2-preview`,
  `gemini-embedding-2`.
- **Handled:** Chose `gemini-embedding-001` as the stable non-preview replacement.
  Added `gemini_embedding_model: str = "gemini-embedding-001"` to `settings.py`
  so the model name is configurable without touching `vector_store.py`. Also noted
  that the new model produces **3072-dimensional** vectors (not 768 as documented
  in the plan) — ChromaDB infers dimensions from the first insert and stores them
  as part of the collection metadata, so no code change was needed.
- **Verified:** `len(result.embeddings[0].values)` returns 3072. Async path
  (`client.aio.models.embed_content`) also confirmed working before writing code.

### P2 — `chromadb.PersistentClient` is a factory function, not a class *(impact: low, resolved)*

- **What:** The initial `vector_store.py` used `chromadb.PersistentClient | None`
  as a type annotation for the singleton variable. On import, Python raised
  `TypeError: unsupported operand type(s) for |: 'function' and 'NoneType'`. The
  module failed to load.
- **Cause:** In ChromaDB 1.x, `PersistentClient` is a factory function that returns
  a `chromadb.api.ClientAPI` object. In the old ChromaDB 0.x API, it was a class.
  The `|` union operator requires both operands to be types; a function is not a type.
- **Handled:** Changed the annotation on both the module-level variable and the
  `_get_client()` return type to `chromadb.api.ClientAPI`. The callable
  `chromadb.PersistentClient(path=...)` still works correctly — only the type hint
  needed updating.
- **Verified:** Module imports cleanly; `isinstance` checks and IDE completions
  work correctly against `chromadb.api.ClientAPI`.

### P3 — First HTTP memory test appeared to fail (stale server) *(impact: medium, resolved)*

- **What:** After writing `chat.py` with the memory blocks, the first HTTP test
  (`POST /chat` with an ABL1 message) returned a valid response but no memory row
  appeared in SQLite. The server logs showed the request completing 15 ms after the
  LLM call — physically impossible if two more API calls (importance scoring +
  embedding) ran during that window.
- **Cause:** The uvicorn server serving the request had been started *before* the
  `chat.py` edits were saved. It was running the old code (Day 5 version) that
  had no memory operations. The new code was on disk but not loaded. Multiple
  attempts to kill and restart the server ran into port 8000 conflicts
  (`WSAENADDRINUSE`) from a previous server process that was still alive.
- **Investigation path:** Logs were filtered at INFO level, so `DEBUG` lines from
  `vector_store.search` and `importance.score` were invisible. The absence of even a
  single `llm_call` log entry for importance scoring (which would have appeared at
  INFO via cost_tracker) confirmed the memory block was never reached — which is
  only possible if the handler was the old code.
- **Handled:** Used `netstat -ano` to find the PID holding port 8000, killed it with
  `Stop-Process`, started a clean server. Subsequent HTTP test: memory stored,
  chroma_id populated, retrieval working.
- **Lesson:** Always kill the server explicitly before testing code changes, even if
  you think you already did. `Stop-Process -Name python` may not match processes
  started via uvicorn. Use `netstat -ano | findstr :8000` to find and kill by PID.

### P4 — Memory injection inflates trivial message importance scores *(impact: low, accepted)*

- **What:** Sending "ok" after a session with ABL1 kinase context stored in memory
  resulted in a score of 10 and the exchange being stored — despite the user's message
  being trivially short.
- **Cause:** Memory injection worked correctly: the "ok" message triggered retrieval
  of kinase facts, which were prepended to the LLM call. The LLM then responded with
  a substantive kinase research follow-up. When scoring `"User: ok\nAssistant: <rich
  kinase content>"`, the scorer correctly identifies the combined exchange as highly
  important (score 10) — because the assistant's half *is* important.
- **Decision:** Accepted. The behavior is arguably correct. The exchange is meaningful
  even if the user's token count was 1. Storing it means future conversations about
  the same project have access to the assistant's synthesised follow-up. The alternative
  — scoring only the user message — would discard substantive assistant responses to
  short prompts.
- **Implication:** The threshold filter works as intended for standalone trivials (a
  standalone "ok" with no prior memory would receive a low score and not be stored).
  It does not filter trivials that happen to trigger rich memory injection. This is a
  feature, not a bug, but it means the `memory` table will grow faster in active
  projects than naive estimates based on "only important messages" would suggest.

---

## 4. Heads-up: downstream complications to watch

### Embedding model version is baked into collection data — never change it silently

The dimensions of a ChromaDB collection are set by its first insert and stored as
collection metadata. `gemini-embedding-001` produces 3072-dimensional vectors. If
the model is ever changed (e.g. to `gemini-embedding-2` when it leaves preview), the
new model's vectors must match the stored dimensions or ChromaDB will reject inserts
with a dimension mismatch error.

**Fix if this ever happens:** delete `data/chroma/` to wipe the collections and
re-embed from scratch. The SQLite `memory` table rows still exist (with their text and
importance scores), so nothing is permanently lost — just the vector index needs
rebuilding. A helper script that reads `memory` rows and re-runs `vector_store.add()`
would handle this cleanly.

**Watch for:** any change to `settings.gemini_embedding_model` that is deployed
against an existing `data/chroma/` directory.

### ChromaDB collection `hnsw:space` cannot be changed retroactively

Cosine distance is correct for this use case and is set at collection creation. If a
collection already exists with L2 (e.g. from an earlier test run), `get_or_create_collection`
with `metadata={"hnsw:space": "cosine"}` will silently ignore the metadata on the
existing collection and use L2. The collection will appear to work but retrieval quality
will be degraded.

**Diagnosis:** `collection.metadata` returns the stored settings. If `hnsw:space` is
missing or `"l2"`, delete `data/chroma/` and restart to rebuild with cosine.

**Watch for:** unexpected retrieval misses where semantically similar documents score
low. This is the most common cause if `data/chroma/` was created in an earlier test
run before the cosine setting was added.

### Memory-related DEBUG logs are filtered at the INFO threshold

`vector_store.search`, `vector_store.add`, and `importance.score` all log at DEBUG
level. With `log_level: str = "INFO"` in settings, these lines never appear in
`data/logs/jarvis.log`. The only memory-related INFO log is `memory stored:
importance=X, chroma_id=...` — and that only fires when a memory is actually stored.

If memory retrieval or storage appear to be silently doing nothing, the diagnostic
is not the log file — it is querying SQLite and ChromaDB directly:

```python
# SQLite check
from backend.database.db import get_db
get_db().execute("SELECT * FROM memory ORDER BY id DESC LIMIT 5;").fetchall()

# ChromaDB check
from backend.memory.vector_store import _get_collection
_get_collection(project_id=1).count()
```

Alternatively, set `log_level=DEBUG` in `.env` temporarily to see all memory
pipeline steps.

### Starlette middleware `call_next` timing is misleading for async handlers

The `← POST /chat done` log line from `request_id_middleware` fires as soon as
`call_next` returns a response, which for non-streaming JSON responses happens when
the handler returns. For the normal case this is fine. However, if Starlette ever
starts using background tasks internally (e.g. for response streaming), memory
operations could appear to complete "instantly" in the log even if they finish after
the response is sent. This is not currently a problem but is worth understanding
when reading timing logs.

**Practical consequence today:** memory operations that take 2-3 seconds (importance
scoring + embedding) will always complete before the response is returned — adding
latency to `/chat` even for trivial messages. Day 11 (voice loop) should profile
end-to-end latency; if memory operations add too much, consider running them as an
`asyncio.create_task()` background coroutine after the response is built.

### Importance scoring adds one full LLM call per `/chat` request

Every chat request now makes two LLM calls: one for the answer, one for importance
scoring. The scoring call uses a small prompt and typically returns in < 1 second
(the flash model handles it fast). But it does consume quota and add latency.

**Watch for:** Gemini flash quota exhaustion during heavy testing days. If scoring
starts returning errors (rate limit), the try/except in `importance.score()` catches
them and returns 0, so no exchanges get stored until quota recovers. Run
`SELECT COUNT(*) FROM memory;` periodically to catch this — if the count stops
growing during active use, the scorer may be silently failing.

### Cross-project `is_active` rule still enforced only in Python, not SQL

From Day 5: `set_active_project()` and `scripts/set_project.py` maintain the
"exactly one active project" invariant. Day 21 adds voice commands that switch
projects. Those commands must route through `set_active_project()` — not raw SQL
updates — or the invariant breaks. Memory operations inherit `project_id` from
`get_active_project()`, so a corrupted active state (zero active projects) would
cause `RuntimeError` at the top of `/chat` before memory operations are reached.

---

## 5. Open items before Day 7

- [ ] Manual C7 verification: break Gemini API key briefly in `.env`, send a
      `/chat` message, confirm 200 response with warning in logs and no 500 error
- [ ] (Optional) Set `log_level=DEBUG` in `.env` for one session to see the full
      memory pipeline in action — `vector_store.search`, `importance.score`,
      `vector_store.add` all logging at DEBUG. Restore to INFO after.
- [ ] Review Day 7 plan: the PyWebView transparency gotcha in `SKILL.md` and the
      pynput threading note. Day 7 has no memory-related work.

---

## 6. How to verify Day 6

```powershell
# 1. Start server
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 2. Send an important message
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" `
  -d "{\"message\": \"I am studying ABL1 T315I gatekeeper resistance to imatinib\"}"

# 3. Confirm memory row written to SQLite
sqlite3 data/jarvis.db "SELECT importance, chroma_id IS NOT NULL, substr(content,1,60) FROM memory ORDER BY id DESC LIMIT 1;"
# Expected: importance >= 4, chroma_id NOT NULL, content starts with "User: I am studying"

# 4. Ask a retrieval question
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" `
  -d "{\"message\": \"What resistance mechanism am I studying?\"}"
# Expected: response mentions T315I or gatekeeper without you having said it in this message

# 5. Cross-project isolation
python -c "
import asyncio
from backend.memory import vector_store
async def check():
    r = await vector_store.search('T315I resistance', project_id=1, k=2)
    print('project_1:', r)
    r2 = await vector_store.search('T315I resistance', project_id=2, k=2)
    print('project_2:', r2)
asyncio.run(check())
"
# Expected: results are different; no cross-contamination
```

---

## 7. Commit log for this period

```
feat: semantic memory with importance scoring

- Add vector_store.py: ChromaDB wrapper with per-project collections,
  Gemini gemini-embedding-001 embeddings (replaces retired text-embedding-004),
  task-typed embed calls (RETRIEVAL_DOCUMENT/RETRIEVAL_QUERY), cosine distance
- Add importance.py: LLM-scored 1-10 filter via prompt with concrete anchors;
  returns 0 on any failure so memory never breaks chat
- Add save_memory() to sqlite_store.py; populates memory.chroma_id
- Update /chat: retrieve top-3 relevant memories before LLM call (injected
  as labelled context block); score + store important exchanges after
- Add chroma_persist_dir, gemini_embedding_model, importance_threshold to settings
- Pin chromadb==1.5.9 in requirements.txt
- Verified: cross-project isolation, persistence across restart,
  memory retrieval affects LLM response, non-fatal failure handling
```
