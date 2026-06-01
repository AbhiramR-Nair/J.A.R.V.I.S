# Day 27 — Latency + Quota Hardening (replaces optional wake word)

**Period covered:** Day 27 (Week 4, Day 8 — repurposed from the optional wake-word day)
**Status:** Planned
**Environment:** Windows 11, Python 3.13.5. Verify before starting: backend boots, `tools registered: 11`.

> **Scope note (per CLAUDE.md rule 2):** the written plan's Day 27 is "wake word (optional)."
> This is a deliberate repurposing because wake word was always the first cut and you're
> directing the change. Wake word moves to v2. No new product features are added here —
> this day is purely hardening two live pain points surfaced in the Day 26 status doc:
> Gemini 429 amplification and the ~9.4s TTS cold-start.

> **Workflow reminder (the daily loop):** for each task, write the signature/docstring
> yourself → ask Claude Code to implement → read every line → ask about anything unclear →
> re-type at least one line in your own keystrokes → run the test → commit. Add the 3–5 line
> "what/why" comment block above each non-trivial block. Never accept code you can't explain.

> **Skills to read first:** `voice-pipeline/SKILL.md` (before touching `tts.py`,
> `importance.py`, or `_persist_turn`) and `project-architecture/SKILL.md` (file placement,
> the Lock pattern, graceful-error rule). You do **not** need the tool-calling skill today —
> Task 3 is explicitly text-only and does not touch the tool loop.

---

## Goal

End the day with:

1. The first TTS call of a session responsive (~2–3s, not ~9s).
2. Roughly half the per-turn LLM calls gone (the importance scorer no longer hits the API).
3. A text-only Groq fallback so a Gemini 429 on a chat turn **degrades** to Groq instead of erroring.

All three are free (no billing), reversible, and inside the locked stack.

---

## Decisions to make before coding

Record the choice next to each before writing any code (mirrors the Day 26 Decision A/B/C pattern).

### Decision D-1 — Importance scorer shape

- **D-1a (recommended):** a readable rule-set in `_heuristic_score()` — a handful of `if`
  checks that nudge a 1–10 score. Easy to read at a glance and hand-tune. Fits the
  comprehension goal.
- **D-1b:** a weighted formula with weights in `settings.py`. More "tunable" on paper but
  harder to reason about and easier to mis-weight.

→ **Default: D-1a** unless you specifically want the formula.

### Decision D-2 — Keep the LLM scorer?

- **Recommended:** keep it behind a setting `use_llm_importance_scorer: bool = False`.
  Reversible, costs nothing while off, and lets you A/B later.
- Alternative: delete it entirely. Don't — the reversibility is free.

### Decision D-3 — Groq fallback model

- Make it a setting (`groq_llm_model`), no magic string.
- **Default suggestion:** `llama-3.3-70b-versatile` (answer quality for a fallback that may
  serve a real research question). Alternative: `llama-3.1-8b-instant` (faster, higher daily
  cap) if you'd rather optimise availability over quality.
- **Verify the exact current model string** against Groq's model list before coding —
  model IDs change (CLAUDE.md rule 4).

---

## T-0 — Pre-flight (establish the "before" measurements)

You can't prove the fixes worked without a baseline. Do this first.

**What / How:**
1. Pull latest from main. Boot backend. Confirm startup log shows `tools registered: 11`.
2. Run `demo_script.md` once, clean. Note any pre-existing failures so later regressions are
   attributable to today's changes, not yesterday's.
3. Re-probe the Gemini quota carry-overs (P-2/P-4 from Day 26) now that quota has had time to
   reset: one live `grounded_search()` call. Record OK / still-429.
4. Capture **baseline call count:** do one normal PTT turn (e.g. "tell me a joke"), then grep
   the log for LLM calls in that turn. Expected today: 2 (answer + scorer).
   ```
   grep -i "llm" data/logs/jarvis.log | tail -n 20
   ```
5. Capture **baseline first-call TTS latency:** restart backend, do the first spoken turn of
   the session, note the TTS latency log line. Expected: ~9s.

**Pass criteria:**
- [ ] Backend boots, `tools registered: 11`.
- [ ] Baseline demo_script result recorded.
- [ ] Baseline per-turn LLM count recorded (should be 2).
- [ ] Baseline first-call TTS latency recorded (should be ~9s).
- [ ] grounded_search status recorded (OK / 429).

**Time:** 30 min.

---

## T-1 — Piper warm-up at startup

The quick win. Open the day with it — clean boot + instant morale.

**What:** pay Piper's cold-start cost once at backend startup so your first real query doesn't
eat it.

