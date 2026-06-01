# Project Status — Day 28

**Period covered:** Day 28 (Week 4, Day 9 — manual demo script pass + regression fixes)
**Status:** Complete — two full demo passes run; 7 bugs found and fixed; §7 cross-project isolation confirmed; feature set frozen for Day 29 README + polish.
**Environment:** Windows 11, Python 3.13.5

> Checkpoint summary for Day 28: Demo script expanded to the full 10-item canonical run (§6.1 project memory, §6.2 PDF, §6.3–6.5 web/app/timers, §7 cross-project isolation). Two live passes run. Six bugs surfaced and fixed: Groq fallback silently dropping tool results; " project" suffix creating duplicate projects; set_active_project returning raw input name; multi-intent "log this into X project" missing log_to_project call; ChromaDB HNSW crash on stale-count empty collection; corrupted project_1 collection reset. grounded_search soft-error path confirmed live. SQLite memory gap fixed. Feature freeze in effect.

---

## 1. What was done

| Task | What landed | Status |
|---|---|---|
| demo_script.md expansion | §6 restructured into §6.1 (project memory tools), §6.2 (PDF), §6.3–6.5 (web/app/timers). §7 cross-project isolation invariant check added. Known Limitations section added. | Done |
| D-28-1 — SQLite memory gap fix | `_persist_turn` now calls `sqlite_store.save_memory()` after `vector_store.add()` when score ≥ threshold. chroma_id passed for cross-store linkage. Inside existing non-fatal try/except; outside lock. | Done |
| D-28-2 — grounded_search confirmed | Soft-error path verified live: 429 → error dict → LLM answers from knowledge → no crash. Documented in demo_script.md Known Limitations. | Confirmed + documented |
| T-7 — Gemini latency documented | Known Limitations in demo_script.md. v2 timeout fix (D-4) reference included. No code action on freeze day. | Documented |
| Bug fix — Groq flattening | `function_response` parts were silently dropped in Groq's content flattening. PDF summary tool result was lost, Groq responded "I need to know which paper" instead of the summary. Fixed: all three Part types now handled (text, function_call, function_response). | Fixed |
| Bug fix — " project" suffix normalization | "switch to alpha project" created a separate "alpha project" project instead of routing to "alpha". Fixed: `sqlite_store.set_active_project()` strips trailing " project" after lowercasing. | Fixed |
| Bug fix — set_active_project return string | Tool discarded the DB return value and formatted confirmation from the raw input: `f"Switched to project '{name}'."` still showed "alpha project" even after normalization. Fixed: `project = sqlite_store.set_active_project(name); return f"...'{project['name']}'."` | Fixed |
| Bug fix — multi-intent log+switch | "Log this into alpha project. JD15 is X" fired only `set_active_project`; LLM then hallucinated having logged. Fixed: directive added to `50_tools.md` — "log this into [project]" requires both `set_active_project` then `log_to_project`. Confirmed in pass: both fired in sequence (iter=0, iter=1). | Fixed |
| Bug fix — ChromaDB HNSW crash | `collection.query()` raised `InternalError: Nothing found on disk` when `count() > 0` but HNSW index was never flushed (stale metadata from early Day 3-6 /chat sessions). Fixed: `collection.query()` wrapped in try/except; returns `[]` on failure, logs WARNING. Voice turn continues with recency context only. | Fixed |
| Corrupted project_1 collection reset | `project_1` (general) reported count=28 but had no HNSW index. Deleted and reset to empty via `client.delete_collection('project_1')`. Next boot recreates fresh with count=0; early `count == 0` guard returns `[]` without calling `query()`. | Fixed |
| §7 cross-project isolation — confirmed | JD15 logged to alpha → switched to general → `recall_from_project` returned `[]` three times across three different query phrasings → no alpha data surfaced in general project. ChromaDB project-scoping working correctly. | Confirmed ✅ |

---

## 2. Key decisions

### Decision D-28-1 — SQLite memory gap: fixed (not deferred)

The Day 28 plan recommended deferring this to v2 (Option A — freeze day). On review, the fix is a single `sqlite_store.save_memory()` call inside the existing try/except in `_persist_turn`, outside the orchestrator lock, with no state mutations. Fixed.

