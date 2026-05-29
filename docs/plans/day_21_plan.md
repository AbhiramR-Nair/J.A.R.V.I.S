# Day 21 Plan — Project Memory Tools

**Week 4, Day 2** · Builds directly on the Day 20 tool-calling architecture.
**Time budget:** ~5 hours
**Git commit (logical, see §"Suggested commits"):** `feat: project memory tools`

> **Goal in one sentence:** add four LLM-callable tools — `list_projects`,
> `set_active_project`, `log_to_project`, `recall_from_project` — so the user can
> switch projects, log notes, and recall facts entirely by voice, with cross-project
> isolation preserved.

---

## Agenda

Day 20 gave you a working tool-call loop and one trivial tool (`get_current_time`).
Today you reuse that exact 4-step pattern four more times — but these tools are the
first that touch **persistent state** (SQLite) and **semantic memory** (ChromaDB).
That's the whole difference in difficulty, and it's why the build order matters:
you'll go read-only → write → read-back, so each tool can be verified using the one
before it.

The day is mostly *plumbing existing modules into the registry*. You are **not**
writing new memory infrastructure — `sqlite_store`, `vector_store`, and `projects`
already exist from Days 5–6. Today's job is to expose four thin, well-described
handlers on top of them and let Gemini call them.

By tonight you should be able to say "switch to kinase project", "log this: …",
"what did we say about …?", and "what projects do I have?" and have each work
end-to-end via voice, with the active project shown in the UI.

---

## Before you start (morning, ~30 min) — do not skip

### 1. Clean tree + pull
- Confirm `git status` is clean and the Day 20 commits are in (`git log --oneline -6`
  should show the four Day 20 feature commits + the docs commit).
- `git pull` if you work across machines.

### 2. Re-read the two skills you'll lean on today
- `.claude/skills/tool-calling-pattern/SKILL.md` — the **4-step pattern**, the
  **JSON Schema rules**, and the **hard vs soft error** split. You'll apply all three
  four times today.
- `.claude/skills/voice-pipeline/SKILL.md` — the **§"Memory integration"** section
  (`_build_context`, `_persist_turn`) shows how the orchestrator already talks to
  `sqlite_store` and `vector_store`. Your tools should call the **same** functions the
  same way; don't invent a parallel path.
