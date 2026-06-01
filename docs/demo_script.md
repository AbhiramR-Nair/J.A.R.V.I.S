# Manual Demo Script

Run this checklist after any change to voice, LLM, memory, or prompt paths.
All probes can be done via PTT or `POST /chat`. Check all boxes before committing.

---

## 1. Voice + persona (Day 13)

- [ ] "What time is it?" → short reply ending in "sir", no preamble, no "Certainly!"
- [ ] "Tell me a joke." → dry, brief, British register — not a knock-knock
- [ ] "Switch to the kinase project." → curt acknowledgement, no filler
- [ ] "I'm a bit tired today." → brief acknowledgement + offered lighter pace
- [ ] Multi-turn: "Tell me about T315I." → concise technical answer + offered follow-up.
      Then "And how does Ponatinib differ?" → persona holds across the second turn
- [ ] Five consecutive replies contain no "Certainly!", "I'd be happy to", "Of course!", "Great question!"
- [ ] `GET /debug/system-prompt` returns 6 sections assembled in order (length_chars ~5300+)

## 2. Voice quality (Day 13)

- [ ] TTS voice is clearly British male register (en_GB-alan-medium)
- [ ] No chipmunk or slow-motion pitch artifacts
- [ ] No regression in TTS latency vs. previous session

## 3. PTT voice loop regression (Day 11-12)

- [ ] Hold Alt+Space, ask "What's the capital of France?", release → spoken answer within ~5s
- [ ] Mute toggle (Ctrl+Alt+J) stops TTS mid-speech
- [ ] Mute → PTT → unmute cycle returns to IDLE cleanly
- [ ] State badge in UI tracks: idle → listening → transcribing → thinking → speaking → idle

## 4. Audio robustness (Day 12)

- [ ] `POST /audio/device` with index 999 → HTTP 400 (invalid device rejected)
- [ ] Settings panel "Test mic" → green badge with peak amplitude after speaking
- [ ] Settings panel "Test mic" → yellow "Silent" badge when quiet

## 5. Memory + persistence (Day 11)

- [ ] After a factual exchange, ask "What did we just discuss?" → references prior context
- [ ] `SELECT COUNT(*) FROM memory ORDER BY id DESC LIMIT 5;` shows recent entries in jarvis.db

## 6. Tool-calling — Week 4 (Days 20–26)

### 6.1 Project memory tools

- [ ] "Switch to the kinase project." → set_active_project fires → project badge in status bar flips to "kinase" within 1 s (watch for `project_changed` WS event in log)
- [ ] "Log this: T315I shows 40-fold shift in TKI binding" → log_to_project fires (confirm `tool_call: log_to_project` in log) → spoken confirmation ("Logged to project 'kinase', sir") → `SELECT COUNT(*) FROM memory WHERE project_id = <kinase_id>` grows by 1
- [ ] "What did we just log?" → recall_from_project fires → spoken answer includes T315I note

### 6.2 PDF summarization

- [ ] Drag a text-based PDF onto the window → say "summarize this" → summarize_paper fires → structured spoken summary (key claims, methods, results). *Target ~15 s; under Gemini API load may be longer — latency alone is not a fail.*
- [ ] (optional) "Summarize arxiv 2301.07041" → fetch_arxiv fires then summarize_paper → spoken summary

### 6.3 Web search

- [ ] "What are the latest papers on ABL1 inhibitors?" → web_search (Tavily) fires → sources block renders in UI → spoken answer

### 6.4 App launcher

- [ ] "Open VS Code" → VS Code launches → spoken "Opening Visual Studio Code, sir."
- [ ] Unknown app (e.g. "Open Photoshop") → soft-error spoken response, no crash

### 6.5 Timers

- [ ] "Set a timer for 1 minute" → spoken confirmation → toast fires after 60 s → spoken completion when loop is idle
- [ ] Two overlapping timers → both toasts fire, both spoken completions heard

---

## 7. Cross-project isolation (hard invariant — run every pass)

After logging the T315I note to the kinase project (§6.1):

- [ ] "Switch to the general project." → set_active_project fires → badge flips to "general"
- [ ] "What did we conclude about T315I?" → recall_from_project fires → spoken reply must NOT surface the kinase log (expect "I don't have any notes on that" or similar)
- [ ] Switch back: "Switch to the kinase project." → "What did we conclude about T315I?" → kinase log surfaces correctly

This verifies that `project_id` scoping in `vector_store.search()` is enforced end-to-end.

---

## Known limitations (as of Day 27–28)

- **grounded_search — 429 RESOURCE_EXHAUSTED** (confirmed Day 28): Quota blocked since Day 25 (shared Google Search grounding free-tier bucket). Soft-error path confirmed: grounded_search fires, returns error dict, LLM answers from its own knowledge — no crash, no unhappy path. Gemini may also route current-fact queries to web_search (Tavily) instead. Not a regression.
- **Gemini API latency**: Under API load, embedding + generation calls stack to 23–27 s/turn (vs. <4 s target). This is an external API performance issue, not a code bug. Scoped timeout fix designed (Day 27 Decision D-4) and deferred to v2 to avoid tool-calling regressions. If still >20 s during this pass, note it in run results — do not panic-fix on a freeze day.
- **SQLite memory table gap**: Fixed Day 28. `_persist_turn` now calls `sqlite_store.save_memory()` after `vector_store.add()` for turns scoring ≥ threshold — voice-loop turns are now SQL-queryable. The `memory` table grows from both explicit tool calls (`log_to_project`, `summarize_paper`) and auto-scored voice turns (score ≥ 4.0).

---

## End-of-week milestones

- **Week 2 (Day 14):** "I can hold Alt+Space, ask 'what's the capital of France?', and hear a spoken answer within 4 seconds."
- **Week 3 (Day 19):** "The blob looks alive — it reacts to my voice and changes state visibly."
- **Week 4 (Day 30):** "I used Jarvis for actual work today — summarised a paper, searched the web, logged a note, opened an app."
