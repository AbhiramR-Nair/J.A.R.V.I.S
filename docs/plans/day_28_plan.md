# Day 28 Plan — Manual Demo Script Full Pass + Regression Fixes

**Period:** Week 4, Day 9 (Day 28 of 30)
**Predecessor:** `PROJECT_STATUS(DAY_27).md`
**Goal:** Run the full `demo_script.md` regression pass twice, document every result, fix what breaks, and close out the two open items Day 27 handed forward (grounded_search quota, SQLite memory gap). Freeze the feature set before the Day 29 README/polish pass.
**Time budget:** 4 hours — most of it is *fixing what breaks*, not building.
**New tools today:** none. No registry changes, no new JSON schemas. Day 28 is a test/freeze day.

> **A note on names in this plan.** Method and file references (`_persist_turn`, `save_memory`, `log_to_project`, `_build_context`, etc.) are taken from the Day 27 status and the skill files. Treat them as placeholders — verify against the actual source before editing, per CLAUDE.md rule #4.

---

## 0. Morning pre-flight (10 min)

- [ ] `git pull` latest from main
- [ ] **Confirm Day 27's 6 commits actually landed** — they were listed "to be made end of day" in `PROJECT_STATUS(DAY_27).md` §7. `git log --oneline -8`; if anything is still unstaged, commit it before starting so today's diffs stay clean.
- [ ] Backend boots clean → startup log shows `tools registered: 11`
- [ ] Startup log shows `tts warm-up complete in ~1s` (Day 27 T-1) and no audio plays at boot
- [ ] `python -m backend.tests.test_importance` → **9/9 pass**
- [ ] `npm run dev` up; PyWebView window shows the blob idle in a corner
- [ ] **Test material ready:** one real *text-based* PDF from your reading list at a known path, and one arxiv ID for a paper you know
- [ ] **Record baselines** so you can tell what grew during the run:
  - `SELECT COUNT(*) FROM memory;` (SQLite)
  - ChromaDB collection count for the active project
- [ ] `tail` `data/logs/jarvis.log` in a spare terminal so you can watch `tool_call` / `tool_result` lines live during the pass

---

## 1. Decisions to resolve *before* touching code

### D-28-1 — SQLite memory gap: fix today or defer to v2?

**Context (Day 27 Problem A):** `_persist_turn` writes ChromaDB only; the SQLite `memory` table is never written from the voice loop. `recall_from_project` reads ChromaDB, so the demo is unaffected — the gap only shows up in SQL-side `COUNT(*)`.

- **Option A — defer (recommended).** Day 28 is a freeze day. Touching `conversation.py` puts the Lock pattern at risk right before ship, for a gap that doesn't affect any demo path. ChromaDB recall works; SQL count is cosmetic for v1.
- **Option B — fix now.** One added call in the lock-released persist step, after the existing `vector_store.add()`: `await sqlite_store.save_memory(...)`. Genuinely small, but it's *inside the orchestrator*. If you do it: read `voice-pipeline/SKILL.md` §"The Lock pattern" and §"Memory integration" first, mirror the existing `vector_store.add` placement, verify the real `save_memory` signature, and **re-run a full demo pass after**.

**Recommendation:** defer to v2 *unless* the first demo pass finishes clean with >1h to spare.

### D-28-2 — grounded_search: if still quota-blocked, document or remove?

**Context:** grounded_search has returned 429 RESOURCE_EXHAUSTED since Day 25 (shared Google Search grounding bucket). Gemini picks `web_search` (Tavily) for test queries anyway, and the soft-error path is graceful (no crash, LLM acknowledges).

- **If quota reset** → verify once, mark confirmed, done.
- **If still blocked** → **Option A (recommended): document.** Keep it registered (it degrades cleanly), note the limitation in `demo_script.md` and the Day 29 README, move on. **Option B: remove** grounded_search registration for v1 — cleaner, but it's a code change on a test day and drops a capability that works when quota is available.

**Recommendation:** document, don't remove. v1 web search = Tavily; grounded_search is a graceful bonus.

> **Latency = already decided.** No fix today. The v2 timeout design (Day 27 Decision D-4) stands. Day 28's only latency task is to *document* the current state if turns are still slow (T-7).

---

## 2. Task breakdown

| ID | Task | Notes |
|---|---|---|
| **T-1** | grounded_search retest | First, per Day 27 heads-up. Ask a general-fact query that should prefer grounding (e.g. "what's the weather today?"). Watch the log: did Gemini call `grounded_search` or `web_search`? Record the 429 status. **Resolves D-28-2.** |
| **T-2** | Full `demo_script.md` pass #1 | Run every section in order. For each item record: pass/fail, eyeballed latency, and the `tool_call` line. **Don't fix mid-pass** — note failures and keep going so you see the whole picture first. |
| **T-3** | Triage + fix | Group failures by cause. For each, follow the CLAUDE.md debug loop (what the error means → 2-3 likely causes → diagnostic → *then* fix). Expect most fixes to be system-prompt / `50_tools.md` directive tweaks, not code. |
| **T-4** | *(conditional, per D-28-1)* SQLite memory persist fix | Only if decided yes and time allows. Follow the Lock-pattern caveat above. |
| **T-5** | Full `demo_script.md` pass #2 | Confirms fixes and catches any regression introduced in T-3/T-4. This is the "run it twice today" requirement. |
| **T-6** | Fold new edge cases into `demo_script.md` | Any phrasing that broke, any tool that didn't fire when it should have. |
| **T-7** | Document Gemini latency | If turns are still 20s+, add a note to `demo_script.md` referencing the D-4 v2 timeout fix. |
| **T-8** | Journal line + commits | One line in `docs/journal.md`; commits per §7. |

---

## 3. The demo pass — what to run

