# Day 14 — Plan (Week 2 Buffer + `v0.2.0-voice-loop` Release)

**Period:** Day 14
**Type:** Buffer day 2 — close-out, harden, release
**Predecessor:** Day 13 (voice swap to Alan + JARVIS personality module) — all tasks done, three commits landed (`7f680fc`, `4336855`, `811eac6`)
**Successor:** Day 15 (SVG/CSS animated blob — Week 3 begins)

> Day 14 is not a feature day. It's the seam between Week 2 (voice pipeline) and
> Week 3 (visual presence). The job is to make the voice-loop foundation reliably
> shippable on a clean machine, capture proof it works, tag a release, and only
> then optionally lean into Week 3 prep. Don't add new features today.

---

## 1. Context — where we are

Per the v2 plan, Week 2's milestone test is:

> "I can hold Alt+Space, ask 'what's the capital of France?', and hear a spoken
> answer within 4 seconds."

That test passes today. The full voice loop (`LISTENING → TRANSCRIBING → THINKING
→ SPEAKING → IDLE` with mute, error recovery, project-scoped memory, Alan voice
and JARVIS persona) has been verified end-to-end on Days 11–13. Days 11 and 12
landed the orchestrator and audio-robustness work; Day 13 added polish.

**What's still loose** (drawn from Day 13 Section 6 + standing open items in
`voice-pipeline/SKILL.md`):

| # | Item | Source | Severity |
|---|---|---|---|
| 1 | Alan voice download URLs missing from `scripts/download_models.py` | Day 13 §6 | **Blocks fresh-clone setup** — needs fix today |
| 2 | UTF-8 console encoding not set in `scripts/setup_windows.ps1` | Day 13 §6 | Low — cosmetic, but a one-liner |
| 3 | `assert self._lock.locked()` not added to `_handle_error` | voice-pipeline skill | Low — dev-time only, no functional impact |
| 4 | Per-turn `request_id` binding missing in `_process_turn` | voice-pipeline skill | Low — log searchability |
| 5 | Week 2 demo video not recorded | Day 13/14 plan | **Blocks release tag** |
| 6 | `v0.2.0-voice-loop` git tag not pushed | v2 plan Day 14 | **The release marker for Week 2** |

Items 1, 5, 6 are non-negotiable for today. Items 2, 3, 4 are quick wins that
fit comfortably in the day. Anything else is Week 3 territory.

---

## 2. Goal of the day

By end of Day 14, all six items in the table above are closed, and a tagged
release `v0.2.0-voice-loop` exists on GitHub with an attached demo video link.
A fresh `git clone` on a clean Windows machine should reach a working voice loop
following only the README and `setup_windows.ps1`.

---

## 3. Tasks

Five blocks, sequenced. Blocks A–D are non-negotiable; E is the release; F is
optional Week 3 prep if time allows. The day has a natural rhythm: small fixes
first to warm up, then the regression run (which catches anything the small
fixes broke), then the demo, then the tag, then optional prep.

---

### Block A — Close out Day 13 open items (1–1.5h)

#### A.1 — Add Alan voice URLs to `scripts/download_models.py`

**Why:** The Alan voice was downloaded manually on Day 13. Anyone setting up the
repo from a fresh clone will hit a TTS-init failure because the Piper subprocess
can't find `en_GB-alan-medium.onnx`. Without this fix, `v0.2.0-voice-loop` is
not actually reproducible.

**Approach (per CLAUDE.md §1, §2):**

1. Open `scripts/download_models.py` and read it first. Note its existing
   structure — how Lessac is downloaded, what helper it uses, where files land.
2. Before asking Claude Code to write anything, write a comment block in the
   file describing what you want added:

   ```python
   # Add Alan voice download (Day 13 swap, en_GB-alan-medium, 22050 Hz).
   # Two files needed: the .onnx model and its .onnx.json sidecar.
   # Both go in piper_voices/ (gitignored). Lessac is retained as a fallback
   # voice — do not remove its download.
   ```
3. Ask Claude Code: "add the Alan voice download following the same pattern as
   Lessac." Read the diff carefully.

**URLs to use** (verified from Hugging Face on Day 13):

```
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json
```

**Verification:**

