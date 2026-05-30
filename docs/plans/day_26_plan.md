# Day 26 Plan — App Launcher + Timers

**Period covered:** Day 26 (Week 4, Day 7 — App Launcher + Timers)
**Goal:** "Open VS Code" launches an app via voice; "Set a Pomodoro for 25 minutes" fires a Windows toast (and announces itself). Two new tools shipped → `tools registered: 11`.
**Prerequisites carried in:** Day 25 left `grounded_search` and the web_search-via-PTT path unverified (quota-blocked). Part 0 closes those before any new work.
**Environment (confirm at boot):** Windows 11, Python 3.13.5, `tavily-python==0.7.25`, `google-genai==2.6.0`. New deps this day: `plyer`, `PyYAML` (verify/pin).
**Time budget:** ~4 hours (order-of-operations in §8).

> Day 26 turns Jarvis from "answers questions and reads the web" into "does things on the
> machine." Both deliverables are plain tools following the 4-step pattern from
> `tool-calling-pattern/SKILL.md`. The only real engineering question is the timer's
> relationship to the voice loop's audio device — read §2 Decision C before writing `timer.py`.

---

## 0. Pre-flight — close the Day 25 carry-over (do this FIRST)

Day 25 shipped both search tools but couldn't verify the live Gemini-grounded path or the
full PTT loop because the `gemini-2.5-flash` 20-RPD free-tier quota was exhausted during
summarizer testing. These checks are **quota-sensitive**, so run them immediately after the
daily reset, *before* Day 26 PTT testing burns quota again (every voice turn also makes a
second LLM call for importance scoring — see `voice-pipeline/SKILL.md`).

### P-1 — Re-run the model probe (T-0a)

```bash
python -c "
import asyncio
from backend.llm.router import get_gemini_provider
async def probe():
    for m in ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-flash-lite-latest']:
        try:
            r = await get_gemini_provider().generate('ping', model=m)
            print(f'{m}: OK')
        except Exception as e:
            print(f'{m}: {type(e).__name__} — {e}')
asyncio.run(probe())
"
```

Record which models answer vs. 429 today. This tells you whether the grounding-model decision
below even needs revisiting.

### P-2 — Live `grounded_search` call (the check Day 25 deferred)

```bash
python -c "
import asyncio
from backend.llm.router import get_gemini_provider
async def t():
    result = await get_gemini_provider().grounded_search('weather in Thiruvananthapuram today')
    print('OK — sources:', len(result['sources']))
    print('text snippet:', result['text'][:100])
asyncio.run(t())
"
```

**Decision tree (one-line config change, no code):**

| Result | Meaning | Action |
|---|---|---|
| Prints `OK — sources: N` | `gemini-flash-lite-latest` supports `google_search` grounding | Leave `grounding_model` as-is. Day 25 Decision B confirmed. |
| `LLMRateLimitError` (429) | flash-lite quota also hit; capability still unknown | Wait for reset / retry once; if still 429, fall back to `gemini-2.5-flash` for this check only |
| A capability error (NOT 429), e.g. tool-not-supported | flash-lite can't ground | Set `grounding_model = "gemini-2.5-flash"` in `settings.py` (one line), re-run P-2 |

If you switch `grounding_model`, that's a real commit: `fix(config): use gemini-2.5-flash for grounding (flash-lite lacks google_search)`.

### P-3 — `web_search` end-to-end via PTT (Problem A from Day 25)

Day 25 confirmed `web_search` returns real results via direct handler call, but **never through
the voice loop** — because `/chat` doesn't attach tools; only `ConversationOrchestrator._run_pipeline()` does. Confirm the real path now:

1. Start backend + frontend, blob idle.
2. Hold Alt+Space: *"What are the latest ABL1 kinase inhibitor papers?"*
3. In `data/logs/jarvis.log` confirm the tool-call trace:
   ```
   tool_call iter=0: web_search({...})
   tool_result: web_search -> ...
   ```
