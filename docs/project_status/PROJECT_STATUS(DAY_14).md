# Project Status — Day 14

**Period covered:** Day 14 (Week 2 Buffer Close-out + v0.2.0 Release)
**Status:** Complete — all mandatory blocks done. Commits `596052d`, `a7d4051`, `1266004`, `7c7a11a`.
**Environment:** Windows 11, Python 3.13.5, FastAPI, React 18 + Vite, Groq Whisper-large-v3, Piper `en_GB-alan-medium`, Gemini 2.5 Flash

> Checkpoint summary for Day 14: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 15.

---

## 1. What has been done

Day 14 was the Week 2 seam day — no new features, just hardening the foundation so that
`v0.2.0-voice-loop` is genuinely reproducible on a fresh clone. Four blocks completed.

| Task | What landed | Status |
|---|---|---|
| A.1 — `scripts/download_models.py` | Written from scratch. Downloads Piper binary (GitHub releases zip, extracts to `piper/piper/`) and both voice files (Lessac + Alan) from Hugging Face. Skip-if-present logic makes it idempotent. Verified by moving Alan files aside and re-running | Done |
| A.2 — `scripts/setup_windows.ps1` | Written from scratch. Sections: UTF-8 console encoding, Python/Node/npm sanity checks, `.venv` creation + `pip install`, `npm install`, `data/` subdirectories, `.env` copy from `.env.example`, `python scripts/download_models.py`. Prints next steps on finish | Done |
| A.3 — Block A commit | `596052d` — both scripts committed together as a single `chore:` commit | Done |
| B.1 — `_handle_error` assert | Already present at line 274 from Day 12 work. No action needed | Done (pre-existing) |
| B.2 — Per-turn `request_id` in logs | `_run_pipeline` now wraps the full pipeline body in `with logger.contextualize(request_id=turn_id):`. All pipeline stages (save → STT → LLM → persist → TTS) share one searchable ID per voice turn | Done |
| B.3 — Block B commit | `a7d4051` — conversation.py change committed as `chore:` | Done |
| C — Regression run | All 8 voice-loop prompts from `demo_script.md` passed: persona probes, voice quality, PTT round-trip, mute in all states, multi-turn context retention | Done |
| Bug fix — mute toggle UI | `mute_toggle` event was toggling `muted` back to `true` after `state_changed` had already cleared it. Fixed by making the `mute_toggle` handler a no-op in React — `state_changed` is the authoritative source | Done |
| UI fix — assistant bubble | `bg-white/10` was invisible on light backgrounds. Changed to `bg-cyan-900/50` to match the cyan accent | Done |
| Fix commit | `1266004` — both UI fixes committed as `fix:` with the journal entry | Done |
| D — Demo video | 2-minute screen recording: PTT round-trip, T315I technical query, multi-turn context, mute toggle, mid-recording cancellation. Saved to `docs/media/week_2_demo.mp4` | Done |
| E — Release tag | `v0.2.0-voice-loop` tagged at HEAD and pushed to GitHub. Release notes published | Done |
| Docs commit | `7c7a11a` — demo video + `docs/plans/day_14_plan.md` committed | Done |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. `download_models.py` — single list of model specs, one helper

The script is built around a flat list of `(url, dest_path, label)` tuples and a
single `_download(url, dest, label)` helper that skips if the file already exists.
This means adding a new voice or model in the future is one tuple — no new logic.
The Piper binary is handled separately (`_ensure_piper`) because it requires zip
extraction rather than a direct file write.

The skip-if-present pattern makes the script safe to re-run at any time. It's called
at the end of `setup_windows.ps1` so a fresh clone can run one script and have
everything it needs.

### 2. `setup_windows.ps1` — sections, not a monolith

Each setup concern (venv, npm, data dirs, .env, models) is an independent section
with its own `[skip]` logic. This means the script is re-runnable after a partial
failure without repeating work already done. It's also easy to read in order: each
section is a complete unit with its own `Write-Host` header.