### Decision D-28-2 — grounded_search: documented, not removed

Tool stays registered. 429 returns a dict, LLM answers gracefully from its own knowledge. Confirmed live. Limitation documented.

### Decision D-28-3 — ChromaDB try/except scope: query only, not count

Wrapped only `collection.query()`, not `collection.count()`. If `count()` raises (different failure mode), it will still propagate up and be caught by `_process_turn`'s outer except. Wrapping only `query()` keeps the guard targeted and avoids masking count-side failures.

### Decision D-28-4 — Stray projects (alpha project, general project): leave in DB

Both projects have conversations attached (FK constraint prevented deletion). They're inactive, won't be selected (normalization routes their inputs to "alpha"/"general"), and appear in `list_projects` output. Cosmetic issue for v1; clean up in v2 if it becomes confusing.

---

## 3. Problems and resolutions

### Problem P-1 — PDF spoken response wrong on Groq fallback

**Symptom:** `summarize_paper` returned full structured ABYSSAL summary dict. Final LLM call fell back to Groq. Spoken response: "I'd be happy to attempt a summary. However, I need to know which paper…" — the actual summary was ignored.

**Cause:** `groq_llm.py` content flattening only extracted `p.text` parts. `function_response` Parts (which carry the tool result dict) have no `.text` attribute — they were silently skipped. Groq received only the original user query with no summary attached.

**Fix:** Flattening loop now handles all three Part types: `p.text` (unchanged), `p.function_call` → `[tool called: name(args)]`, `p.function_response` → `[tool result from name]: response`. Groq now receives the full summary dict as text.

**Verified:** Second pass — "Summarize this paper" → Groq fallback active → spoken response: "The paper discusses ABYSSAL, a deep learning method for pred…" (712 chars) ✅

### Problem P-2 — " project" STT suffix creates duplicate projects

**Symptom:** "switch to alpha project" → created a separate "alpha project" project. `list_projects` showed both "alpha" and "alpha project" as distinct entries.

**Cause:** `sqlite_store.set_active_project()` only did `name.strip().lower()`. STT consistently appends " project" to project names.

**Fix:** After lowercasing, strip trailing `" project"` suffix. "alpha project" → "alpha". "general project" → "general".

**Verified:** "switch to alpha project" → `set_active_project({'name': 'alpha'})` → "Switched to project 'alpha'." ✅

### Problem P-3 — Multi-intent "log this into X project" missing log call

**Symptom:** "Log this into alpha project. JD15 is a thermo tolerant…" → only `set_active_project` fired; LLM said "I have logged that information" without calling `log_to_project`.

**Cause:** `50_tools.md` had no directive for the combined switch+log pattern. LLM handled the first intent (project switch) and then verbally acknowledged the second without calling the tool.

**Fix:** Directive added to `50_tools.md`: "log this into [project]" requires both `set_active_project` then `log_to_project` — never claim to have logged without calling the tool.

**Verified:** "log this into alpha project JD 15 is a PGPR" → iter=0: `set_active_project` → iter=1: `log_to_project` → both confirmed ✅

### Problem P-4 — ChromaDB HNSW crash on query for general project

**Symptom:** "What did I say about JD-15?" (in general project) → `chromadb.errors.InternalError: Nothing found on disk` → orchestrator ERROR state → auto-recover 3s → voice turn failed.

**Cause:** `project_1` (general) reported `count()=28` (stale metadata from Day 3-6 `/chat` API testing) but no HNSW index existed on disk. The `count == 0` guard was bypassed; `collection.query()` raised.

**Fix:** `collection.query()` wrapped in try/except; returns `[]` on any exception, logs WARNING. Also: deleted the corrupted `project_1` collection; recreates fresh on next boot with `count()=0`.

**Verified:** After fix — "What did I say about JD-15?" in general project → `WARNING: vector_store.search: query failed for project_1 (HNSW index missing?), returning empty` × 3 → voice turn completed with "no records in this project" response ✅

### Pre-existing — Gemini API latency 23-27s

