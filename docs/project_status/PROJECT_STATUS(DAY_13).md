# Project Status — Day 13

**Period covered:** Day 13 (Voice Swap + JARVIS Personality)
**Status:** Complete — all tasks done. Commits `7f680fc`, `4336855`, `811eac6`.
**Environment:** Windows 11, Python 3.13.5, FastAPI, React 18 + Vite, Groq Whisper-large-v3, Piper `en_GB-alan-medium`, Gemini 2.5 Flash

> Checkpoint summary for Day 13: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 15.

---

## 1. What has been done

Day 13 repurposed the Week 2 buffer day for two high-leverage polish tasks: swapping
the Piper voice to a warmer British male register, and giving the assistant a stable
film-JARVIS personality via a modular system prompt architecture. Neither task touches
the voice loop state machine or breaks anything from Days 11–12.

| Task | What landed | Status |
|---|---|---|
| A.1–A.2 — Download Alan voice + verify sample rate | `en_GB-alan-medium.onnx` + `.onnx.json` downloaded to `piper_voices/`. Sidecar confirmed 22050 Hz — matches `tts_sample_rate` | Done |
| A.3 — Update settings | `piper_voice_path` in `settings.py` changed from `en_US-lessac-medium.onnx` to `en_GB-alan-medium.onnx`. Old files retained | Done |
| A.4–A.5 — Listen + regression | Alan voice verified: deeper register, mild British inflection, no chipmunk artifacts. Full PTT regression passed (STT, LLM, TTS, mute) | Done |
| B.2 — Prompts module scaffold | `backend/prompts/` directory created with `__init__.py` and `system/` subdirectory containing six `.md` files | Done |
| B.3 — `loader.py` | `load_system_prompt(directory)` auto-discovers `.md` files, sorts by filename prefix, strips and concatenates with `\n\n`. Graceful on missing dir/files | Done |
| B.4 — `system_prompts.py` | `JARVIS_SYSTEM_PROMPT` constant assembled at import time from the six files. Single import point for the rest of the codebase | Done |
| B.5 — Debug endpoint | `backend/api/debug.py` — `GET /debug/system-prompt` returns `{length_chars, prompt}`. Registered in `main.py`. Verified: 5337 chars, all six sections present | Done |
| B.6 — Wire into conversation | `_BASE_SYSTEM_PROMPT` constant removed from `conversation.py`. `JARVIS_SYSTEM_PROMPT` imported and substituted in `_build_system_prompt()`. No other changes to the file | Done |
| B.7 — Persona probes | All 7 probes passed: "What time is it?", "Tell me a joke", "Switch to kinase project", "I'm tired", context recall, T315I technical answer, multi-turn Ponatinib follow-up | Done |
| C.1–C.4 — Documentation | `project-architecture/SKILL.md` updated (prompts module + debug.py added, TTS entry updated). `voice-pipeline/SKILL.md` sample-rate gotcha reinforced with Day 13 evidence. `docs/journal.md` Day 13 entry. `docs/demo_script.md` drafted | Done |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. Modular prompt files over a single string

The previous `_BASE_SYSTEM_PROMPT` was a single Python string in `conversation.py`.
Editing tone or behaviour required a Python edit + backend restart + code review.

The six-file structure (`00_base_identity`, `10_personality`, `20_behavior`,
`30_domain_context`, `40_safety`, `90_examples`) separates concerns so that:

- **Tone** (personality, examples) and **capability** (domain context, safety) are
  independently editable without touching Python.
- Filename numeric prefixes define concatenation order from the directory listing —
  no need to read `loader.py` to understand the order.
- A new section (e.g. a per-tool persona for PDF summarisation) can be added as
  `50_pdf_mode.md` without renumbering anything else.
- Empty or missing files are skipped with a log warning — a typo in a filename
  doesn't crash the backend.

### 2. Import-time assembly, not per-request

`JARVIS_SYSTEM_PROMPT` is assembled when `system_prompts.py` is first imported (at
backend startup) and cached as a module-level constant. The I/O cost (six
`read_text()` calls) is paid once, not on every voice turn.

The trade-off: editing a `.md` file requires a backend restart to take effect. This
is acceptable because:
- Prompt iteration is a deliberate act (edit → restart → test), not something that
  should happen silently mid-session.
- Hot-reload would require a file watcher, adding complexity with no benefit for a
  single-user daily-driver.

The `GET /debug/system-prompt` endpoint compensates — you can always verify what
prompt the running process is actually using without reading six files.

### 3. B.6 was largely pre-done