The UTF-8 encoding line (`[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`)
is placed at the very top — before any section headers — so it takes effect for the
entire session before any Unicode output could appear.

### 3. `logger.contextualize(request_id=turn_id)` — reuse the existing field

The loguru format string only renders `{extra[request_id]}`. Rather than adding a
new `{extra[turn_id]}` slot (which would require editing the format string and
risking a `KeyError` on records that don't carry it), the existing `request_id` field
is reused. The `_patcher` in `logging.py` uses `setdefault`, so an explicit
`contextualize(request_id=...)` wins over the ContextVar default. HTTP request log
lines show a UUID4; voice turn log lines show the 12-char hex `turn_id`. Both are
searchable in the same column.

### 4. Mute-toggle bug — `state_changed` is the authority, not `mute_toggle`

The React app maintained a `muted: boolean` state updated from two sources:
- `mute_toggle` event: `setMuted((m) => !m)` (toggle)
- `state_changed` event: explicit `setMuted(true/false)` based on `event.state`

In `_dispatch_events`, the ordering is:
1. `await _handle_event_side_effects(...)` — calls `on_mute_toggle()`, which
   broadcasts `state_changed(state="idle")` **from inside** the orchestrator
2. `await ws_manager.broadcast(event)` — broadcasts the raw `mute_toggle` event

So the frontend event queue receives `state_changed` first, then `mute_toggle` second.
After `state_changed` set `muted=false`, the `mute_toggle` handler fired and toggled
it back to `true`. The badge then showed "Muted" indefinitely.

The fix removes the side effect from the `mute_toggle` handler entirely. The
`state_changed` event provides all the information needed — `state` and `prev_state`
are explicit, not derived by toggling. This is the correct design: let the backend
state machine be the single source of truth, and have the frontend reflect it rather
than guess.

---

## 3. Problems faced and how they were handled

### Problem 1 — PowerShell parse error from Unicode box-drawing characters

**What happened:** `scripts/setup_windows.ps1` was written with `──` (U+2500, BOX
DRAWINGS LIGHT HORIZONTAL) in the section header comments. PowerShell 5.1 reads
script files with CP1252 encoding by default; U+2500 is a multi-byte UTF-8 character
that CP1252 misinterprets, corrupting the parser's token stream. The result was:

```
At setup_windows.ps1:37 char:59
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
MissingEndCurlyBrace
```

The error was at the `npm` check (line 37) even though the real cause was the
garbled section header on line 19. PowerShell's parser recovered from the corrupt
bytes but mis-counted braces, surfacing the error much later.

**Fix:** Replaced all `──` box-drawing characters and any em dashes inside strings
with plain ASCII (`--` and `-`). PowerShell 5.1 scripts must be pure ASCII or UTF-16
LE with BOM; UTF-8 without BOM is not reliably parsed.

**Rule going forward:** Never use non-ASCII characters in `.ps1` files. The UTF-8
encoding fix that the script sets (`[Console]::OutputEncoding`) applies to *output
display*, not to the parser reading the file itself.

### Problem 2 — `logger.contextualize` indentation error

**What happened:** The Edit tool inserted the `with logger.contextualize(...):` line
into `_run_pipeline` but did not re-indent the body. The `with` block had no indented
content, producing:

```
IndentationError: expected an indented block after 'with' statement on line 326
```

**Fix:** Re-read the affected section, confirmed the actual indentation level (8
spaces for the method body, 12 spaces required inside the `with` block), and replaced
the entire pipeline body with the correctly indented version in a single Edit call.

**Rule going forward:** When wrapping a large existing block in a new scope (`with`,
`if`, `try`), always verify the result visually in the file — indentation errors in
Python are silent until the module is imported. The "don't re-read after edit" rule
applies to verifying a change was applied, not to verifying the change is correct.

### Problem 3 — `turn_id` invisible in log output

**What happened:** `logger.contextualize(turn_id=turn_id)` set the value in loguru's
extra dict, but the log format string only renders `{extra[request_id]}`. The `turn_id`
key was never rendered, making the `findstr "turn_id"` verification return empty.

**Fix:** Changed to `logger.contextualize(request_id=turn_id)`. The `_patcher` in
`logging.py` uses `setdefault`, so an explicit `request_id` from `contextualize` wins
over the ContextVar default. No format string changes required.

### Problem 4 — `findstr` unavailable in CMD

**What happened:** Verification commands written for PowerShell (`Select-String`)
were pasted into a CMD session. `Select-String` is a PowerShell cmdlet; CMD has no
equivalent built-in.

**Fix:** Used `findstr "pattern" file` for CMD, and `Get-Content file | Select-Object -Last N`
via `powershell "..."` inline call for multi-line output.

**Rule going forward:** Always check which shell is active before suggesting cmdlet
names. PowerShell uses `Select-String`, `Get-Content`, `Get-ChildItem`. CMD uses
`findstr`, `type`, `dir`. The terminal prompt prefix (`.venv) D:\...>` vs `PS D:\...>`)
is the tell.

### Problem 5 — Mute toggle UI stuck on "Muted" after unmuting

**What happened:** See Section 2.4 above for the full causal chain. After unmuting,
the badge stayed on "Muted" indefinitely. Pressing Alt+Space would snap it directly
to "listening", skipping "idle" — because `voiceState` was correctly "idle" but
`muted` was wrongly `true`, so `statusLabel` fell into the `muted ? "Muted"` branch.

**Fix:** Removed `setMuted((m) => !m)` from the `mute_toggle` event handler. One
line deleted. No new logic added.

---

## 4. Heads-up: downstream complications to watch

### `setup_windows.ps1` assumes Anaconda/system Python, not the venv

The script creates `.venv` using `python -m venv .venv` where `python` refers to
whatever is on `PATH` at the time the script runs. On this machine, `python` resolves
to Anaconda's Python 3.13.5. On a clean Windows machine with only the official Python
installer, it will resolve to `python.exe` from the installer.

**Potential issue:** If the system Python is 3.11 or older, some dependencies
(e.g. `type X | Y` union syntax in type hints) may fail. The script does not enforce
a minimum version.

**Mitigation when relevant:** Add a Python version check near the top of the script:
```powershell
$pyMajorMinor = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$pyMajorMinor -lt [version]"3.12") {
    Write-Host "[error] Python 3.12+ required (found $pyMajorMinor)" -ForegroundColor Red
    exit 1
}
```

### `muted` boolean in React is now effectively redundant

After the mute-toggle fix, `muted: boolean` in `App.tsx` is set correctly by
`state_changed` events but is no longer needed — `voiceState === "muted"` carries
the same information. The `statusLabel` logic checks `muted` as a fallback only when
`voiceState === "idle"`, which never happens when the orchestrator has sent a proper
`state_changed(state="muted")`.

The redundancy is harmless now but could cause confusion if a future developer sees
the `setMuted` calls in the `state_changed` handler and assumes `muted` is the
authoritative state. On Day 15 or Day 17 (window polish), consider removing `muted`
entirely and replacing all references with `voiceState === "muted"`.

### Per-turn `request_id` overwrites HTTP middleware `request_id` in shared log

`_run_pipeline` uses `logger.contextualize(request_id=turn_id)`. Voice turns don't
go through HTTP middleware, so the `request_id_var` ContextVar holds its default `"-"`
during a voice turn. This is correct behavior.

However, if a future code path triggers a voice turn from within an HTTP request
context (e.g. a `POST /chat` route that also triggers TTS), the `contextualize` call
will override the HTTP request's `request_id` in the log for the duration of the
`with` block. The HTTP middleware's `request_id` will be lost for those log lines.

**Mitigation when relevant:** If `POST /chat` is ever wired to trigger the
orchestrator, use a composite ID (e.g. `f"{http_rid}:{turn_id}"`) so both are
preserved. Not a concern for v1 where HTTP chat and voice turns are independent paths.

### TTS latency — LLM must complete before TTS starts (non-streaming)

Noted during regression: TTS begins only after the LLM has fully generated its
response. For a 2–3 sentence answer on Gemini Flash, this adds ~1–2 seconds of
silence between the user's question and the first spoken word.

The fix is streaming: buffer Gemini's output until a sentence boundary, then pipe
each sentence to Piper as it arrives. This is a real improvement but requires:
1. Switching `llm.generate()` to a streaming API call
2. Sentence segmentation (split on `.`, `!`, `?` with minimum length guard)
3. Overlapping Piper synthesis with LLM generation

**Complexity:** Medium. Target: Day 18–19 buffer, or Week 4 if pipeline is stable.
End-to-end latency would drop by ~1s for typical responses.

---

## 5. How to verify Day 14

```powershell
# 1. Fresh-clone simulation: move model files aside, run download script
Move-Item piper_voices\en_GB-alan-medium.onnx piper_voices\_bak.onnx
python scripts\download_models.py
# Expected: [dl] Alan model downloaded, size matches _bak.onnx
Move-Item piper_voices\_bak.onnx piper_voices\en_GB-alan-medium.onnx

# 2. Setup script parses cleanly (all sections skip on existing install)
.\scripts\setup_windows.ps1
# Expected: all [skip] lines, "=== Setup complete ===" at end

# 3. Per-turn request_id in logs
# Hold Alt+Space, ask one question, then:
Get-Content data\logs\jarvis.log | Select-Object -Last 30
# Expected: contiguous block of lines sharing one 12-char hex request_id
# covering recording save → STT → LLM → persist → TTS

# 4. Mute toggle round-trip
# Ctrl+Alt+J → badge: "muted"
# Ctrl+Alt+J → badge: "idle"  (was stuck before the fix)
# Alt+Space → badge: "listening" (not jumping directly from muted)

# 5. Release tag visible
git tag -l -n1 v0.2.0-voice-loop
# Expected: v0.2.0-voice-loop  Week 2 complete: PTT voice loop with Alan voice and JARVIS persona
```

All checks passed on 2026-05-26.

---

## 6. Open items before Day 15

- [ ] Upload `docs/media/week_2_demo.mp4` to YouTube/Loom and update the GitHub
      release notes with a real link
- [ ] `setup_windows.ps1` does not enforce Python 3.12+ minimum version — add a
      version check before Day 17 settings panel (which will also touch the script)
- [ ] TTS streaming — LLM must complete before TTS starts. Target Day 18–19 buffer
- [ ] `muted` boolean in `App.tsx` is now redundant — clean up on Day 15 or 17 when
      the blob component replaces the status badge logic

---

## 7. Files changed this day

```
NEW:
  scripts/download_models.py
  scripts/setup_windows.ps1
  docs/media/week_2_demo.mp4
  docs/plans/day_14_plan.md
  docs/project_status/PROJECT_STATUS(DAY_14).md

EDIT:
  backend/services/conversation.py    — _run_pipeline wrapped in
                                        logger.contextualize(request_id=turn_id)
  frontend/src/App.tsx                — mute_toggle handler made no-op (state_changed
                                        is the authoritative mute source)
  frontend/src/components/ChatPanel.tsx — assistant bubble bg-white/10 → bg-cyan-900/50
  docs/journal.md                     — Day 14 entry added
```

---

## 8. Commits

```
596052d  chore: package alan voice download + utf-8 console for fresh installs
a7d4051  chore: dev-time guards in conversation orchestrator
1266004  fix: mute-toggle UI state regression + assistant bubble style
7c7a11a  docs: Week 2 demo video + Day 14 plan
```