**Why this works (read before assuming a bigger fix is needed):** the Day 26 status observed
that *subsequent* TTS calls are already fast even though each spawns a fresh `piper.exe`
subprocess. That tells you the ~9s is **not** per-process model loading — if it were, every
call would be slow. It's first-execution OS overhead (Windows page-caching the `.exe` + `.onnx`,
DLL init, Defender scanning the binary on first run). A single warm-up call primes that cache,
which is why warm-up is sufficient and the persistent-subprocess refactor is **not** needed
for this symptom. (That refactor stays a v2 item.)

**How:**
1. Add a `warm_up()` method to `TTSService` in `backend/voice/tts.py`. It runs the Piper
   subprocess on a tiny string (e.g. `"ok"`) to force the first-execution cost, but **skips the
   sounddevice playback step** — startup must be silent. (Do not call `speak()`, which plays
   audio.)
2. Wrap the Piper invocation in try/except: if warm-up fails, log a warning and continue boot.
   A warm-up failure must never crash startup (graceful-error rule).
3. Call `tts.warm_up()` in the `backend/main.py` lifespan startup, right after the TTSService
   is constructed. Log timing: `tts warm-up complete in {ms}ms`.
4. This adds a public method to a service contract → add a one-line mention to
   `voice-pipeline/SKILL.md` service-contract section when done.

**Test:**
1. Restart backend. Confirm `tts warm-up complete in ...ms` appears in the startup log, and
   that no audio plays during boot.
2. Do the first spoken PTT turn of the session. Check its TTS latency log line.

**Pass criteria:**
- [ ] Warm-up line present in startup log; startup is silent.
- [ ] First-call TTS latency now ~2–3s (comparable to subsequent calls), not ~9s.
- [ ] Warm-up failure path tested once (e.g. temporarily point to a bad Piper path) → warning
      logged, backend still boots.

**Time:** 30–45 min.

---

## T-2 — Heuristic importance scorer (the anchor — do it while fresh)

Removes the second LLM call from every turn. Your voice-pipeline skill confirms `_persist_turn`
makes an `importance.score(...)` LLM call after every answer; that's the call that hits Groq's
per-minute limit on heavy testing days and roughly doubles your request count.

**What:** replace the LLM call inside `importance.score()` with a heuristic, keeping the
**same signature and the same 1–10 scale** so `_persist_turn` and the `>= importance_threshold`
comparison need no changes (minimal diff). Gate the old LLM path behind a setting.

**How:**
1. Read `backend/memory/importance.py` and the THINKING/`_persist_turn` section of
   `conversation.py` first. Confirm the current shape is `score(text: str) -> float` returning
   1–10, and that `_persist_turn` calls it then compares against `settings.importance_threshold`
   (default 4.0).
2. Add to `settings.py`: `use_llm_importance_scorer: bool = False`. Leave
   `importance_threshold: float = 4.0` unchanged.
3. Inside `score()`, branch:
   - `if settings.use_llm_importance_scorer:` → existing LLM path (untouched).
   - `else:` → `return _heuristic_score(text)`.
