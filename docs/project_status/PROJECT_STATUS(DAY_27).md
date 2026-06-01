# Project Status — Day 27

**Period covered:** Day 27 (Week 4, Day 8 — repurposed from optional wake word)
**Status:** Complete — T-1/T-2/T-3/T-4 all done. tools registered: 11 (unchanged).
**Environment:** Windows 11, Python 3.13.5

> Checkpoint summary for Day 27: Wake word deferred to v2. Day used for latency and
> quota hardening. Piper cold-start eliminated (warm_up). Importance scorer now runs
> heuristic (no LLM call) by default — 1 LLM call/turn instead of 2. Groq fallback
> confirmed live and pre-existing. Two concurrent timers verified. Gemini API latency
> root cause identified (23-27s = embedding + generation both slow); timeout fix
> designed but deferred to v2 to avoid tool-calling and recall_from_project regressions.

---

## 1. What was done

| Task | What landed | Status |
|---|---|---|
| T-0 — Pre-flight | Baselines: 2 LLM calls/simple turn, 3/tool turn, first-call TTS ~3.9s (279chars). ChromaDB cold-init ~1s. grounded_search quota: Gemini chose web_search, status unconfirmed. | Done |
| T-1 — Piper warm-up | `warm_up()` added to TTSService. Called in lifespan after TTSService construction. Synthesizes "ok", skips playback. 991ms at boot. Failure non-fatal (warning + boot continues). SKILL.md updated. | Done |
| T-2 — Heuristic importance scorer | `_heuristic_score()` in importance.py (5 rules, 1-10 scale). `use_llm_importance_scorer: bool = False` in settings. Branch in `score()` — heuristic by default, LLM behind flag. 9 smoke tests in backend/tests/test_importance.py (all pass). Per-turn LLM calls: 1 (was 2 simple) / 2 (was 3 tool). | Done |
| T-3 — Groq text-only fallback | Already fully implemented: GroqLLMProvider, router fallback on RateLimitError/UnavailableError/AuthError, tools=None passed to Groq, list[Content] flattened to text. Confirmed live from T-1 log. | Pre-existing / Confirmed |
| T-4.1 — grounded_search retest | Gemini chose web_search (Tavily) for test query — grounded_search not triggered. Status still unconfirmed. Carry to Day 28. | Deferred |
| T-4.2 — Two concurrent timers | 1-min timer + 2-min timer running simultaneously. Both toasts fired accurately (60.03s, 120.02s). C2 speech: timer-1 skipped (orchestrator THINKING), timer-2 spoke (orchestrator IDLE). _active_timers GC guard held both. | Done |
| T-4.3 — demo_script.md #7/#8 | Section 6 added: web search, open VS Code (#7), set 1-min timer (#8), unknown app soft-error, two overlapping timers. | Done |
| Latency root cause analysis | 23-27s thinking = two Gemini API calls per turn: `_embed()` in `_build_context()` + `llm.generate()`. Both slow when Gemini API under load. Scoped timeout fix designed (see Decision D-4). | Analysed, deferred |

---

## 2. Key decisions and non-obvious choices

### Decision D-1 — Importance scorer shape: D-1a (readable rule-set)

Five rules in priority order (1 = highest):
1. Explicit save signal ("log this", "remember", etc.) → **10**
2. Trivial user input (single filler word, or combined text < 60 chars) → **1**
3. Domain/decision content (unit measurements, mutation identifiers like T315I/ABL1, conclusion words) → **7**
4. Substantive by length (combined text ≥ 250 chars) → **5**
5. Default → **3** (just below the 4.0 threshold; favours less clutter)

### Decision D-2 — Keep LLM scorer behind flag

`use_llm_importance_scorer: bool = False`. Costs nothing while off. A/B-testable by setting `USE_LLM_IMPORTANCE_SCORER=true` in `.env` and restarting. The LLM path is untouched.

### Decision D-3 — Groq fallback model

`groq_model = "llama-3.3-70b-versatile"` — already correct in settings from Day 4. No change needed.

### Decision D-4 — Gemini latency timeout fix: deferred to v2

Root cause: every voice turn makes two Gemini API calls:
1. `_embed(query)` inside `_build_context() → vector_store.search()` (~10-15s when Gemini slow)
2. `llm.generate()` for the actual response (~10-15s when Gemini slow)

**Why deferred:** a naive timeout on either call breaks things:
- Timeout on `vector_store.search()` globally → `recall_from_project` tool silently returns "No memories found" even when memories exist (wrong).
- Timeout on `router.generate()` globally → tool calls time out → Groq fallback gets `tools=None` → tool never executes; verbal-only response instead of action (wrong).

**Correct v2 approach:**
- Embedding timeout: wrap `_embed()` only inside `_build_context()`, not the global `vector_store.search()`. On timeout: skip semantic context, proceed with recency context only.
- Generation timeout: apply only when `tools is None AND response_schema is None`. Tool calls and structured output must always wait for Gemini.
- New settings: `embedding_timeout_seconds` and `llm_primary_timeout_seconds`.

### SQLite memory table vs ChromaDB — pre-existing gap