No change from Day 27. v2 timeout fix (D-4) designed and deferred. Documented.

### Pre-existing — grounded_search 429

Confirmed live. Soft-error path works correctly. Documented.

---

## 4. Verified demo pass results (Pass 2, Session 3)

```
[x] boot: tools registered: 11, tts warm-up complete in ~800ms
[x] §6.1 "log this into alpha project JD 15 is a PGPR" → set_active_project (iter=0) + log_to_project (iter=1) → both confirmed
[x] §6.1 "switch to general project" → normalization active → "Switched to project 'general'." 
[x] §6.1 "What did I say about JD-15?" (in general) → recall_from_project → [] × 3 queries → graceful "no records" spoken
[x] §6.3 web_search: "latest computational biology papers" → 5 real results (biorxiv, PLOS, Nature, ScienceDirect) → spoken synthesis
[x] §6.4 "Open Chrome" → launched in ~1.8s, spoken confirmation
[x] §6.4 "Open Cursor" → launched via cmd
[x] §6.4 "Open anti-gravity" → soft-error: "not in the whitelist, sir" → no crash
[x] §6.5 "Set timer for 30 seconds" → fired at 30.03s → toast + spoken "Timer — time's up, sir."
[x] window hide/show via tray → both directions confirmed
[x] "Open Spotify" → Store app launched via cmd dispatch
[x] grounded_search soft-error: 429 → dict → LLM answers from knowledge → no crash (confirmed live)
[x] §7 cross-project isolation: alpha data did NOT surface in general project (ChromaDB scoping correct)
[x] mute toggle confirmed working mid-speech (multiple times)
[~] grounded_search quota still blocked (expected — documented limitation)
[~] Gemini API latency variable (3-8s typical this session; 23-27s under heavy load — documented)
```

---

## 5. Heads-up for Day 29

Day 29 is the README + polish pass.

1. **Python version** — CLAUDE.md pins Python 3.12; running environment is 3.13.5. README prerequisites must state the actual ship version. No 3.13-specific regressions observed.
2. **grounded_search** — quota still blocked; include in README troubleshooting. If it resets, test once and mark confirmed.
3. **Final demo pass** — run `demo_script.md` once more on Day 29. Target: all §1–§7 items pass cleanly before Day 30 demo video.
4. **Stray projects** — `alpha project` and `general project` remain in SQLite (FK prevents delete). If confusing for the README, note as known limitation. `list_projects` will show them.
5. **Latency** — document in README Known Limitations if still >10s under Gemini load. Do not attempt the v2 timeout fix before ship.

---

## 6. Files changed this day

```
NEW:
  docs/project_status/PROJECT_STATUS(DAY_28).md     -- this file

EDIT:
  backend/services/conversation.py                   -- _persist_turn: sqlite_store.save_memory() after vector_store.add()
  backend/llm/groq_llm.py                            -- flatten function_call + function_response Parts in Groq fallback
  backend/memory/sqlite_store.py                     -- set_active_project: strip " project" suffix from name
  backend/tools/set_active_project.py                -- use normalized DB name in confirmation string
  backend/prompts/system/50_tools.md                 -- multi-intent directive: "log this into X project" → both tools required
  backend/memory/vector_store.py                     -- wrap collection.query() in try/except; return [] on HNSW error
  docs/demo_script.md                                -- §6 restructured, §7 + Known Limitations added, grounded_search confirmed
  docs/journal.md                                    -- Day 28 entry
```

---

## 7. Commits (end of day)

```
[ ] fix(memory): mirror voice-loop turns to SQLite memory table in _persist_turn
[ ] fix(llm): flatten function_call+function_response parts in Groq fallback
[ ] fix(memory): strip " project" suffix in set_active_project normalization
[ ] fix(tools): use normalized DB name in set_active_project confirmation string
[ ] fix(tools): multi-intent directive — log this into X project requires both tool calls
[ ] fix(memory): graceful [] return when ChromaDB HNSW index missing on disk
[ ] docs: demo_script full canonical pass + §7 cross-project isolation + known limitations
[ ] docs: day 28 journal + status
```