```powershell
# Move the existing Alan files aside (do NOT delete — fallback if download fails)
Move-Item piper_voices\en_GB-alan-medium.onnx piper_voices\_backup_alan.onnx
Move-Item piper_voices\en_GB-alan-medium.onnx.json piper_voices\_backup_alan.onnx.json

# Run the script
python scripts\download_models.py

# Confirm both files reappeared with the right sizes
Get-ChildItem piper_voices\en_GB-alan-medium*
```

Both files should match the sizes of the backup copies. Once verified, delete
the `_backup_*` files. If the download fails, restore the backups before
proceeding — the rest of the day depends on TTS working.

**Time:** 30 min.

**Watch out for:** The Hugging Face URLs use `/resolve/main/` (raw download),
not `/blob/main/` (HTML page). Easy typo. If the script downloads ~5 KB of
HTML instead of a 60 MB binary, that's the bug.

---

#### A.2 — Add UTF-8 console encoding to `scripts/setup_windows.ps1`

**Why:** PowerShell 5.1's default console code page is CP1252 on most Windows
installs. UTF-8 output (em dashes in the system prompt, any non-ASCII
characters in Gemini's responses) renders as `â` and friends. The data is
correct; only the display is wrong. Setting `[Console]::OutputEncoding` once
in the setup script saves anyone — including future-you on a new machine —
from staring at mojibake.

**Approach:**

1. Read `scripts/setup_windows.ps1`. Find the top of the script (after any
   `#Requires` or param block).
2. Add this line near the top, with a comment:

   ```powershell
   # PowerShell 5.1 defaults to CP1252; force UTF-8 so Gemini's em dashes,
   # smart quotes, and Unicode tool output render correctly in the console.
   [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
   ```
3. This is a one-liner; you can type it yourself per the CLAUDE.md §1 rule
   (re-type at least one line in your own keystrokes).

**Verification:**

```powershell
# Open a fresh PowerShell session, run the setup script, then test:
.\scripts\setup_windows.ps1
"em dash test: — — —"
# Expected: three real em dashes, not "â€" or "â".
```

**Time:** 10 min.

**Watch out for:** The setting only persists for the current PowerShell session
unless added to `$PROFILE`. The setup script setting it is the right scope —
it runs once during install, but anyone running the backend in a separate
PowerShell window won't have it. For a single-user dev tool this is fine.

---

#### A.3 — `git commit` Block A as a single chore commit

**Suggested message:**

```
chore: package alan voice download + utf-8 console for fresh installs

- scripts/download_models.py: add en_GB-alan-medium.onnx + .onnx.json
  downloads alongside the existing Lessac entries. Fresh clone now boots
  TTS without manual file copies.
- scripts/setup_windows.ps1: force [Console]::OutputEncoding = UTF8
  so the JARVIS system prompt's em dashes render correctly in PS 5.1.

Closes Day 13 §6 open items.
```

One commit, two small files. Don't fold Block A into the release tag commit —
keep history clean.

---

### Block B — Dev-time hardening (optional, 30–60 min)

These two items are flagged in `voice-pipeline/SKILL.md` as "worth adding when
next touching the orchestrator". Day 14 is a defensible time to do them —
they're additive (no behaviour change) and they make Day 20 tool-calling work
easier to debug. Skip this block if Block A took longer than 1.5h, or if you
don't feel like opening `conversation.py` today.

#### B.1 — Add `assert self._lock.locked()` to `_handle_error`

**Why:** `_handle_error` requires the lock to be held by the caller (it calls
`_transition` internally, which mutates state). Today this is enforced only by
docstring. A development-time assertion catches accidental misuse from any
future caller — including a future-you who forgot the contract by Day 25.

**Approach (per CLAUDE.md §5 — when uncertain, ask):**

1. Open `backend/services/conversation.py`, locate `_handle_error`.
2. Read the existing docstring — confirm it states the lock requirement.
3. Add this as the first line of the method body:

   ```python
   # Dev-time invariant: _handle_error must be called with self._lock held by
   # the caller. We mutate state via _transition below; the lock is what makes
   # that state mutation race-free with on_mute_toggle / on_ptt_start.
   assert self._lock.locked(), "_handle_error must be called with lock held"
   ```

**Decision point — ask before writing:** there are two valid styles for this
guard.

- **Plain `assert`** — runs in dev, stripped by `python -O`. Simpler.
- **`if not self._lock.locked(): raise RuntimeError(...)`** — runs in all
  modes. More defensive.

For a single-user daily-driver that is never run with `-O`, `assert` is fine.
Confirm with Claude Code which you prefer before adding.

**Verification:**

```powershell
# Run the existing Day 11 race-condition smoke test
python -m backend.tests.smoke_test  # (or whatever the actual path is)
# Expected: no AssertionError. Pipeline works exactly as before.
```

**Time:** 20 min.

---

#### B.2 — Per-turn `request_id` binding in `_process_turn`

**Why:** Currently each stage in a voice turn binds its own `request_id`
(`Path.stem` for save, then propagated through STT; `tts-{N}ch` for TTS).
Searching `data/logs/jarvis.log` for "everything that happened in this turn"
requires correlating multiple IDs. Binding one ID at the top of
`_process_turn` and using `logger.contextualize(request_id=...)` for the
whole pipeline makes each turn a single searchable unit.

**Approach:**

1. Open `_process_turn` in `conversation.py`.
2. Generate the per-turn ID from the WAV stem (it's already an ISO8601
   timestamp — naturally unique and human-readable).
3. Wrap the body of `_process_turn` in `with logger.contextualize(request_id=turn_id):`.
4. Remove the now-redundant individual `logger.bind(...)` calls inside the
   pipeline if any are made obsolete. Don't remove ones that still serve a
   purpose (e.g. the STT-internal binding may want to stay for failure
   isolation).

**Decision point — ask before writing:** the WAV stem is the natural choice,
but it's not generated until `_save_recording` runs. Two options:

- **(a)** Generate the ID in `_process_turn` (timestamp + short UUID), pass
  it into `_save_recording` so the filename matches.
- **(b)** Let `_save_recording` generate the WAV path first, then bind its
  stem as the request ID immediately after.

Option (b) is one line cleaner but ties the log ID to the filesystem path,
which is fine for now but couples the two. Option (a) is more decoupled but
needs a small refactor of `_save_recording`'s signature. **Ask before
implementing** — this is exactly the "non-trivial choice" case from CLAUDE.md §2.

**Verification:**

```powershell
# Hold Alt+Space, ask one question. Then:
Select-String -Path data\logs\jarvis.log -Pattern "request_id" | Select-Object -Last 20
# Expected: a contiguous block of log lines all sharing one request_id covering
# save → STT → LLM → persist → TTS for that one turn.
```

**Time:** 30–40 min.

---

#### B.3 — Commit Block B (if done)

**Suggested message:**

```
chore: dev-time guards in conversation orchestrator

- _handle_error now asserts self._lock.locked() — codifies the docstring
  contract that callers hold the lock across error broadcasting.
- _process_turn binds one request_id for the whole turn via
  logger.contextualize, replacing the per-stage bindings. Searching the
  log for a single turn is now one Select-String call.

No behaviour change; both items flagged in voice-pipeline/SKILL.md.
```

---

### Block C — Full regression via `demo_script.md` (30–45 min)

The `demo_script.md` was drafted on Day 13 (per the status doc's "Files
changed" section). Day 14 is the first time it gets run end-to-end after
Blocks A and B. This is the safety net before recording the demo video — if
something is broken, find out now, not on camera.

**Approach:**

1. Open `docs/demo_script.md`. Confirm it has the 10 prompts from the Day 28
   spec (or fewer, if Day 13 only drafted a Week 2 subset).
2. Run each prompt in order. For each, write next to it:
   - ✅ if it passed
   - ❌ if it failed, with a one-line note on what broke
3. The voice-loop subset (everything that doesn't require Week 4 tools):
   - "What time is it?" → expect "[time], sir." in Alan's voice
   - "Tell me a joke" → expect short, dry joke; no `Certainly!` preamble
   - "What's the capital of France?" → expect "Paris" (or short answer)
   - Multi-turn: "What about Germany?" immediately after → expect "Berlin"
     with context retained
   - "I'm tired" → expect calibrated empathy, no syrup
   - Hit Ctrl+Alt+J during silence → state goes MUTED, blob (if wired)
     reflects it. Hit again → IDLE.
   - Hit Ctrl+Alt+J **mid-recording** (hold Alt+Space, then hit mute) →
     recorder stops cleanly, no error spam
   - Hit Ctrl+Alt+J **mid-speaking** → TTS cuts off cleanly, no audio crackle

**Verification:** all 8 pass. If any fail, **stop and fix before continuing
to Block D.** Don't record a demo with known bugs.

**Time:** 30 min if everything works; up to 2h if something is broken (in
which case Block F gets dropped, not extended).

**Watch out for:**

- The 30-second max-duration silent buffer loss (gotcha #1 in
  `voice-pipeline/SKILL.md`) — if a test prompt accidentally goes >30s, the
  recording will silently vanish. Stay under 20s per held PTT.
- Importance scorer rate-limit failures are silent (per the same skill).
  After running 8 prompts, check `SELECT COUNT(*) FROM memory ORDER BY id
  DESC LIMIT 20;` — if memory rows are sparse compared to message rows, the
  scorer is being throttled. Not a release blocker (SQLite still has the
  messages), but note it in the journal entry.

---

### Block D — Week 2 demo video (45 min – 1h)

**Goal:** a 2–3 minute screen recording that proves Week 2 works. Not a
polished marketing piece — a checkpoint artifact. Goes in the README link for
`v0.2.0`.

**Approach:**

1. Tool: Windows + G (Xbox Game Bar), OBS, or Screencast-o-matic. Whichever
   you've used before. Don't burn time setting up a new recording tool today.
2. Script (no need to memorise — speak naturally):
   - Open the PyWebView window. Show it's transparent + always-on-top by
     dragging it over another window.
   - Hold Alt+Space, ask "what's the capital of France?", release. Hear
     answer.
   - Hold Alt+Space, ask "tell me about T315I", release. Hear answer.
     (Showcases technical vocab + persona register.)
   - Hold Alt+Space, ask follow-up "what inhibitors target it?", release.
     Hear answer with context retained.
   - Hit Ctrl+Alt+J, show muted state. Hit again, unmute.
   - Hold Alt+Space, speak for ~2 seconds, hit Ctrl+Alt+J mid-recording.
     Demonstrate the orchestrator's cancellation handling.
3. One take is fine. Edits: trim head/tail, nothing fancy.
4. Upload to YouTube unlisted (or Loom, or just commit as `docs/media/week_2_demo.mp4`
   if it's small enough — git LFS not needed for a 2-minute MP4 if compressed).

**Verification:** watch it back once. If the audio is OK and the voice loop
is visibly working, it's good. Don't re-record for polish issues — Day 30
gets the real demo.

**Time:** 45 min. **Hard cap at 1.5h.** This is a checkpoint video, not a
launch trailer.

**Watch out for:** PyWebView's transparent window may not record cleanly with
some screen recorders — the alpha channel becomes black or invisible. Test
with a 10-second throwaway recording first. If transparency captures poorly,
that's fine — record against a neutral desktop background and accept it.

---

### Block E — Tag `v0.2.0-voice-loop` (15 min)

The release marker for Week 2.

**Approach:**

1. Confirm everything is committed and pushed.
2. Update README to link the demo video.
3. One-line entry in `docs/journal.md` for Day 14.
4. Tag and push:

   ```powershell
   git tag -a v0.2.0-voice-loop -m "Week 2 complete: PTT voice loop with Alan voice and JARVIS persona"
   git push origin v0.2.0-voice-loop
   ```

5. On GitHub, edit the release notes — paste a short summary:

   ```
   ## v0.2.0 — voice-loop

   End-to-end PTT voice pipeline. Hold Alt+Space, speak, release, hear a
   spoken answer in JARVIS register within ~4 seconds.

   ### Highlights
   - 7-state voice loop (idle → listening → transcribing → thinking → speaking, plus muted/error)
   - Groq Whisper-large-v3 STT, Piper TTS (en_GB-alan-medium), Gemini 2.5 Flash LLM
   - Project-scoped SQLite + ChromaDB memory with LLM-scored importance
   - Modular JARVIS system prompt (six .md files, hot-swappable per restart)
   - PyWebView transparent always-on-top window with pynput global hotkeys
   - Mute hotkey (Ctrl+Alt+J) responsive in all states including mid-recording and mid-speaking

   ### Not yet
   - Animated blob (Week 3, Day 15)
   - Tool calling (Week 4, Day 20)
   - PDF / arxiv / web search / app launcher (Week 4)
   - Wake word (Day 27, optional)

   Demo: [link to video]
   ```

**Verification:** the tag appears at `https://github.com/<you>/research-jarvis/releases/tag/v0.2.0-voice-loop`.

**Time:** 15 min.

---

### Block F — Week 3 prep (optional, 30–60 min)

**Only if Blocks A–E finished well under budget and you have energy left.** This
is not a feature start — it's reading and sketching, so that Day 15 begins
with momentum instead of cold.

#### F.1 — Read `frontend-design/SKILL.md`

Located at `/mnt/skills/public/frontend-design/SKILL.md` (per the available
skills listing). The Week 3 deliverable is a single React component, but the
design choices (accent colour, animation curves, Framer Motion conventions)
are easier with the skill's guidance fresh in mind.

**Time:** 15 min.

#### F.2 — Sketch the six blob states on paper

Per Day 15 in the v2 plan, the blob has six visual states: `idle`, `listening`,
`thinking`, `speaking`, `muted`, `error`. Sketch each one on paper:

- Rough shape (round, lobed, irregular?)
- Colour relative to base (brighter, desaturated, red?)
- Animation feel (slow pulse, fast pulse, rotation, jitter?)
- Scale relative to idle (smaller, same, larger?)

This is design work, not coding work. The point is to walk into Day 15 with
opinions, not a blank page.

**Time:** 20 min.

#### F.3 — Pick the accent colour

The v2 plan suggests "soft cyan" but leaves it open. Decide today and write
it as a hex value in `docs/journal.md` so Day 15 doesn't burn 30 minutes on
colour-picking. Some candidates:

- `#7DD3FC` — soft cyan, the plan's default suggestion
- `#A78BFA` — lavender, easier on the eyes against varied desktop backgrounds
- `#34D399` — mint, JARVIS-ish in a different register
- `#F0ABFC` — soft pink, unconventional

**Decision is yours.** This is one of the things you'd want to actually like
looking at for 4+ hours a day.

**Time:** 10 min.

#### F.4 — Do NOT start writing `Blob.tsx`

Tempting after F.1–F.3, but per CLAUDE.md §1 — no features outside the day's
plan, and Week 3 in the v2 plan is a 5-day stretch (Day 15–19) that wants its
full warmth-up Day 15 morning. Stop at sketches and colour. Day 15 starts
fresh.

---

## 4. Decision points (call these out before doing them)

Per CLAUDE.md §2 — these are the non-trivial choices in today's plan. Surface
them to Claude Code before writing code, not after.

| # | Decision | Default | Alternative |
|---|---|---|---|
| 1 | Block B.1 assertion style | `assert` (dev-time) | `if not ... raise RuntimeError` (always-on) |
| 2 | Block B.2 turn-ID generation | Generate in `_process_turn`, pass into `_save_recording` | Let `_save_recording` generate the path first, bind its stem |
| 3 | Block B do at all? | Yes, additive low-risk | Skip, defer to Day 20 when next touching orchestrator |
| 4 | Block F do at all? | Only if A–E under budget | Skip entirely, walk away from the laptop |
| 5 | Demo video hosting | YouTube unlisted | Loom / GitHub-attached MP4 / Google Drive |
| 6 | Block F.3 accent colour | Open — pick one you'll like seeing | n/a |

---

## 5. Completion criteria

**Non-negotiable (Blocks A, C, D, E):**

- [ ] Alan voice URLs in `scripts/download_models.py`; fresh-clone setup verified end-to-end
- [ ] `[Console]::OutputEncoding = UTF8` in `scripts/setup_windows.ps1`
- [ ] Block A committed as a single `chore:` commit
- [ ] All 8 voice-loop prompts in `demo_script.md` pass
- [ ] Week 2 demo video recorded (2–3 min), linked or committed
- [ ] `v0.2.0-voice-loop` tag pushed to GitHub with release notes
- [ ] `docs/journal.md` updated with Day 14 one-liner

**Optional (Block B):**

- [ ] `_handle_error` has `assert self._lock.locked()` guard
- [ ] `_process_turn` binds one `request_id` for the whole turn via `logger.contextualize`
- [ ] Block B committed as a single `chore:` commit
- [ ] Voice-loop regression re-run after B — all 8 still pass

**Optional (Block F):**

- [ ] `frontend-design/SKILL.md` read
- [ ] Six blob states sketched on paper
- [ ] Accent colour hex written in `docs/journal.md`

---

## 6. Watch out for

- **Don't widen scope.** This is a buffer day. If you find a bug in Block C
  that takes >1h to fix, the bug doesn't get fixed today — it goes into a
  GitHub issue and gets handled on Day 18–19 (the next buffer window). The
  release tag matters more than perfection.
- **Don't refactor unrelated code in Blocks A or B.** Minimal diffs per
  CLAUDE.md §3. The two `scripts/` files are tiny — touch only what's needed.
  `conversation.py` is a sensitive file — touch only the two named spots in
  Block B.
- **Don't skip the regression run** (Block C) even if you "know" Blocks A and B
  didn't touch the voice loop. Block A touched the setup script which the
  fresh-clone path depends on; Block B touched the orchestrator. Trust nothing.
- **Don't re-record the demo for polish.** Day 30 has a dedicated polished
  demo. Today's is proof-of-life.
- **Don't push the tag before the video link is in the release notes.** Tags
  are immutable in spirit; if you push and then add the video, the GitHub
  release notes auto-rebuild but the commit history doesn't reflect it. Have
  the video ready first, then tag and write notes in one go.
- **`piper_voices/` is gitignored** (per Day 13 Problem 2). Block A.1 changes
  the *script* that downloads the files, not the files themselves. The
  60 MB ONNX still doesn't enter the repo.

---

## 7. Time budget

| Block | Min | Max | Notes |
|---|---|---|---|
| A — Day 13 close-out | 45 min | 1.5 h | Block-A only commit |
| B — Dev hardening | 0 min | 1 h | Optional |
| C — Regression | 30 min | 1 h | Stops the day if a bug is found |
| D — Demo video | 45 min | 1 h | Hard cap at 1.5 h |
| E — Release tag | 15 min | 20 min | |
| F — Week 3 prep | 0 min | 1 h | Optional |

**Realistic total:** 3–4 hours of focused work for the mandatory blocks
(A + C + D + E). With Blocks B and F, 5–6 hours. Either is a fine day.

**If you hit 6 hours and Block E isn't done yet, descope:** skip B, skip F,
get the tag pushed before stopping. The release is what makes Day 14 count;
everything else is supporting work.

---

## 8. End-of-day checklist (Daily Discipline reference from v2 plan)

- [ ] All commits pushed to `main`
- [ ] `docs/journal.md` Day 14 entry written (one line is enough)
- [ ] `v0.2.0-voice-loop` visible on GitHub
- [ ] README links the demo video
- [ ] Glance at Day 15 plan — note: tomorrow opens `frontend-design/SKILL.md`
      and starts on `Blob.tsx`. Block F today makes that smooth.
- [ ] Honest assessment in journal: ahead / on-track / behind?

---

## 9. What this day is NOT

- Not a Day 12 redo. The 30-second silent-buffer-loss gotcha is real, but it's
  a Day 12-scope item and the Day 11–12 status doc has not yet been read into
  today's plan. If a fix is wanted, it's a Day 18–19 buffer item, not today.
- Not the start of Week 3. Block F is *prep* — reading and sketching — not
  the first commit of `Blob.tsx`. Day 15 is for that.
- Not for adding any tool, any new voice, any new prompt section, any new
  endpoint. Per CLAUDE.md §7: no features outside the v2 plan, and Day 14 in
  the v2 plan is explicitly buffer + release.

---

## 10. End-of-week-2 single-sentence test (from v2 plan)

> "I can hold Alt+Space, ask 'what's the capital of France?', and hear a
> spoken answer within 4 seconds."

If this is still true at the end of Day 14, Week 2 is locked in. Tomorrow we
make it look alive.