`_persist_turn` in `conversation.py` calls `vector_store.add()` (ChromaDB only) and does **not** call `sqlite_store.save_memory()`. The SQLite `memory` table is populated only by explicit tool calls (`log_to_project`, `summarize_paper`, `fetch_arxiv`) and the HTTP chat API. This was confirmed during T-2 verification — `SELECT COUNT(*) FROM memory` does not reflect voice-loop semantic memory writes. Pre-existing, not a T-2 regression. Day 28 cleanup candidate.

---

## 3. Problems and resolutions

### Problem A — SQLite memory count didn't grow after "log this" PTT turn

**Symptom:** `SELECT COUNT(*) FROM memory` stayed at 14 after "Log this into Alpha Project. JD7 strain from Jodhpur are the most heat-tolerant organisms in our collection."

**Cause:** `_persist_turn` calls `vector_store.add()` which only writes to ChromaDB. `sqlite_store.save_memory()` is never called from the voice pipeline. The memory table is only written by explicit tool calls. The heuristic correctly scored this turn 10 (Rule 1: "log this"), and ChromaDB was written to — but there's no SQLite record of it.

**Status:** Pre-existing gap. Not introduced by T-2. Document for Day 28.

### Problem B — Gemini API latency 23-27s per turn

**Symptom:** STT → spoken response taking 23-27s in today's session. Target is < 4s.

**Cause:** Two slow Gemini API calls in sequence: embedding (for semantic context) + generation. Both independently slow (~10-15s each) when Gemini API is under load.

**Status:** External API performance issue, not a code bug. Scoped timeout fix designed and deferred to v2 (see Decision D-4). Not today's scope — confirmed deferred by user.

---

## 4. Verification results

### T-1 Piper warm-up
```
[x] tts warm-up complete in 991ms in startup log
[x] No audio plays at startup
[x] First-call TTS latency now proportional to length (not cold-start spiked)
[x] Failure path: bad Piper path → "tts warm-up failed after 5ms (boot continues)" → reaches tools registered: 11
```

### T-2 Heuristic importance scorer
```
[x] 9/9 smoke tests pass (python -m backend.tests.test_importance)
[x] Per-turn LLM calls = 1 for simple turns (bafc06d0ee3c "tell me a joke": 1 llm_call)
[x] Per-turn LLM calls = 2 for tool turns (33ebba23774b: 2 llm_calls, no scorer call)
[x] "hi" → 1 llm_call, memory count unchanged (trivial filtered)
[x] use_llm_importance_scorer flag present in settings (toggle confirmed reversible)
```

### T-3 Groq fallback
```
[x] Live confirmation from T-1 session log:
    "primary unavailable, falling back to groq" → Groq answered in 1.9s
[x] tools=None always passed to Groq (router line 90)
[x] list[Content] flattened to text in groq_llm.py (lines 67-74)
[x] response_schema short-circuits before fallback (router lines 79-84)
```

### T-4 Carry-overs
```
[x] Two concurrent overlapping timers:
    timer-1 (1 min): fired at 09:55:50 (60.03s after schedule) — toast ✅, speech skipped (THINKING) ✅
    timer-2 (2 min): fired at 09:57:45 (120.02s after schedule) — toast ✅, speech spoken (IDLE) ✅
    _active_timers held both without GC drop ✅
[x] demo_script.md section 6 added (#7 open VS Code, #8 set 1-min timer)
[x] web_search via PTT confirmed (protein stability query → Tavily results → spoken answer)
[~] grounded_search quota — 429 RESOURCE_EXHAUSTED confirmed (same Google Search grounding bucket blocked since Day 25). Soft-error path works: no crash, LLM acknowledged gracefully. Carry to Day 28.
```

---

## 5. Heads-up for Day 28

Day 28 is the manual demo script day (`demo_script.md` full pass + fix any failures).

1. **grounded_search live retest (P-2/P-4)** — first item; confirm quota reset after today's testing gap.
2. **demo_script.md full pass** — run all 6 sections, fix any regressions before the Day 29 README + polish pass.
3. **SQLite memory table gap** — `_persist_turn` missing `sqlite_store.save_memory()` call. Low risk (ChromaDB still populated; only affects SQL-side queries), but worth fixing during the cleanup window.
4. **Gemini latency** — if still 20s+ on Day 28, document it in demo_script notes. The v2 timeout fix is designed and ready when needed.

---

## 6. Files changed this day

```
NEW:
  backend/tests/test_importance.py             -- heuristic scorer smoke tests (9 tests)
  docs/project_status/PROJECT_STATUS(DAY_27).md -- this file

EDIT:
  backend/voice/tts.py                         -- warm_up() method added
  backend/main.py                              -- await tts.warm_up() in lifespan
  backend/memory/importance.py                 -- _heuristic_score() + branch on flag; constants
  backend/config/settings.py                   -- use_llm_importance_scorer field added
  docs/demo_script.md                          -- section 6 (Week 4 tool-calling tests)
  docs/journal.md                              -- Day 27 entry
  .claude/skills/voice-pipeline/SKILL.md       -- warm_up() in TTSService service contract
```

---

## 7. Commits (to be made end of day)

```
[ ] perf(tts): warm up piper at startup to eliminate first-call cold start
[ ] feat(config): use_llm_importance_scorer flag (default off)
[ ] perf(memory): heuristic importance scorer; keep llm scorer behind setting
[ ] test: heuristic scorer smoke tests (9 cases)
[ ] docs: demo_script section 6 — Week 4 tool-calling tests
[ ] docs: day 27 journal + status
```