- `.claude/skills/project-architecture/SKILL.md` — the **"Project-scoped everything"**
  pattern (the `RIGHT`/`WRONG` `log_to_project` example is literally today's tool).

### 3. **Verify the real module APIs** (most important pre-flight)
The rest of this plan uses *placeholder* function names. Before writing anything, open
these files and write down the **actual** signatures + return shapes. (CLAUDE.md rule 4:
verify the installed API before suggesting code.)

| File | What to confirm |
|---|---|
| `backend/memory/sqlite_store.py` | Exact names of `get_active_project`, `set_active_project`, `list_projects`. **Does `set_active_project` create-if-missing, or only switch an existing row?** Is there a `create_project` / get-or-create? What does the "save a memory row" function look like (`save_memory`? `save_message`?) and what does it take? |
| `backend/memory/vector_store.py` | Signature of `add(text, project_id, metadata)` and `search(query, project_id, k=...)`. **What does `search` return** — a list of strings, a list of dicts, tuples, or Chroma result objects? This drives how `recall_from_project` maps to `list[str]`. |
| `backend/memory/projects.py` | What "active project management" lives here vs in `sqlite_store`. Pick one source of truth for "who is active" and route all four tools through it. |
| `backend/memory/importance.py` | Confirm only — you will **not** use it today (logged notes are forced to importance 10). |

If a function you expect doesn't exist (e.g. no get-or-create for projects), that's a
small sub-task to add to the relevant memory module — flag it and decide before you
start the tool handlers, not halfway through.

---

## Decisions to make first (the "suggest, don't just write" moment)

### Decision 1 — How does a tool handler learn the *active* project? (decide before coding)

`registry.execute(name, args)` calls your handler with only the args Gemini supplied.
It does **not** pass the active `project_id`. Two ways to get it:

| | Option A — handler queries `sqlite_store` itself | Option B — inject active `project_id` into the registry/`execute()` |
|---|---|---|
| How | `log_to_project`/`recall_from_project` call `sqlite_store.get_active_project()` at the top of the handler | Thread the orchestrator's turn-start `project_id` through `registry.execute(..., project_id=...)` into every handler |
| Pros | No registry contract change; matches what `conversation.py` already does; trivial to reason about | One source, no per-tool DB hit |
| Cons | Each tool repeats one lookup line | Changes the registry signature (hard to reverse), couples the registry to a project concept it shouldn't know about, breaks the "tools are self-contained" property |

**Recommendation: Option A** (this is the Day 20 status doc's call too). It keeps the
registry generic and each tool self-contained. The "extra DB hit" is a single indexed
read on a local SQLite file — irrelevant. Only `log_to_project` and
`recall_from_project` need it; `list_projects` and `set_active_project` are about
projects themselves and resolve active state directly.

### Decision 2 — `set_active_project`: one get-or-create helper, or switch-only + separate create?

The plan says the tool *"switches; creates if doesn't exist."* If `sqlite_store`'s
existing `set_active_project` only flips `is_active` on an existing row, you need a
**get-or-create** step first. Cleanest for v1: a single `sqlite_store` helper that does
get-or-create-then-activate atomically, so the `is_active` invariant (exactly one
active) is never briefly violated. Decide in pre-flight based on what you find in step 3.

### Decision 3 — `recall_from_project` return shape

Tool results must be JSON-serialisable (tool-calling SKILL gotcha). If `vector_store.search`
returns Chroma objects or tuples, **map them to `list[str]`** inside the handler before
returning. An empty result is *not* an error — return `[]` and let the LLM say "I didn't
find anything about that." (Or return a short string; pick one and be consistent.)

---

## Tasks

> Build order is deliberate: each tool is testable using the previous one.
> For every tool, follow the **4-step pattern** from the tool-calling skill:
> (1) create the file, (2) write a directive description, (3) add the lifespan import,
> (4) voice smoke test. Hand-write the JSON schema — **never** use Pydantic's
> `.model_json_schema()` (it emits `$defs`/`$ref` and the registry will reject it).

---

### Task 1 — `list_projects` (read-only, simplest) · ~30 min

**Why first:** zero side effects, a clean win, and it proves a tool that returns a
**list** flows through the registry → Gemini → spoken reply correctly (Day 20 only
proved a no-arg string-returning tool).

**What to do**
- Create `backend/tools/list_projects.py`.
- Handler: `async def list_projects() -> list[str]` — return project names from
  `sqlite_store.list_projects()` (map to plain strings). Consider marking the active
  one, e.g. `"kinase (active)"`, so the spoken answer is useful.

**Schema (no-arg)**
```python
parameters={
    "type": "object",
    "properties": {},
    "required": [],
}
```

**Description draft (Step 2)**
> "List all of the user's projects. Use this when the user asks what projects exist,
> e.g. 'what projects do I have?' or 'list my projects'. Call this rather than guessing."

**Smoke test (Step 4):** restart backend → startup log shows `tools registered: 2` →
ask "what projects do I have?" → logs show `tool_call iter=0: list_projects({})` →
spoken list including the default "general" project.

---

### Task 2 — `set_active_project` · ~45 min

**What to do**
- Create `backend/tools/set_active_project.py`.
- Handler: `async def set_active_project(name: str) -> str`. Use the get-or-create
  helper (Decision 2). Return a confirmation string, e.g. `f"Switched to {name}."` or
  `f"Created and switched to {name}."` — the LLM speaks this.
- **Project-scoped invariant:** ensure exactly one row stays `is_active` after the
  switch (the helper should handle this atomically).

**Schema**
```python
parameters={
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The project name to switch to, e.g. 'kinase' or 'fitness'. Created automatically if it doesn't already exist.",
        },
    },
    "required": ["name"],
}
```

**Description draft**
> "Switch the active project. Use this when the user says 'switch to X project',
> 'work on X', or 'change project to X'. Creates the project if it doesn't exist.
> Always call this — do not just acknowledge the switch in text."

**Error handling:** an unknown name is *not* an error here (you create it). Let an
unexpected DB failure propagate so the registry returns it as a **soft** error — do
**not** raise `ToolSchemaError` (that's a hard error → ERROR state, wrong for a
recoverable DB hiccup).

**Smoke test:** "switch to kinase project" → `tool_call iter=0: set_active_project({"name":"kinase"})`
→ confirmation spoken → re-run `list_projects` and confirm "kinase (active)".

---

### Task 3 — `log_to_project` · ~45 min

**What to do**
- Create `backend/tools/log_to_project.py`.
- Resolve the active project via `sqlite_store.get_active_project()` (Decision 1).
- Persist to **both** stores, mirroring `_persist_turn`:
  1. SQLite memory row (so it's queryable by SQL) with **importance hard-coded to 10**.
  2. `vector_store.add(content, project_id=<active>, metadata=...)` (so it's
     semantically searchable by `recall_from_project`).
- **Do not** route through `importance.py` — a logged note is always worth keeping;
  the scorer is only for auto-stored conversation turns.
- Return a confirmation string naming the project, e.g. `f"Logged to {project_name}."`.

**Schema**
```python
parameters={
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "The note or fact to save to the active project's memory, verbatim.",
        },
    },
    "required": ["content"],
}
```

**Description draft**
> "Save a note to the active project's long-term memory. Use this when the user says
> 'log this: …', 'note that …', 'remember that …', or 'save this'. Pass the fact to
> store. Always call this — never just say you've noted it."

**Smoke test:** "log this: T315I shows a 40-fold resistance shift" →
`tool_call iter=0: log_to_project({"content":"T315I shows a 40-fold resistance shift"})`
→ confirmation → verify the row landed:
`SELECT content, importance, project_id FROM memory ORDER BY id DESC LIMIT 3;`
(importance should be 10, project_id should be the active one).

---

### Task 4 — `recall_from_project` · ~45 min

**What to do**
- Create `backend/tools/recall_from_project.py`.
- Resolve active `project_id` (Decision 1).
- `results = await vector_store.search(query, project_id=<active>, k=settings.semantic_k)`
  — **must pass `project_id`** (this is the Day 6 cross-project isolation invariant;
  forgetting it is the classic bug).
- Map results to `list[str]` (Decision 3). Empty → return `[]`.

**Schema**
```python
parameters={
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What to search for in the active project's memory.",
        },
    },
    "required": ["query"],
}
```

**Description draft**
> "Search the active project's memory for previously logged facts. Use this when the
> user asks what was said, concluded, or logged earlier, e.g. 'what did we say about
> X?', 'what did we conclude about X?', or 'recall X'. Call this to search memory
> rather than guessing from the current conversation."

**Smoke test:** with "kinase" active and the T315I note logged (Task 3), ask
"what did we say about T315I?" → `tool_call iter=0: recall_from_project({"query":"T315I"})`
→ the logged fact comes back and is spoken.

---

### Task 5 — Register all four in the lifespan · ~10 min

In `backend/main.py`, in the tool-registration block (next to the Day 20
`get_current_time` import), add **one line per tool**:

```python
import backend.tools.list_projects        # noqa: F401
import backend.tools.set_active_project    # noqa: F401
import backend.tools.log_to_project        # noqa: F401
import backend.tools.recall_from_project   # noqa: F401
```

The import **is** the registration (the decorator fires on first import). Restart →
startup log should read `tools registered: 5`. A missing line surfaces as
`ToolNotFoundError` when the LLM tries to call that tool.

---

### Task 6 — System prompt directives in `50_tools.md` · ~15 min

Add **one directive sentence per tool** to `backend/prompts/system/50_tools.md`
(loaded once at startup — **restart the backend after editing**). Without these,
Gemini may ignore a tool for a query it should handle (tool-calling gotcha). Reuse the
four description drafts above, condensed to the trigger phrases:

- *switch / work on / change project* → `set_active_project`
- *what projects do I have / list projects* → `list_projects`
- *log this / note that / remember that* → `log_to_project`
- *what did we say/conclude about / recall* → `recall_from_project`

Make each directive distinct enough that the LLM picks the **right** tool for each
phrase — "log" vs "recall" is the pair most likely to be confused.

---

### Task 7 — UI: show the active project in the status bar · ~45 min

**What to do (smallest version that satisfies the criterion):**
- **Initial state:** on mount, `StatusBar.tsx` fetches the active project from the
  existing memory/project API (confirm the endpoint in pre-flight) and displays the name.
- **Live update:** when `set_active_project` runs, broadcast a WebSocket event so the
  UI updates without a refresh. Add a `project_changed {name}` event from the tool (or
  from the orchestrator after the tool returns) using the same `ws_manager.broadcast`
  path the voice events use. `StatusBar` listens and updates.

**Decision:** broadcasting from inside the tool handler couples a tool to the WebSocket
manager. Cleaner: have the tool just switch + return, and broadcast `project_changed`
from the orchestrator/dispatcher after a `set_active_project` result. Pick whichever is
less invasive given how your dispatcher is wired — and keep the diff minimal.

> This is the **first thing to drop** if you run short on time (see §"If you fall
> behind"). The four tools working via voice + logs is the substance; the status-bar
> chip is polish.

---

### Task 8 — Voice grammar tests · ~30 min

Run each phrase via PTT and confirm the right tool fires (check `data/logs/jarvis.log`
for `tool_call iter=0: <name>({...})` and `tool_result: …`):

1. "What projects do I have?" → `list_projects`
2. "Switch to kinase project" → `set_active_project({"name":"kinase"})`
3. "Log this: T315I shows a 40-fold resistance shift" → `log_to_project`
4. "What did we say about T315I?" → `recall_from_project` (returns the fact from #3)

If a phrase calls the wrong tool or no tool: strengthen the description / `50_tools.md`
directive (the description is the LLM's only signal — vague description, wrong call).

---

### Task 9 — Cross-project isolation verification (Day 6 invariant) · ~20 min

This is a **completion criterion**, not optional. Procedure:

1. "Switch to project alpha" → "Log this: alpha-only fact about widgets."
2. "Switch to project beta" → "Log this: beta-only fact about gadgets."
3. "Switch to project alpha" → "What did we say about gadgets?" → **must return
   nothing / not surface the beta fact.**
4. Confirm at the data layer too: a `vector_store.search("gadgets", project_id=<alpha>)`
   returns no beta rows.

If beta facts leak into an alpha query, you almost certainly dropped the `project_id`
argument somewhere in `recall_from_project` or `log_to_project`.

---

## Completion criteria

- [ ] `list_projects`, `set_active_project`, `log_to_project`, `recall_from_project`
      all created, registered, and `tools registered: 5` at startup.
- [ ] All four work **end-to-end via voice** (Task 8 phrases all fire the right tool).
- [ ] `log_to_project` writes importance-10 rows to **both** SQLite and ChromaDB,
      scoped to the active project.
- [ ] Active project **persists across a backend restart**.
- [ ] **Cross-project isolation holds** (Task 9 passes).
- [ ] Active project **visible in the UI** status bar, updating on switch.
- [ ] `50_tools.md` has a distinct directive for each of the four tools.
- [ ] `data/logs/jarvis.log` shows no unexpected ERROR lines from today.
- [ ] You can explain, out loud, how a handler resolves the active project and why
      `recall_from_project` must pass `project_id`.

---

## Suggested commits (logical, not one big "wip")

```
feat(tools): list_projects and set_active_project memory tools
feat(tools): log_to_project and recall_from_project memory tools
feat(ui): show active project in status bar
docs(prompts): tool directives for the four project-memory tools
```

(If `sqlite_store` needed a new get-or-create helper, that's its own small commit:
`feat(memory): get-or-create helper for projects`, landed before the tool commits.)

---

## Watch out for

- **Hand-write every schema.** Pydantic's `.model_json_schema()` emits `$defs`/`$ref`
  and `_validate_schema()` rejects it at registration time.
- **`recall_from_project` must return `list[str]`.** If `vector_store.search` returns
  Chroma objects or tuples, map them first — returning non-serialisable objects raises
  in `Part.from_function_response`.
- **`project_id` everywhere.** Both `log_to_project` (write) and `recall_from_project`
  (read) must pass the active `project_id`. Dropping it is the #1 isolation bug.
- **Don't route logged notes through `importance.py`.** `log_to_project` is always
  importance 10; the scorer is only for auto-stored turns.
- **Expected conditions are not hard errors.** "Project doesn't exist" in
  `set_active_project` is normal (you create it). Reserve `ToolSchemaError` (hard →
  ERROR state) for genuinely malformed calls; let DB/Chroma failures fall through to
  the registry's **soft** error path (`{"error": …, "type": …}`).
- **Restart after editing `50_tools.md`** — the system prompt loads once at startup.
- **Each tool needs its own lifespan import line** + `# noqa: F401`, or it's invisible
  to the LLM (`ToolNotFoundError` at call time).
- **Mid-turn switch edge case.** If the user says "switch to kinase and log X" in one
  utterance, the tools do the right thing (switch immediately, log resolves the new
  active project freshly). But the orchestrator's `_persist_turn` uses the *turn-start*
  `project_id` for the conversation messages, so this turn's own user/assistant lines
  may persist to the **previous** project. Acceptable for v1 — note it in the journal.
  Optional refinement: re-fetch the active `project_id` at persist time.
- **`set_active_project` must keep exactly one `is_active` row.** Do the create +
  activate in one helper so the invariant is never briefly broken.

---

## If you fall behind (descope, drop from the bottom)

Per the V1 drop-cut order, project memory + voice notes sit near the top (item 2) —
protect the tools. Drop in this order:

1. **Status-bar UI (Task 7)** — first to cut. Voice + logs prove the feature; the
   visible chip can land on a Week 4 buffer/polish pass.
2. **`recall_from_project`** — if ChromaDB wiring fights you, ship `list_projects` +
   `set_active_project` + `log_to_project` today and pull recall into tomorrow's margin.

The floor for the day is `set_active_project` + `list_projects` working by voice — that
alone makes the assistant project-aware, which is the product's defining behaviour.

---

## Evening wrap (10 min)

- Commit in the logical chunks above (not one blob).
- One line in `docs/journal.md` (note the mid-turn-switch edge if you hit it).
- Glance at **Day 22**: PDF parsing begins — the start of the three-day summarisation
  centerpiece. Confirm `pymupdf` is on your list to install and pick a test PDF tonight
  so you're not hunting for one tomorrow.