The canonical 10 from the original Day 28 plan, grouped as they appear in `demo_script.md`. **Verify the real section numbering against the file** — the items are fixed, the section layout is what you confirm.

1. **"What time is it?"** → `get_current_time` fires → spoken time. *Trivial — should NOT persist (heuristic Rule 2).*
2. **"Switch to kinase project"** → `set_active_project` → active project flips, visible in UI.
3. **"Log this: T315I shows 40-fold shift in TKI binding"** → expect a `log_to_project` `tool_call` in the log. Note whether SQLite `memory` grows (see watch-out below).
4. **"What did we just log?"** → `recall_from_project` → returns the T315I line (ChromaDB).
5. **"Latest ABL1 inhibitor papers"** → `web_search` (Tavily) → spoken summary + links in chat.
6. **Drag a PDF onto the window → "summarize this"** → structured summary; key claims spoken. *Target was ~15s; with current Gemini latency expect longer — that's an external issue, not a fail.*
7. **"Open VS Code"** → `open_app` launches it. Then an app **not** in `apps.yaml` → graceful soft-error ("add it to apps.yaml").
8. **"Set a timer for 1 minute"** → toast + spoken alert at ~60s. (Day 27 verified two concurrent; single timer is the demo item.)
9. **"Tell me a joke"** → basic chat, no tool. *Trivial — should NOT persist.*
10. **Ctrl+Alt+J (mute)** → try PTT (dropped, warn-log) → **Ctrl+Alt+J again (unmute)** → PTT works.

**Section 6 extras (added Day 27), re-run lightly:** unknown-app soft-error and two overlapping timers — both verified Day 27.

**Cross-project isolation spot-check (hard rule — do this every pass):** after item 3 logs to kinase, switch to "general" and ask "what did we conclude about T315I?" → the kinase log must **not** surface. This is the project-scoping correctness check.

---

## 4. Completion criteria

- [ ] **D-28-2 closed** — grounded_search confirmed working OR documented as a known limitation
- [ ] All 10 items + section-6 extras pass, OR each failure documented as a known limitation with a reason
- [ ] Cross-project isolation spot-check passes
- [ ] `demo_script.md` updated with any new edge cases and committed
- [ ] Full pass run **twice** today (and once more on Day 29)
- [ ] Gemini latency state noted in `demo_script.md` if still >20s/turn
- [ ] *(if D-28-1 = fix)* SQLite `memory` count grows after a voice "log this" turn; second full pass clean

---

## 5. Watch-out list

- **Don't touch the Lock pattern casually.** If you do D-28-1, read `voice-pipeline/SKILL.md` §"The Lock pattern" + §"Memory integration" first. The `save_memory` call belongs in the lock-*released* persist step, mirroring the existing `vector_store.add`. Re-run a full pass after any orchestrator edit.
- **"Log this:" doesn't always call the `log_to_project` tool.** Day 27 saw a "log this" turn persist via `_persist_turn` (ChromaDB, heuristic score 10) *instead* of the tool — so SQLite didn't grow. Recall still works (ChromaDB), but if you want the tool to fire reliably that's a `50_tools.md` directive / description fix per the tool-calling skill — **not** a code bug. Check the log for `tool_call: log_to_project` and decide.
- **Heuristic trivial-filter: combined text <60 chars → score 1 → skipped.** Items 1 and 9 are *meant* to be filtered. Only Rule 1 ("log this"/"remember") forces score 10 regardless of length — a short log line *without* a save signal will be filtered.
- **Gemini latency may make the demo feel slow (20-27s/turn).** Expected per Day 27 Problem B. Do **not** panic-fix on a test day; the v2 timeout fix (D-4) is designed and deferred.
- **Two LLM calls per tool turn.** Heavy re-runs can still brush Groq/Gemini per-minute limits. If turns start failing oddly after many runs, check for rate-limit lines *before* assuming a regression.
- **grounded_search vs web_search routing.** Gemini consistently picks `web_search`. "grounded_search not triggered" is expected routing, not a bug.
- **PDF must have a text layer.** A scanned PDF returns the graceful "no text layer" error (Day 22) — use a text-based paper for item 6 or it "fails" for the wrong reason.
- **Mute under load.** Item 10 covers basic mute. If you stress it (mute *during* PDF summarize), the MUTED re-check bows out at the next LLM-call boundary, not instantly mid-tool (tool-calling skill) — that's correct behavior.
- **Python version drift — flag for Day 29, don't act today.** CLAUDE.md and the locked stack pin **Python 3.12**; the Day 27 environment line says **3.13.5**. Nothing to change on a freeze day, but Day 29's README prerequisites must state the version you actually ship on — note it now while it's in view.

---

## 6. Descope order (if the 4h runs out)

**Protect, top-down:**
1. Core demo passing — voice loop + project memory + PDF + web search + app launch + timers
2. Cross-project isolation check
3. `demo_script.md` edge-case updates

**Cut first, bottom-up:**
- SQLite memory gap fix (D-28-1) → v2 cleanup
- grounded_search removal → just document (D-28-2 Option A)
- Latency work → already deferred (D-4)
- grounded_search retest → if quota is clearly still blocked, document and move on rather than waiting on a reset

---

## 7. Commit plan (end of day)

```
[ ] docs: day 28 full demo pass — results + new edge cases in demo_script
[ ] (conditional) fix(memory): persist voice-loop turns to sqlite memory in _persist_turn
[ ] (conditional) chore(tools): document grounded_search quota limitation   # if not removing
[ ] docs: day 28 journal + status
```

---

**Bottom line:** today is about proving the whole thing still works end-to-end and freezing it — not adding anything. Make the two decisions in §1 up front, run the pass twice, fix only what's broken, and resist touching the orchestrator unless the SQLite gap genuinely earns it.