Reading the existing code before implementation revealed that `base.py`,
`gemini.py`, `openai.py`, and `router.py` already had `system_prompt: str | None = None`
wired through (from Day 11's initial pipeline work). `conversation.py` already
called `self._llm.generate(..., system_prompt=system_prompt)`.

The only real change in B.6 was removing the `_BASE_SYSTEM_PROMPT` constant and
substituting `JARVIS_SYSTEM_PROMPT`. The lock topology, MUTED re-checks, and
`_persist_turn` ordering in `_run_pipeline` were untouched — exactly as the plan
required.

### 4. `90_examples.md` as the primary behaviour driver

The six files are not equal in influence. Gemini (and most instruction-tuned LLMs)
respond more reliably to concrete example exchanges than to abstract rules. The
`90_examples.md` file has the highest practical weight. When the persona drifts
in future iterations, the fix is to add or refine examples in that file, not to
add more rules to `20_behavior.md`.

### 5. Voice swap commit isolation

The voice swap (`settings.py` only) was committed separately from the personality
work. Reason: if the Alan voice ever needs to be reverted (chipmunk audio on a
different machine, user preference change), the revert is a single-file commit
cherry-pick that touches nothing in the prompts module. Mixing both changes in one
commit would make the revert messier.

---

## 3. Problems faced and how they were handled

### Problem 1 — `curl.exe` quoting failure on Windows terminal

**What happened:** The plan's verification command used single-quoted JSON:

```
curl.exe -X POST http://localhost:8000/speak -H "Content-Type: application/json" \
    -d '{"text": "A pleasure, sir..."}'
```

On Windows, single quotes are not string delimiters in CMD or PowerShell 5.1. The
shell passed the literal single-quote characters to `curl.exe`, which interpreted
`}` as a URL component. The result was a cascade of "unmatched close brace" and
"could not resolve host" errors for every word in the sentence.

**Fix:** Switched to `Invoke-RestMethod` with PowerShell's single-quoted string
syntax for the body (which IS a valid string delimiter in PowerShell, but not in
CMD):

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/speak" `
    -ContentType "application/json" `
    -Body '{"text": "A pleasure, sir. Shall I pull the latest papers on T315I resistance?"}'
```

**Rule going forward:** All API testing in this project uses `Invoke-RestMethod`,
not `curl.exe`. The quoting model is simpler and the output is already parsed as
a PowerShell object.

### Problem 2 — `piper_voices/` is gitignored; binary not committed

**What happened:** The git add for the `.onnx` and `.onnx.json` files was rejected
because `piper_voices/` appears in `.gitignore`. The plan anticipated this and said
to pick a path — commit or gitignore — and be consistent.

**Decision:** Keep the gitignore as-is. The 60 MB ONNX binary is too large for
routine commits, and the repo is intended to be public. The voice files are
downloaded once from Hugging Face and live in the local working tree only. The
`settings.py` change (which voice path to use) is committed; the binary is not.

**What's missing:** `scripts/download_models.py` does not yet include the Alan
voice download URLs. This is the open item — see Section 4.

### Problem 3 — PowerShell console displays em dashes as `â`

**What happened:** `Invoke-RestMethod` on the debug endpoint showed the assembled
prompt with `â` in place of every `—` (em dash). This appeared in the terminal
output when displaying `length_chars` and `prompt`.

**Root cause:** PowerShell 5.1's default console code page (CP1252 on most Windows
installs) cannot display UTF-8 multi-byte characters. The em dash (U+2014, encoded
as `E2 80 94` in UTF-8) is misinterpreted as CP1252 characters. This is a console
display artifact only.

**Verification:** The actual JSON payload, the Python string in memory, and the
content sent to Gemini's `system_instruction` are all correct UTF-8. Gemini
receives the proper em dash character. The issue is purely cosmetic at the
PowerShell terminal.

**Fix (if it bothers you):** `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`
at the start of a PowerShell session forces UTF-8 display. Not worth doing
permanently for a development tool; the data is correct.

---

## 4. Heads-up: downstream complications to watch

### `JARVIS_SYSTEM_PROMPT` has no hot-reload — backend restart required after every prompt edit

`JARVIS_SYSTEM_PROMPT` is assembled at import time and cached for the process
lifetime. Editing any file under `backend/prompts/system/` does NOT take effect
until the backend is restarted. This is easy to forget during prompt iteration:
you edit `90_examples.md`, send a probe, see no change, and assume the edit
didn't work — but the process is still running the cached version.

**Mitigation:** `GET /debug/system-prompt` lets you verify the running process's
assembled prompt in one curl call. Make this the first check whenever a prompt edit
seems to have no effect. The log also shows the prompt length at startup.

### Filename prefix ordering is load-bearing — renaming shifts everything

`load_system_prompt` sorts `.md` files lexicographically by filename. The `00_`,
`10_`, `20_` prefixes define concatenation order. If a file is renamed or its
prefix is changed, the order shifts. Shifting the order changes how Gemini weighs
the instructions (earlier sections get slightly more weight in long prompts).

**Rule:** When adding a new section, pick a prefix that slots into the existing
gap (e.g. `50_`, `60_`, `70_`, `80_`) rather than renumbering existing files.
Renumbering `90_examples.md` would be especially risky — it's the heaviest lifter
and its position at the end (after all rules) is intentional.

### `/debug/system-prompt` is unauthenticated

The endpoint is on `localhost:8000` with no auth. For a single-user, localhost-only
tool this is fine. If the backend is ever exposed beyond localhost (e.g. ngrok
tunnel for remote access, or a future multi-user version), this endpoint leaks the
full system prompt — including the domain context section which describes the user.

