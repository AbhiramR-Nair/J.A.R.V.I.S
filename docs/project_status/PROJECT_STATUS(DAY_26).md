# Project Status — Day 26

**Period covered:** Day 26 (Week 4, Day 7 — App Launcher + Timers)
**Status:** Complete — both tools shipped and verified via PTT. tools registered: 11.
**Environment:** Windows 11, Python 3.13.5, plyer==2.1.0, PyYAML==6.0.3 (was transitive, now pinned)

> Checkpoint summary for Day 26: Jarvis can now do things on the machine. open_app launches
> whitelisted apps via voice (exe/shell/store dispatch, 7 entries). set_timer fires a Windows
> toast + speaks completion when the voice loop is idle (Decision C2 confirmed).
> Bug fix: TTS no longer reads asterisks aloud. tools registered: 11.
> Read before Day 27 (wake word, optional) or use as polish/buffer day.

---

## 1. What was done

| Task | What landed | Status |
|---|---|---|
| T-0 — Pre-flight | P-1: gemini-2.5-flash OK, gemini-2.0-flash dead (limit:0), gemini-flash-lite-latest OK. P-2: grounded_search quota blocked (grounding quota is separate from generation quota). P-3: web_search via PTT confirmed (tool_call trace, sources block rendered). P-4: deferred (same quota block). | Done (P-2/P-4 pending quota reset) |
| Bug fix — TTS asterisks | `_clean_for_tts()` added to tts.py (regex strips **bold**, *italic*, # headers, bullets, backticks, stray asterisks). `20_behavior.md` strengthened: explicit ban on all markdown formatting in spoken responses. | Done |
| T-1 — plyer + PyYAML | plyer==2.1.0 installed; PyYAML==6.0.3 already present (transitive), now pinned. Toast verified visible on this machine. | Done |
| T-2 — apps.yaml | 7 entries (vscode, chrome, cursor, spotify, word, excel, powerpoint). Decision A2: structured schema with explicit type field. All paths verified with Test-Path / Get-StartApps before committing. | Done |
| T-3 — open_app tool | backend/tools/app_launcher.py. Case-insensitive lookup, list-form Popen (no shell=True), three dispatch modes (exe/shell/store), soft-error on unknown key/bad path/broken YAML. Per-call YAML load (no restart needed for new entries). | Done |
| T-4 — set_timer tool | backend/tools/timer.py. _active_timers module-level set (GC guard + done-callback discard). Toast in run_in_executor. timer_fired WS broadcast. C2 speech path: reads orchestrator state under lock, releases lock, speaks only if IDLE. Validation: minutes > 0 and <= timer_max_minutes. | Done |
| T-5 — Lifespan imports | Two lines added to main.py tool-registration block. tools registered: 11 confirmed. | Done |
| T-6 — 50_tools.md | open_app + set_timer directives added. | Done |
| T-7 — Settings | 3 new fields: timer_max_minutes=1440.0, notification_timeout_seconds=10, timer_announce_on_idle=True. | Done |
| T-8 — Journal + status | This file. | Done |

---

## 2. Key decisions and non-obvious choices

### Decision A — apps.yaml schema: A2 (structured with explicit type)

Chose the structured form over the plan's flat-string sketch. The Day 26 app list mixes exe paths (Chrome, Office) and Store/MSIX apps (Cursor, Spotify) — flat strings would require guessing the launch mode per entry. With `type: exe/shell/store`, open_app dispatches cleanly and every launch is list-form Popen (no `shell=True` anywhere).

### Decision B — timer task storage: B1 (module-level set)

`_active_timers: set[asyncio.Task]` in timer.py. Tool handlers are plain functions with no DI; module-level is the least-friction correct solution. `task.add_done_callback(_active_timers.discard)` keeps the set clean automatically. Month 2 can promote to an accessor if "list/cancel timers" is needed.

### Decision C — timer speech: C2 shipped (toast always, speak only if IDLE)

The C2 path uses a lazy local import of `backend.main.app` inside `_run_timer` (same circular-import avoidance pattern as `_broadcast_sources` in web_search.py). State is read under `orchestrator._lock`, lock released before calling `tts.speak()` — consistent with the voice-pipeline Lock pattern. Confirmed working: `timer: spoke completion for 'Timer'` in log after 1-minute PTT test.

### Unplanned: TTS asterisk bug fix

The system prompt's existing "avoid markdown bullets and headers" directive was not explicit enough — Gemini still used `**bold**` formatting. Fixed with both layers: (1) explicit ban in 20_behavior.md ("asterisks, markdown formatting of any kind — no exceptions"), (2) `_clean_for_tts()` safety net in tts.py that strips all markdown before Piper synthesis. Verified with unit test and PTT.

### grounded_search quota note (Day 25 carry-over)

P-2 and P-4 remain blocked. The 429s are not a code bug — `gemini-2.5-flash` generate() works (P-1 confirmed), but the Google Search grounding feature uses a separate quota bucket exhausted by Day 25 testing. grounding_model config stays `gemini-flash-lite-latest`; its grounding capability is still unconfirmed (429 masks whether it supports google_search). Confirm on Day 27/28 after quota reset.

---

## 3. Problems and resolutions

### Problem A — P-2 grounded_search still quota-blocked

**Symptom:** Both gemini-flash-lite-latest and gemini-2.5-flash returned 429 for `grounded_search()`, even though gemini-2.5-flash passed the P-1 generate() probe.
**Cause:** Google Search grounding has a separate quota bucket from regular generation.
**Status:** Not a code bug. Error-handling path confirmed (returns soft-error dict). Retry Day 27/28.

### Problem B — settings.py fields missing on first timer test

**Symptom:** `AttributeError: 'Settings' object has no attribute 'timer_max_minutes'`
**Cause:** T-7 (settings) was planned after T-4 but timer.py reads settings at call time.
**Fix:** Added the three fields immediately when the error appeared, before retrying.

### Problem C — First TTS call high latency (9443ms for 20 chars)

**Symptom:** "Opening Chrome, sir." took 9443ms vs. typical 2-3s.
**Cause:** Likely Piper binary cold-start on first call of the session. Subsequent calls normal.
**Status:** Accepted. Known Piper warm-up behaviour; not investigated further.

---

## 4. PTT smoke test results

```
1. "Open Chrome"         → open_app({'name': 'chrome'}) → Chrome launched → "Opening Chrome, sir."    ✅
2. "Set timer 1 minute"  → set_timer({'minutes': 1})    → confirmation → toast + C2 speech at 60s     ✅
3. "Open Photoshop"      → soft-error dict → "Photoshop is not in the whitelist..."                   ✅
4. "Open Spotify"        → Store app launched via cmd shell:AppsFolder                                ✅
5. Decision C2           → timer: spoke completion for 'Timer' (orchestrator state was IDLE)           ✅
6. tools registered: 11                                                                                ✅
```

Two concurrent overlapping timers not tested — Day 27 polish item.

---

## 5. Heads-up for Day 27 (Wake word — optional)

Day 27 is the optional wake word day. Core tools are working; all criteria met for attempting it.

### If doing wake word (openWakeWord):
- Always-on audio listener + PTT must coexist without device conflicts
- False-positive handling, mute-during-PTT logic, pause-during-PTT
- Review voice-pipeline/SKILL.md before starting

### If using Day 27 as polish/buffer instead:
1. Confirm grounded_search live call (P-2/P-4) — first after quota resets
2. Two concurrent overlapping timers test
3. Add demo_script.md items #7 (open VS Code) and #8 (set 1-minute timer)
4. Investigate first-call TTS latency

---

## 6. Verification checklist

```
PRE-FLIGHT:
[x] P-1 model probe: gemini-2.5-flash OK, gemini-2.0-flash dead, gemini-flash-lite-latest OK
[~] P-2 grounded_search live — quota blocked, retry Day 27
[x] P-3 web_search via PTT: tool_call trace in log, sources block renders
[~] P-4 grounded_search via PTT — quota blocked, retry Day 27

BUG FIX:
[x] _clean_for_tts() strips bold, italic, headers, bullets, stray asterisks
[x] 20_behavior.md: explicit no-markdown ban in system prompt

DAY 26:
[x] tools registered: 11
[x] plyer toast verified visible on this machine
[x] apps.yaml: 7 real entries; paths verified before committing
[x] open_app launches Chrome (exe) via PTT
[x] open_app launches Spotify (Store) via PTT
[x] Unknown app via PTT → graceful soft-error spoken response
[x] set_timer fires toast at correct time
[x] Decision C2: timer speaks completion when loop is IDLE
[x] timer_fired WS event broadcast confirmed in log
[x] 50_tools.md: open_app + set_timer directives added
[x] Both tools soft-error with no crash
[ ] Two concurrent overlapping timers — Day 27 polish
[~] grounded_search PTT — quota blocked
```

---

## 7. Files changed this day

```
NEW:
  backend/tools/apps.yaml                        -- app launcher whitelist (7 entries)
  backend/tools/app_launcher.py                  -- open_app tool (exe/shell/store dispatch)
  backend/tools/timer.py                         -- set_timer tool with C2 speech path
  docs/project_status/PROJECT_STATUS(DAY_26).md  -- this file

EDIT:
  backend/voice/tts.py                           -- _clean_for_tts() + import re
  backend/prompts/system/20_behavior.md          -- explicit no-markdown/no-asterisk ban
  backend/config/settings.py                     -- 3 new timer/notification settings
  backend/main.py                                -- 2 lifespan imports (app_launcher, timer)
  backend/prompts/system/50_tools.md             -- open_app + set_timer directives
  backend/requirements.txt                       -- plyer==2.1.0 added
  docs/journal.md                                -- Day 26 entry
```

---

## 8. Commits

```
[x] fix(tts): strip markdown formatting before Piper synthesis; ban asterisks in system prompt
[x] chore(deps): pin plyer==2.1.0 in requirements
[x] feat(tools): apps.yaml whitelist + open_app launcher (exe/shell/store dispatch)
[x] feat(tools): set_timer with background task, plyer toast, and idle-state speech (C2)
[x] feat(desktop): timer_fired WebSocket event
[x] docs(prompts): open_app + set_timer directives in 50_tools.md
[x] docs: day 26 journal + status
```