4. Hear the spoken summary (cited by title, no spoken URLs — the Day 25 no-URL rule).
5. **Visual confirm (Day 25 item 10):** the dim-cyan "Sources" block renders in `ChatPanel` with clickable, truncated links that open in a browser.

### P-4 — `grounded_search` end-to-end via PTT

Hold Alt+Space: *"What's the weather today?"* → confirm it routes to `grounded_search` (not Tavily) in the log, speaks the answer, and shows grounding sources.

> If P-2/P-4 stay quota-blocked even after reset, that's acceptable slack: Day 26 tool work
> (`open_app`, `set_timer`) consumes almost no Gemini quota for the tools themselves, so do the
> build first and retry the grounded checks at end of day.

---

## 1. Today's agenda

Two tools, both via the standard 4-step pattern:

- **`open_app(name)`** → `backend/tools/app_launcher.py` — looks up `name` in a `backend/tools/apps.yaml` whitelist, launches it with `subprocess.Popen`. Whitelist-only: the LLM supplies a *key*, never a command. Unknown key → graceful "add it to apps.yaml" soft-error.
- **`set_timer(minutes, label)`** → `backend/tools/timer.py` — schedules a background `asyncio` task that fires a `plyer` Windows toast on completion (and optionally announces via TTS — see Decision C). Strong task reference required to survive GC.

Plus the usual supporting edits: `50_tools.md` directives, `settings.py` additions, smoke tests, journal/status/commits.

**File placement** (per `project-architecture/SKILL.md` canonical structure):

| File | Status | Contains |
|---|---|---|
| `backend/tools/apps.yaml` | NEW | The launch whitelist |
| `backend/tools/app_launcher.py` | NEW | `open_app` tool (note: file name ≠ tool name, matches canonical structure) |
| `backend/tools/timer.py` | NEW | `set_timer` tool |
| `backend/main.py` | EDIT | Two lifespan imports |
| `backend/config/settings.py` | EDIT | Timer/notification settings |
| `backend/prompts/system/50_tools.md` | EDIT | Two new directives |
| `backend/requirements.txt` | EDIT | Pin `plyer`, `PyYAML` |

---

## 2. Decisions to make before coding

Per CLAUDE.md rule #2: these are hard-to-reverse choices. Pick before writing the handlers.

### Decision A — `apps.yaml` entry schema