**Mitigation when relevant:** Gate behind `settings.debug_endpoints_enabled: bool = True`
and return HTTP 404 when disabled.

### `scripts/download_models.py` does not yet include the Alan voice

The Alan voice was downloaded manually during Day 13. `scripts/download_models.py`
was not updated. On a fresh machine or after a repo clone, the Alan voice will be
missing and the backend will fail at TTS init (Piper subprocess can't find the
file).

**Fix needed (Day 14 buffer or Day 17 settings panel):** Add the following to
`scripts/download_models.py`:

```
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json
```

Until then, anyone setting up from scratch needs to download these manually.

### Persona drift across long sessions or after Day 20 tool-calling

The JARVIS persona has been verified over 7 single-session probes. Two known
failure modes have not yet been stress-tested:

1. **Multi-turn drift:** Gemini may acknowledge the persona on early turns and
   revert to generic assistant patterns after 5–10 turns. Fix: add more multi-turn
   examples to `90_examples.md` (specifically exchanges where the second turn also
   shows JARVIS register, not just the first).

2. **Tool loop pollution:** From Day 20, tool calls insert intermediate
   `user/assistant` turns into the conversation context. These turns don't carry
   the persona — they're function call results. If the LLM starts treating tool
   result turns as the "style to imitate", the persona may degrade during
   tool-heavy sessions. Fix: if this is observed on Day 20+, add a section to
   `90_examples.md` showing a tool-call turn handled in JARVIS register.

---

## 5. How to verify Day 13

```powershell
# 1. Debug endpoint — all six sections present
Invoke-RestMethod -Uri "http://localhost:8000/debug/system-prompt" | Select-Object length_chars
# Expected: ~5337

# 2. Voice register — Alan vs Lessac
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/speak" `
    -ContentType "application/json" `
    -Body '{"text": "A pleasure, sir. Shall I pull the latest papers on T315I resistance?"}'
# Expected: deeper register, slight British inflection, no chipmunk pitch

# 3. Persona probe — no servile preamble
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/chat" `
    -ContentType "application/json" `
    -Body '{"message": "What time is it?"}' | Select-Object -ExpandProperty reply
# Expected: "[time], sir." — short, no "Certainly!" or "I'd be happy to"

# 4. Technical calibration
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/chat" `
    -ContentType "application/json" `
    -Body '{"message": "Tell me about T315I."}' | Select-Object -ExpandProperty reply
# Expected: concise gatekeeper-mutation answer + offered follow-up. No re-explanation of ABL1.

# 5. Voice loop regression
# Hold Alt+Space, say "What's the capital of France?", release.
# Expected: spoken "Paris" (or equivalent) within ~5s in Alan's voice.

# 6. Revert path works (verify without executing)
# One-line revert: change settings.py piper_voice_path back to en_US-lessac-medium.onnx
# Both voice files are present in piper_voices/ — no download needed.
```

All checks passed on 2026-05-25.

---

## 6. Open items before Day 15

- [ ] `scripts/download_models.py` — add Alan voice download URLs so fresh installs
      don't break at TTS init
- [ ] Consider adding `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` to
      `scripts/setup_windows.ps1` to prevent the em-dash display issue permanently
- [ ] Prompt iteration post-Day 20 — watch for persona drift during tool-call turns
      and add multi-turn examples to `90_examples.md` if observed

---

## 7. Files changed this day

```
NEW:
  backend/prompts/__init__.py
  backend/prompts/loader.py
  backend/prompts/system_prompts.py
  backend/prompts/system/00_base_identity.md
  backend/prompts/system/10_personality.md
  backend/prompts/system/20_behavior.md
  backend/prompts/system/30_domain_context.md
  backend/prompts/system/40_safety.md
  backend/prompts/system/90_examples.md
  backend/api/debug.py
  docs/demo_script.md
  docs/plans/day_13_plan.md

EDIT:
  backend/config/settings.py          — piper_voice_path: lessac → alan
  backend/main.py                     — import debug; app.include_router(debug.router)
  backend/services/conversation.py    — removed _BASE_SYSTEM_PROMPT constant;
                                        import JARVIS_SYSTEM_PROMPT; updated
                                        _build_system_prompt() to use it
  .claude/skills/project-architecture/SKILL.md
                                      — added backend/prompts/ module to folder
                                        structure; added debug.py to api/; updated
                                        TTS stack entry to alan-medium
  .claude/skills/voice-pipeline/SKILL.md
                                      — reinforced sample-rate gotcha with Day 13
                                        swap evidence (alan = 22050 Hz, confirmed)
  docs/journal.md                     — Day 13 one-liner added
```

---

## 8. Commits

```
7f680fc  feat: swap piper voice to en_GB-alan-medium for warmer JARVIS register
4336855  feat: JARVIS personality via modular system prompt
811eac6  docs: Day 13 notes, prompts module in architecture skill, demo script draft
```