4. Implement `_heuristic_score(text)` per Decision D-1a — a readable rule-set on the 1–10 scale.
   The input is the combined `"User: ...\nAssistant: ..."` string, so **weight the user half
   more** (user intent signals importance better than assistant verbosity). Suggested rules:
   - Very short / trivial (combined text under a small char threshold, or user side is
     "hi"/"ok"/"thanks"/"yes"/"no") → **1–3** (filtered out by threshold).
   - Explicit save signal — regex for `log this`, `remember`, `note that`,
     `for the record`, `don't forget` → **9–10**. (Note: the `log_to_project` tool already
     forces importance=10 for explicit logs; this rule catches conversational "remember X"
     that didn't route through the tool.)
   - Domain/decision signal — numbers with units / identifiers (e.g. `40-fold`, `70 kg`,
     `T315I`), or conclusion words (`therefore`, `we concluded`, `the result was`) → **6–8**.
   - Substantive content (length over a threshold, or named entities) → **5–6**.
   - Default → just below threshold (favours less clutter). Pick the direction deliberately and
     note it in a comment.
5. Keep the existing try/except around scoring in `_persist_turn`. The heuristic effectively
   can't fail (no network), so that path is mostly dormant while the heuristic is active — but
   leave it for when the LLM branch is toggled on. Don't rip it out (minimal diff).
6. Memory writes stay project-scoped — `_persist_turn` already passes `project_id` to
   `vector_store.add`. Do not change that.

**Test:**
1. **Smoke test** (`backend/tests/`, fast, no quota): feed known strings to `_heuristic_score`
   and assert ranges — `"hi"` < 4; `"log this: T315I shows 40-fold resistance shift"` >= 9;
   a normal factual answer in the mid range.
2. **Trivial-filter test:** send "hi" / "ok" via PTT. Confirm ChromaDB does **not** grow:
   ```
   sqlite3 data/jarvis.db "SELECT COUNT(*) FROM memory ORDER BY id DESC LIMIT 20;"
   ```
   (the diagnostic the voice-pipeline skill itself recommends.)
3. **Storage + retrieval test (Day 6 regression):** say "log this: <a real fact>", then later
   "what did we just log?" → confirm it's stored and retrieved (cross-project isolation intact).
4. **Call-count test:** one normal PTT turn → grep the log → LLM calls should now be **1**.
5. **Toggle test:** set `use_llm_importance_scorer=True`, restart, confirm the LLM path runs
   again (2 calls). Set back to False.
6. Full `demo_script.md` pass.

**Pass criteria:**
- [ ] Per-turn LLM calls = 1 for a normal turn (was 2), verified in log.
- [ ] Trivial messages still skip ChromaDB (count doesn't grow).
- [ ] Substantive / "log this" content still lands in ChromaDB and is retrievable.
- [ ] Smoke test passes.
- [ ] Toggle restores the LLM scorer.
- [ ] `demo_script.md` fully passes.

**Watch out for:**
- Old LLM-scored memories and new heuristic-scored ones coexist — the retrieval mix shifts
  slightly. Expected, not a bug.
- Keep `score()`'s signature and 1–10 return scale identical, or you'll be forced to edit
  `_persist_turn` and the threshold comparison (avoid — minimal diff).

**Time:** 2–2.5h including the read-review loop.

---

## T-3 — Groq as a text-only fallback provider

**What:** add Groq behind your existing `BaseProvider`/`router.py` so a Gemini 429 on a
**tool-less** turn falls back to Groq instead of failing. Tool turns are explicitly excluded.

**Why text-only (the correction from planning):** Groq is OpenAI-compatible, so its
function-calling protocol differs from Gemini's. Your `ToolRegistry` emits Gemini-shape schemas
(`parameters_json_schema=`) and your orchestrator's tool loop uses `Part.from_function_response`.
Routing *tool* turns to Groq would need a schema adapter plus a second tool-loop path — that's
its own task (v2), not a Day-27 squeeze. Today, Groq only ever receives plain text.

**How:**
1. **Verify state first (CLAUDE.md rule 4):** check whether a `GroqLLMProvider` already exists
   in `backend/llm/`. The voice-pipeline skill references one, but Day 26 only used Groq for STT
   (Whisper). If a stub exists, extend it; if not, create `backend/llm/groq_llm.py` following the
   `gemini.py` / `openai.py` pattern. Keep it **separate** from the STT Groq client in
   `voice/stt.py` (same SDK/key is fine, but a distinct client — don't entangle the two).
2. Implement `generate(prompt, tools=None) -> LLMResponse` matching `BaseProvider`. Use the
   model from `settings.groq_llm_model` (Decision D-3). Persistent `AsyncGroq` client constructed
   once; `close()` on shutdown (same lifecycle discipline as the STT service).
3. **The routing guard lives in `router.py`, not the orchestrator.** This is the load-bearing
   safety rule. Sketch of the fallback chain:
   ```
   try Gemini
   on 429 / error:
       if request has NO tools AND prompt is short (under a char/token threshold):
           try Groq
       # tool turns and long-context turns skip Groq entirely
       then fall through to your existing OpenAI fallback / surfaced error
   ```
   - **No tools → Groq eligible.** A tool turn must never reach Groq.
   - **Short context only.** Groq's TPM is tight; do not send long prompts (PDF summaries) to
     Groq even as fallback — those stay Gemini-only (1M TPM). Add a length check to the guard.
4. Graceful error handling (hard rule): the Groq branch is wrapped; on Groq failure, fall
   through to the next provider or surface a clean user-facing error. No silent swallow.
5. Do **not** route through anything but the router. The orchestrator already goes through
   `router.generate()` — leave it untouched.

**Test:**
1. **Fallback happy path:** briefly set a bad Gemini key (the Day 4 fallback trick — don't burn
   real quota), send a plain chat turn ("tell me a joke"). Confirm the log shows
   Gemini fail → Groq used → response returned.
2. **Tool-turn isolation (the key safety test):** with Gemini still "broken," ask a tool query
   ("what time is it?"). Confirm it does **not** route to Groq — it should hit your existing
   non-Groq path or surface an error. Verify in the log that Groq received no tool request.
3. **Long-context isolation:** with Gemini still "broken," trigger a PDF summary. Confirm it does
   not route to Groq.
4. **Both-down:** break Gemini and Groq, send a chat turn → clean user-facing error, no crash.
5. Restore the Gemini key → confirm Gemini is primary again and Groq is not invoked when Gemini
   is healthy.

**Pass criteria:**
- [ ] Forced Gemini 429 on a short text turn → Groq answers; fallback chain logged.
- [ ] Tool turns never reach Groq (verified in log).
- [ ] Long-context turns never reach Groq.
- [ ] Both-down → graceful error, no crash.
- [ ] Gemini remains primary when healthy.

**Watch out for:**
- The voice-pipeline skill's mention of `GroqLLMProvider` may be stale — check before assuming
  it exists.
- Verify the Groq model string is current before coding.
- If `tools` is somehow passed to the Groq provider, it should ignore them or raise — but the
  real defence is the router guard never sending tool turns there.

**Time:** ~1.5h.

---

## T-4 — Carry-overs (flex — first to cut if you run long)

From the Day 26 heads-up. Pure win if reached; safe to drop to Day 28.

**What / How / Pass:**
1. **grounded_search live retest (P-2/P-4):** one live call now quota has reset. If OK → note
   it and confirm the PTT path renders the sources block. If still 429 → leave research routed
   to Tavily `web_search` (already working) and document. *Pass:* status recorded; no code change
   if still blocked.
2. **Two concurrent overlapping timers** (Day 26 left this untested): start two (e.g. 1 min and
   90s). Confirm both toasts fire and both C2 speech paths run, and the `_active_timers` set
   GC-guard holds (neither timer is dropped). *Pass:* both fire correctly; no dropped timer.
3. **Add `demo_script.md` items #7 and #8** (open VS Code; set a 1-minute timer) — these were
   placeholders in the Day 28 plan. *Pass:* both committed and they pass.

**Time:** flexible; cut line for the day.

---

## Consolidated Completion Criteria

```
T-0 PRE-FLIGHT
[ ] Backend boots, tools registered: 11
[ ] Baseline demo_script, per-turn LLM count (2), first-call TTS latency (~9s) recorded
[ ] grounded_search status recorded

T-1 TTS WARM-UP
[ ] Warm-up line in startup log; startup silent
[ ] First-call TTS latency now ~2-3s
[ ] Warm-up failure path → warning, no boot crash

T-2 HEURISTIC SCORER
[ ] Per-turn LLM calls = 1 for normal turns (verified in log)
[ ] Trivial messages skip ChromaDB
[ ] Substantive / "log this" content still stored and retrievable
[ ] Smoke test passes; toggle restores LLM scorer
[ ] demo_script.md fully passes

T-3 GROQ TEXT-ONLY FALLBACK
[ ] Forced Gemini 429 on text turn → Groq answers (chain logged)
[ ] Tool turns never reach Groq
[ ] Long-context turns never reach Groq
[ ] Both-down → graceful error, no crash
[ ] Gemini primary when healthy

T-4 CARRY-OVERS (flex)
[ ] grounded_search retested / documented
[ ] Two concurrent timers verified
[ ] demo_script.md items #7, #8 added
```

---

## Git Commits (logical, per convention)

```
[ ] perf(tts): warm up piper at startup to remove first-call cold start
[ ] feat(config): use_llm_importance_scorer flag (default off)
[ ] perf(memory): heuristic importance scorer; keep llm scorer behind setting
[ ] feat(llm): groq as text-only fallback provider on gemini 429
[ ] test: heuristic scorer smoke test
[ ] docs: day 27 journal + status            (if T-4 reached: + carry-overs)
```

---

## Time Budget

5–6 hours. Energy curve: T-0 + T-1 first (clean boot, quick win), T-2 while sharp,
T-3 after lunch, T-4 only if the first three landed clean. If you run long, cut T-4 to Day 28.

---

## Deferred to v2 (do not let these creep in)

- Wake word (openWakeWord) — the originally-planned Day 27.
- Groq **tool-calling** — needs the Gemini→OpenAI schema adapter and a second tool-loop path.
- Local-LLM offline mode — viable only as a slower, weaker-at-tools optional mode (Month 3 per
  the roadmap); not a fix for the 429s.
- Sentence-chunk TTS streaming and persistent Piper subprocess — not needed for the cold-start
  symptom.

---

## Files likely to change this day

```
NEW:
  backend/llm/groq_llm.py            -- Groq text-only fallback provider (if not already stubbed)
  backend/tests/test_importance.py   -- heuristic scorer smoke test
  docs/project_status/PROJECT_STATUS(DAY_27).md

EDIT:
  backend/voice/tts.py               -- warm_up() method (synth, skip playback)
  backend/main.py                    -- call tts.warm_up() in lifespan startup
  backend/memory/importance.py       -- _heuristic_score() + branch on setting
  backend/config/settings.py         -- use_llm_importance_scorer, groq_llm_model
  backend/llm/router.py              -- Groq fallback guard (no-tools + short-context only)
  .claude/skills/voice-pipeline/SKILL.md  -- note warm_up() in service contracts
  docs/journal.md                    -- Day 27 entry
  docs/demo_script.md                -- items #7, #8 (if T-4 reached)
```