**Option A1 — flat string (matches the v2 plan's sketch):**
```yaml
apps:
  vscode: "C:/Users/<you>/AppData/Local/Programs/Microsoft VS Code/Code.exe"
  streamlit_dashboard: "powershell -Command \"cd C:/proj; streamlit run main.py\""
```
- *Pro:* dead simple, matches the plan literally.
- *Con:* `open_app` must *guess* how to launch each entry (raw exe path? shell command? Store app?). The moment you mix a `.exe` path and a PowerShell command in one file, the heuristics get fragile, and quoting a PowerShell string inside YAML is error-prone.

**Option A2 — structured entry with explicit `type` (recommended):**
```yaml
apps:
  vscode:
    type: exe
    path: "C:/Users/<you>/AppData/Local/Programs/Microsoft VS Code/Code.exe"
  chrome:
    type: exe
    path: "C:/Program Files/Google/Chrome/Application/chrome.exe"
  obsidian:
    type: exe
    path: "C:/Users/<you>/AppData/Local/Obsidian/Obsidian.exe"
  streamlit_dashboard:
    type: shell
    command: ["powershell", "-NoExit", "-Command", "cd 'C:/path/to/proj'; streamlit run main.py"]
  # Office (Click-to-Run desktop): "Office16" is the folder for 2016/2019/2021/365 — NOT a year.
  # 32-bit Office on 64-bit Windows lives under "C:/Program Files (x86)/...". The non-obvious part
  # is the exe names: WINWORD / POWERPNT / EXCEL. Confirm the path with `where WINWORD` or Get-StartApps.
  word:
    type: exe
    path: "C:/Program Files/Microsoft Office/root/Office16/WINWORD.EXE"
  powerpoint:
    type: exe
    path: "C:/Program Files/Microsoft Office/root/Office16/POWERPNT.EXE"
  excel:
    type: exe
    path: "C:/Program Files/Microsoft Office/root/Office16/EXCEL.EXE"
  # Spotify: a Store install uses `type: store` with the AUMID below (get it from Get-StartApps).
  # A desktop install instead uses `type: exe`, path "C:/Users/<you>/AppData/Roaming/Spotify/Spotify.exe".
  spotify:
    type: store
    app_id: "<AUMID from Get-StartApps, e.g. SpotifyAB.SpotifyMusic_...!Spotify>"
```
- *Pro:* `open_app` dispatches on `type`, no guessing; robust across exe / shell / Store; PowerShell args live in a **list** so there's no YAML-inside-shell quoting hell. Every launch is list-form `Popen` → no `shell=True` anywhere → no injection surface even within whitelisted entries.
- *Con:* slightly more verbose YAML.

**Recommendation: A2.** You will mix a raw `.exe` (VS Code) and a shell command (Streamlit) on day one, and the completion criterion wants 4+ apps. The structured form is ~5 extra lines and removes a whole class of launch bugs. This *deviates from the plan's flat-string sketch* — flagging it explicitly per CLAUDE.md; the schema was never a locked decision, only loosely illustrated.

### Decision B — where do timer tasks live?

The Day 25 status note already flagged: a timer is a long-lived background task and **needs a strong reference or it gets silently GC-cancelled** (same failure mode as `self._inflight` in the voice pipeline).

- **Option B1 — module-level set in `timer.py`** (`_active_timers: set[asyncio.Task]`): self-contained, the tool owns its state. Matches the status doc's first suggestion.
- **Option B2 — on `app.state`**: more discoverable/testable, mirrors `app.state.audio_recorder`. But tool handlers don't receive `app`, so you'd need a module-level reference set at lifespan or an accessor (`get_orchestrator()`-style, like Day 25's `get_gemini_provider()`).

**Recommendation: B1 for v1.** Tool handlers are plain functions with no DI; a module-level set + a done-callback to discard finished tasks is the least-friction correct answer. If Month 2 adds "list active timers" / "cancel timer", promote to an accessor then.

### Decision C — how does a timer "speak"? (read before `timer.py`)

This is the only genuinely subtle design call today. A timer fires from a background task,
**decoupled from the voice loop**. But `TTSService` and the sounddevice output are owned by the
orchestrator. If a timer calls `tts.speak()` while a turn is in `SPEAKING` (mid-reply) or
`LISTENING` (recording), you get two writers on the audio device. The `voice-pipeline/SKILL.md`
Lock pattern exists precisely to prevent this kind of collision.

| Option | What fires on completion | Trade-off |
|---|---|---|
| **C1 — toast only** | `plyer` toast + `timer_fired` WS event | Bulletproof, zero orchestrator coupling. But the completion criterion literally says "notification **+ speech**", so this technically under-delivers. |
| **C2 — toast + Piper, only if IDLE (recommended)** | toast + WS event always; speak via the existing standalone TTS path **only if `orchestrator.state == IDLE`** | Meets the criterion. Introduces a *tiny* race (a turn could start in the millisecond gap), acceptable for a single-user daily-driver and consistent with the project's documented "acceptable degradation" philosophy. Costs a small bit of orchestrator-reach plumbing. |
| **C3 — toast + frontend chime** | toast + WS event; the React side plays a short audio cue | Decoupled and simple, but a chime isn't "speech." |
| **C4 — orchestrator speak-queue** | timer enqueues a speak request the state machine drains when IDLE | The "correct" long-term design; out of scope for a 4-hour day. Month 2. |

**Recommendation: C2, with C1 as the in-day descope.** Always do the toast + WS event (that's
the reliable Windows-native notification). Attempt Piper speech only when the voice loop is idle.

**The lock-safe way to reach the orchestrator** (mirrors Day 25's lazy local import to dodge
circulars): inside `_run_timer`, *after* the toast, do a quick lock-guarded state read; if and
only if it's `IDLE`, speak. **Do not hold the orchestrator lock across `tts.speak()`** — that
violates the Lock pattern and would freeze mute. Read the state under the lock, release, then
speak. If wiring an orchestrator accessor turns fiddly inside the time budget, ship C1 and leave
"speak on idle" as a one-line-described polish item — be honest in the status doc about which one shipped.

> Mute interaction: the toast (a *visual* notification) is fine even when muted — mute is about
> the voice channel, not all output. But the C2 speech must be suppressed when muted: since
> `MUTED != IDLE`, the `== IDLE` check already handles this. No extra guard needed.

---

## 3. Tasks

### T-0 — Pre-flight (Part 0 above)

Close P-1…P-4. If `grounding_model` needs switching, do it now. ~30–45 min.

### T-1 — Dependencies: `plyer` + `PyYAML`

```bash
pip install plyer pyyaml
pip freeze | findstr /I "plyer pyyaml"   # capture exact versions
# add the two pinned lines to backend/requirements.txt
```

- **Verify `PyYAML` is importable** (`python -c "import yaml; print(yaml.__version__)"`). It may already be present transitively (chromadb pulls it), but the `apps.yaml` parser depends on it, so pin it explicitly per the "verify versions" rule.
- **Verify a `plyer` toast actually appears — fail fast.** plyer's Windows notification backend is historically flaky and Windows Focus Assist / notification settings can *silently* suppress toasts. Run a one-liner before building anything on top of it:
  ```bash
  python -c "from plyer import notification; notification.notify(title='Jarvis', message='toast test', timeout=10)"
  ```
  If nothing appears: check Windows **Settings → System → Notifications** (and Focus Assist) first, then the plyer backend. Knowing the toast works *before* you wire it into a timer saves an hour of "is it my code or is it plyer" debugging. Note the result in the status doc.

Commit: `chore(deps): pin plyer and pyyaml in requirements`.

### T-2 — `backend/tools/apps.yaml` (whitelist)

Create it in the structured form (Decision A2). Populate the entries for your machine — the
starter set is VS Code, Chrome, Obsidian, your Streamlit dashboard, Word, PowerPoint, Excel, and
Spotify (8 entries, comfortably past the "4+ apps launch via voice" criterion). Use **absolute
paths** with forward slashes (Windows accepts them) or escaped backslashes. Confirm exe paths
with `where <name>` (or right-click the Start tile → *Open file location*) and Store-app AUMIDs
with `Get-StartApps` in PowerShell — don't commit a guessed path or ID.

`data/`-style note: `apps.yaml` is config (committed), *not* runtime data — it belongs in
`backend/tools/`, version-controlled, no `.gitignore` entry. (Real machine paths are fine to
commit for a personal repo; if you later open-source, template them in `apps.example.yaml`.)

### T-3 — `open_app` tool (`backend/tools/app_launcher.py`)

Write the signature + schema yourself first (the read-review habit), then have Claude Code
implement the body and explain it. Shape:

```python
"""open_app — launch a whitelisted desktop app by name via subprocess."""

import subprocess
import asyncio
from pathlib import Path

import yaml

from backend.tools import registry
from backend.config.settings import settings  # if you route the cap/limits through settings

# Resolve the whitelist relative to THIS file, never the CWD — see Gotchas.
_APPS_YAML = Path(__file__).resolve().parent / "apps.yaml"


@registry.register(
    name="open_app",
    description=(
        "Open or launch a desktop application by name. Use this when the user asks to "
        "open, launch, or start an app (e.g. 'open VS Code', 'launch my dashboard'). "
        "Only apps in the whitelist can be launched; if the requested app is not "
        "available, tell the user to add it to apps.yaml. Do not guess a path."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Whitelist key of the app to launch, e.g. 'vscode', 'chrome'.",
            },
        },
        "required": ["name"],
    },
)
async def open_app(name: str) -> str | dict:
    # Load the whitelist per-call so edits to apps.yaml take effect without a restart.
    # Soft-error on a missing/broken file rather than crashing the turn.
    ...
```

Handler logic, step by step:

1. **Load + parse** `apps.yaml` per-call (cheap; lets the user add an app and use it immediately). Wrap file read + `yaml.safe_load` in try/except → on failure return a **soft-error dict** `{"error": ..., "type": ...}` (per `tool-calling-pattern/SKILL.md` hard-vs-soft rules — a config problem is user-facing, not an ERROR-state crash).
2. **Look up** `name` (case-insensitive is friendlier — the LLM may send "VS Code" or "vscode"). If not found → soft-error dict whose message tells the LLM to advise adding it to `apps.yaml`. **Never** build a command from the raw `name`.
3. **Build list-form `Popen` args by `type`** (the Windows-specific bit that's easy to botch):
   ```python
   # Three launch modes, all list-form (no shell=True → no injection surface):
   #   exe   -> run the binary directly
   #   shell -> a pre-built arg list (e.g. a PowerShell invocation from apps.yaml)
   #   store -> Microsoft Store app via cmd's `start shell:AppsFolder\<id>`
   def _popen_args(entry: dict) -> list[str]:
       kind = entry["type"]
       if kind == "exe":
           return [entry["path"]]
       if kind == "shell":
           return entry["command"]                       # already a list in apps.yaml
       if kind == "store":
           return ["cmd", "/c", "start", "", f"shell:AppsFolder\\{entry['app_id']}"]
       raise ValueError(f"unknown app type: {kind!r}")
   ```
   The empty `""` after `start` is the window-title placeholder (without it `start` may treat the target as a title).
4. **Launch off the event loop, fire-and-forget:**
   ```python
   # subprocess.Popen returns once the process is spawned; we never .wait() (don't block
   # the loop on the app's lifetime). run_in_executor keeps even the brief spawn syscall
   # off the event loop, consistent with the async-first convention.
   loop = asyncio.get_running_loop()
   await loop.run_in_executor(None, lambda: subprocess.Popen(_popen_args(entry)))
   ```
   Wrap the launch in try/except → `FileNotFoundError` / `OSError` (bad path, app uninstalled) → **soft-error dict**, not a hard crash.
5. **Return** a confirmation string, e.g. `f"Opening {name}."` On any soft failure, return the dict so the LLM phrases it gracefully.

**Direct test (unit level, before PTT):**
```bash
python -c "import asyncio; from backend.tools.app_launcher import open_app; print(asyncio.run(open_app('vscode')))"   # VS Code opens
python -c "import asyncio; from backend.tools.app_launcher import open_app; print(asyncio.run(open_app('nope')))"     # soft-error dict, no crash
```

### T-4 — `set_timer` tool (`backend/tools/timer.py`)

Signature + schema first, then implement. Shape:

```python
"""set_timer — schedule a background countdown that fires a Windows toast on completion."""

import asyncio

from plyer import notification

from backend.tools import registry
from backend.config.settings import settings

# Strong references to in-flight timer tasks. Without this set, asyncio GC-cancels the
# task and the timer silently never fires (same trap as self._inflight in the voice loop).
_active_timers: set[asyncio.Task] = set()


@registry.register(
    name="set_timer",
    description=(
        "Set a countdown timer. Use this when the user asks to set a timer, alarm, "
        "reminder, or Pomodoro for a number of minutes. Always confirm the duration "
        "back to the user. Duration may be fractional (0.5 = 30 seconds)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "minutes": {
                "type": "number",
                "description": "Timer duration in minutes; may be fractional (e.g. 0.5).",
            },
            "label": {
                "type": "string",
                "description": "Optional label, e.g. 'Pomodoro' or 'Tea'. Defaults to 'Timer'.",
            },
        },
        "required": ["minutes"],
    },
)
async def set_timer(minutes: float, label: str = "Timer") -> str | dict:
    ...


async def _run_timer(minutes: float, label: str) -> None:
    # Sleeps, then fires the toast (always) + WS event (always) + speak-if-idle (Decision C2).
    ...
```

Handler logic:

1. **Validate** `minutes`: `> 0` and `<= settings.timer_max_minutes` (24h sanity cap). Out of range → soft-error dict.
2. **Schedule + keep a strong reference:**
   ```python
   task = asyncio.create_task(_run_timer(minutes, label))
   _active_timers.add(task)
   task.add_done_callback(_active_timers.discard)   # auto-clean when it finishes/cancels
   ```
3. **Return immediately:** `f"Timer set for {minutes:g} minute(s): {label}."` (don't await `_run_timer`).

`_run_timer(minutes, label)`:

1. `await asyncio.sleep(minutes * 60)`.
2. **Toast (always), off the loop + guarded** — `plyer.notification.notify` is synchronous and can raise on Windows:
   ```python
   # plyer notify is blocking + can fail on Windows -> executor + try/except (soft, logged).
   loop = asyncio.get_running_loop()
   try:
       await loop.run_in_executor(
           None,
           lambda: notification.notify(
               title="Jarvis",
               message=f"{label} — time's up",
               timeout=settings.notification_timeout_seconds,
           ),
       )
   except Exception as e:
       logger.warning(f"timer toast failed: {e}")
   ```
3. **Broadcast `timer_fired`** over WebSocket (lazy local import to avoid circulars, exactly like Day 25's `_broadcast_sources`):
   ```python
   from backend.api.voice import manager as ws_manager
   await ws_manager.broadcast({"type": "timer_fired", "label": label})
   ```
4. **Speak only if IDLE (Decision C2)** — reach the orchestrator, read its state under the lock, **release before speaking.** If you ship C1 today, skip this step and say so in the status doc.

**Concurrency:** because each call spawns an independent task in `_active_timers`, multiple
overlapping timers "just work" — the criterion needs only that you *test* two at once.

**In-memory caveat:** timers live only for the process lifetime; a backend restart loses pending
timers. That's fine for v1 (the plan never asked for persistent timers) — note it in the status doc.

**Direct test (use a 3-second timer so you don't wait):**
```bash
python -c "
import asyncio
from backend.tools.timer import set_timer, _run_timer
async def t():
    print(await set_timer(0.05, 'Tea'))   # returns immediately, then ~3s later: toast
    await asyncio.sleep(4)                 # keep the loop alive to let _run_timer finish
asyncio.run(t())
"
```

### T-5 — Lifespan imports (`backend/main.py`)

Add two lines to the existing tool-registration block (the import **is** the registration side-effect):

```python
import backend.tools.app_launcher  # noqa: F401
import backend.tools.timer          # noqa: F401
```

Restart and confirm the startup log reads `tools registered: 11`.

### T-6 — System prompt directives (`backend/prompts/system/50_tools.md`)

Per `tool-calling-pattern/SKILL.md`, every new tool needs a one-line directive or the LLM may
ignore it. Add:

- **open_app:** "When the user asks to open/launch/start an application, call `open_app` with the app's whitelist key. The launchable apps are a fixed whitelist; if the app isn't available, tell the user to add it to `apps.yaml` — don't invent a path."
- **set_timer:** "When the user asks to set a timer, alarm, reminder, or Pomodoro for a duration in minutes, call `set_timer` and confirm the duration back."

### T-7 — Settings (`backend/config/settings.py`)

No magic numbers — route the day's tunables through `Settings`:

```python
# App launcher + timer (Day 26)
timer_max_minutes: float = 1440.0          # reject timers > 24h (sanity cap)
notification_timeout_seconds: int = 10     # how long the plyer toast stays up
timer_announce_on_idle: bool = True        # speak "time's up" only when the voice loop is idle (C2)
```

(The `apps.yaml` path is resolved in `app_launcher.py` relative to `__file__`, not put here —
a file adjacent to its module isn't a "magic number.")

### T-8 — Smoke tests, journal, status, commits

- Run the §6 completion checklist; fix what breaks.
- One line in `docs/journal.md`.
- Write `docs/project_status/PROJECT_STATUS(DAY_26).md` in the Day 25 format (what landed, decisions A/B/C as chosen, problems + resolutions, heads-up for Day 27, verification checklist, files changed, commits). **Explicitly record** which Decision-C option shipped and whether P-2/P-4 grounded checks finally passed.
- Pre-add the two Day-26 lines to `docs/demo_script.md` if you want (Day 28 items #7 "Open VS Code" and #8 "Set a timer for 1 minute" are exactly today's features).

---

## 4. The 4-step pattern (quick reminder — both tools follow it)

From `tool-calling-pattern/SKILL.md`:

1. **Create** `backend/tools/<tool>.py` with `@registry.register(name, description, parameters)` above an **`async def`** handler.
2. **Write the description for the LLM**, not the developer — say *when* to call it and end with a directive ("Don't guess a path." / "Always confirm the duration.").
3. **Add the lifespan import** in `main.py` (`# noqa: F401`).
4. **Smoke test via PTT** — confirm the `tool_call` / `tool_result` log trace and that the LLM uses the result in its spoken reply.

JSON-schema reminder: top level is `"type": "object"` with `"properties"` (even if `{}`). **No
`$ref` / `$defs` / `anyOf` / `oneOf` / `allOf`** — never hand it `model_json_schema()` output;
hand-write the dict. `_validate_schema()` rejects the forbidden keys at registration time.

---

## 5. Gotchas specific to Day 26

- **`apps.yaml` path must resolve relative to `__file__`.** `Path("backend/tools/apps.yaml")` breaks depending on where you launched the backend from. Use `Path(__file__).resolve().parent / "apps.yaml"`.
- **PyYAML is a real dependency here.** Verify importable; pin it. `apps.yaml` is dead without it.
- **plyer toasts fail *silently* on Windows.** Focus Assist / notification settings can suppress them with no error. Verify the toast appears (T-1 one-liner) *before* wiring it into the timer. If it never shows: Windows notification settings first, plyer backend second.
- **Strong reference for timer tasks — but NOT for `Popen`.** Don't conflate them. An `asyncio.Task` gets GC-cancelled without a reference (hence `_active_timers`). A `subprocess.Popen` process is an independent OS process that keeps running after the handle is dropped — no reference needed, and you must *not* `.wait()` on it.
- **All three launch modes are list-form `Popen` — no `shell=True`.** Even within a trusted whitelist, list-form avoids quoting bugs and injection surface. Store apps go through `["cmd","/c","start","", "shell:AppsFolder\\<id>"]`.
- **`open_app` failures are SOFT errors, not hard.** Unknown app, missing path, broken YAML → return `{"error": ..., "type": ...}` so the LLM can say "add it to apps.yaml" or "I couldn't find that app." Do **not** raise `ToolSchemaError` for these — that routes to the ERROR state and is wrong UX. Reserve hard errors for structural arg failures.
- **Timer speech shares the audio device with the voice loop.** Never call `tts.speak()` blindly from `_run_timer`. Toast always; speak only when `state == IDLE` (Decision C2), and never hold the orchestrator lock across the TTS call (`voice-pipeline/SKILL.md` Lock pattern).
- **Timers are in-memory** — a backend restart drops pending timers. Acceptable for v1; just document it.
- **`number` vs `integer` for `minutes`.** Use `"number"` so "0.5" and "25" both work; validate `> 0` and `<= timer_max_minutes` in code.
- **All tool verification is via PTT, not `/chat`.** Day 25's Problem A still holds: `/chat` doesn't attach tools. End-to-end checks go through the voice loop; use the `python -c` direct calls only for unit-level confirmation.

---

## 6. Completion criteria

```
PRE-FLIGHT (Day 25 carry-over):
[ ] P-1 model probe run; today's OK/429 status recorded
[ ] P-2 live grounded_search returns sources (or grounding_model switched + re-confirmed)
[ ] P-3 web_search works end-to-end via PTT; tool_call trace in log; sources block renders
[ ] P-4 grounded_search works end-to-end via PTT (quota permitting)

DAY 26:
[ ] tools registered: 11
[ ] plyer toast verified visible on this machine
[ ] apps.yaml has 4+ real entries; open_app launches 4+ apps via voice
[ ] Unknown app via voice → graceful "add it to apps.yaml" spoken response (soft-error)
[ ] set_timer fires at the right time with a toast notification
[ ] Decision C2: timer announces via speech when the loop is idle (or C1 shipped + documented)
[ ] Two concurrent overlapping timers both fire correctly
[ ] timer_fired WS event broadcast (UI can react)
[ ] 50_tools.md has open_app + set_timer directives
[ ] Both tools soft-error (bad path / missing app / toast failure) with no crash
[ ] Journal + PROJECT_STATUS(DAY_26).md written; commits made
```

---

## 7. What to test (read-review loop)

Restart backend + frontend, then via PTT:

1. *"Open VS Code"* → app launches; spoken confirmation. (`tool_call: open_app`)
2. *"Launch my Streamlit dashboard"* → PowerShell/Streamlit window opens.
3. *"Open Excel"* (then *"Open Word"*, *"Open PowerPoint"*, *"Open Spotify"*) → each launches — covers both the `exe` and `store` dispatch paths. Then *"Open Photoshop"* (something NOT in `apps.yaml`) → "I couldn't find that — add it to apps.yaml" (the soft-error path).
4. *"Set a timer for one minute"* → spoken confirmation now; toast (and idle-speech) at 60s.
5. While #4 runs, *"Set a Pomodoro for two minutes"* → both fire independently.
6. Hit Ctrl+Alt+J (mute) → set a 3-second timer via the direct `python -c` call → confirm the **toast still shows** (visual is mute-agnostic) but **no speech** plays (state ≠ IDLE).

Check `data/logs/jarvis.log` for the `tool_call` / `tool_result` lines on each, and watch the
`timer_fired` event reach the frontend.

---

## 8. Order of operations (fits ~4 hours)

| Block | Task | ~Time |
|---|---|---|
| 1 | T-0 pre-flight (grounded_search + web_search PTT) — **first, quota-sensitive** | 30–45 min |
| 2 | §2 Decisions A/B/C — pick and note them | 10 min |
| 3 | T-1 deps + plyer toast verify | 15 min |
| 4 | T-2 apps.yaml + T-3 open_app + direct test | 50 min |
| 5 | T-4 set_timer + direct test | 50 min |
| 6 | T-5 lifespan imports, T-6 50_tools.md, T-7 settings | 15 min |
| 7 | T-7/§7 PTT smoke tests; fix breakage | 30 min |
| 8 | T-8 journal + status + commits | 20 min |

If grounded checks stay quota-blocked: build first (blocks 2–8), retry P-2/P-4 at end of day.

---

## 9. Commits (logical, per CLAUDE.md)

```
[ ] (conditional) fix(config): use gemini-2.5-flash for grounding (flash-lite lacks google_search)
[ ] chore(deps): pin plyer and pyyaml in requirements
[ ] feat(tools): apps.yaml whitelist + open_app launcher (exe/shell/store dispatch)
[ ] feat(tools): set_timer with background task + plyer toast notification
[ ] feat(desktop): timer_fired WebSocket event
[ ] docs(prompts): open_app + set_timer directives in 50_tools.md
[ ] docs: day 26 journal + status
```

---

## 10. If behind (drop-cut order)

Day 26 sits at items 5–6 of the global drop-cut list (`Version_1_plan.md`). Within the day:

1. **Protect:** `open_app` + `set_timer` core (toast). These are the deliverables.
2. **Descope first:** Decision C2 → C1 (toast + WS event, no Piper speech). Honest and shippable.
3. **Then:** trim `apps.yaml` to the 4 mandatory entries; skip Store-app support if no Store app matters to you.
4. **Don't sacrifice** the soft-error paths or the strong-reference timer fix — those are the "code I can explain and won't crash" guarantees, and skipping them creates the exact silent-failure bugs the skill files warn about.

A working `open_app` + `set_timer` with reliable toasts is a genuine daily-driver win even
without idle-speech. Ship the core; mark speech-on-idle as Day 27 slack if needed.
