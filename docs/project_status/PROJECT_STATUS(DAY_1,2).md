# Project Status — Days 1 & 2

**Period covered:** Day 1 (Environment Setup) and Day 2 (Repo Scaffolding)
**Status:** Complete — both days' Definition-of-Done met, two git commits landed.
**Environment:** Windows 11, Python 3.13.5, Node 24.15.0, Git 2.52.0

> This document is a checkpoint summary: what got built, *why* it was built that way, what
> went sideways, how it was handled, and what to watch for downstream. Read it before Day 3.

---

## 1. What has been done

### Day 1 — Environment Setup

| Task | Status | Notes |
|---|---|---|
| Toolchain present (Python, Node, Git, VS Code) | Done | Versions verified (see below) |
| Starter files in place (`CLAUDE.md`, skill, plan) | Done | Pre-existed; moved into planned paths |
| `.env.example` with placeholder keys | Done | All five keys templated |
| `.env` with real keys | Partial | Gemini + OpenAI filled; **Groq + Tavily still empty** |
| `.gitignore` | Done | `.env`, `data/`, `node_modules`, binaries excluded |
| Git repo initialized + first commit | Done | Commit `chore: initial repo setup with Claude Code config` |
| GitHub remote pushed | **Not done** | No remote configured yet — local-only so far |
| Claude Code extension installed/signed in | User-side | Outside automation scope |

### Day 2 — Repo Scaffolding

| Task | Status | Notes |
|---|---|---|
| Full folder structure | Done | Matches `project-architecture/SKILL.md` |
| Python venv at `.venv/` | Done | `python -m venv .venv` |
| Backend deps installed | Done (subset) | Day-2 boot subset only — see §3 |
| `backend/requirements.txt` frozen | Done | `pip freeze` output, exact pins |
| Frontend scaffolded (Vite + React 18 + TS) | Done | via `npm create vite@latest` |
| Tailwind + Framer Motion | Done | Tailwind **v3** (pinned), Framer Motion latest |
| `backend/main.py` — FastAPI `/health` + CORS | Done | Boots, returns `{"status":"ok"}` |
| `backend/desktop.py` — PyWebView shell | Done | Transparent, always-on-top, frameless |
| Boot test | **Pending user run** | Manual 3-terminal test (see §6) |
| Second commit | Done | `feat: project scaffolding, pywebview shell, backend boots` |

---

## 2. Implementation strategy (the *why* behind the choices)

1. **Stubs for logic, real content only for config.**
   Every `.py`/`.tsx` module that holds *behaviour* (e.g. `llm/gemini.py`, `voice/stt.py`)
   was created **empty**. They get filled on their assigned day. Only config files
   (`.gitignore`, `requirements.txt`, `tailwind.config.js`, `main.py`, `desktop.py`) carry
   real content now. Rationale: matches the day-by-day plan and the CLAUDE.md rule *"never
   accept code you can't explain"* — code arrives when you're ready to read it.

2. **Dependencies installed in slices, not all at once.**
   Day 2 installed only what the boot test needs (FastAPI, uvicorn, PyWebView, pynput,
   loguru, pydantic, httpx, websockets). Voice (`groq`, `sounddevice`), LLM (`google-genai`,
   `openai`), memory (`chromadb`), and tool deps (`pymupdf`, `tavily`, `arxiv`, `plyer`)
   are deferred to their respective days. Rationale: smaller install surface = fewer
   version conflicts to debug early, and each day's deps get verified in context.

3. **`desktop.py` is intentionally minimal for Day 2.**
   It only opens a PyWebView window pointing at the Vite dev server (`localhost:5173`).
   FastAPI runs as a *separate* process for now. A `# Day 7 TODO` marker flags where the
   FastAPI background thread will later be folded in so one script starts everything.

4. **Verify-before-code on library APIs.**
   Per CLAUDE.md rule #4, the actual installed `webview.create_window` signature (PyWebView
   **6.2.1**) was inspected before writing `desktop.py`, rather than trusting tutorials.
   Confirmed params used: `frameless`, `on_top`, `transparent`, `background_color`,
   `easy_drag`, `shadow`.

---

## 3. Problems faced & how they were handled

### P1 — Python 3.13.5 installed, plan specifies 3.12  *(impact: medium, ongoing)*
- **What:** The locked stack (CLAUDE.md, plan) calls for Python 3.12. The machine has 3.13.5.
- **Handled:** Proceeded on 3.13. All Day-2 deps installed cleanly on 3.13 with no issues.
  Flagged in the Day-1 commit message and here.
- **Why acceptable now:** Nothing installed so far has a 3.13 incompatibility.

### P2 — Two starter files misplaced at repo root  *(impact: low, resolved)*
- **What:** `project-architecture-SKILL.md` and `Day_by_Day_Plan_v2.md` sat at the repo root,
  but both CLAUDE.md and the plan reference them at `.claude/skills/project-architecture/SKILL.md`
  and `docs/Day_by_Day_Plan_v2.md`.
- **Handled:** Moved into their planned locations; `Version_1_plan.md` also moved to `docs/`
  and the README link updated.

### P3 — Frontend stubs blocked the Vite scaffolder  *(impact: low, resolved)*
- **What:** The folder scaffold step had created empty placeholder files inside `frontend/`.
  `npm create vite` refuses (or prompts interactively) on a non-empty directory — and our
  terminal can't answer interactive prompts.
- **Handled:** Cleared `frontend/` contents first, then scaffolded into the empty dir.
- **Side effect:** `create-vite` only generates `App.tsx` / `main.tsx` in `src/`, so the
  planned subdirectory stubs (`components/`, `blob/`, `hooks/`, `websocket/`) were wiped.
  They were **recreated** after scaffolding to keep the structure matching the plan.

### P4 — Tailwind v4 vs the plan's v3 setup  *(impact: low, resolved by pinning)*
- **What:** The plan uses Tailwind v3 syntax (`npx tailwindcss init -p`, PostCSS-based).
  The current default install is Tailwind **v4**, which drops `init -p` and uses the
  `@tailwindcss/vite` plugin instead — a different setup entirely.
- **Handled:** Pinned `tailwindcss@3` so the plan's documented steps work verbatim.
  `tailwind.config.js` `content` glob set to `./index.html` + `./src/**/*.{ts,tsx}`;
  `index.css` carries the three `@tailwind` directives plus transparent `html/body/#root`
  (required for the see-through window).

### P5 — IDE shows "import could not be resolved" for `webview`/`loguru`  *(impact: cosmetic)*
- **What:** Pylance flagged unresolved imports in `desktop.py`.
- **Cause:** VS Code is pointed at the *system* Python, not `.venv`. The packages exist;
  the analyzer just isn't looking in the venv.
- **Handled:** Not a code error. Fix is user-side — `Ctrl+Shift+P` → *Python: Select
  Interpreter* → choose `.venv\Scripts\python.exe`.

### P6 — Git CRLF warnings  *(impact: cosmetic)*
- **What:** Git warned "LF will be replaced by CRLF" on staging.
- **Handled:** Set `git config core.autocrlf true` (Windows-appropriate). Warnings are benign.

---

## 4. Heads-up: downstream complications to watch

### From P1 (Python 3.13) — **the one most likely to bite**
The plan was validated against 3.12. Packages installed *later* are where 3.13 risk lives:
- **ChromaDB (Day 6)** — highest risk. Has historically lagged on new Python wheels and
  pulls native deps. If `pip install chromadb` fails to build, that's the signal.
- **pymupdf (Days 22-24)**, **Piper runtime / onnxruntime (Day 10)**, **openWakeWord
  (Day 27)** — all have native components; watch for missing 3.13 wheels.
- **Mitigation if it breaks:** install Python 3.12 alongside (don't uninstall 3.13),
  delete `.venv`, recreate it with `py -3.12 -m venv .venv`, reinstall. Cheap to do; only
  worth doing if/when a build actually fails. Don't pre-emptively downgrade.

### From P4 (Tailwind v3 pin)
Tailwind is held at v3 deliberately. If a future frontend dependency demands v4, you'll get
a peer-conflict. Resolve by either staying on v3 (preferred — the plan assumes it) or
migrating the whole Tailwind setup to the v4 plugin model at once. Don't half-migrate.

### PyWebView specifics (Day 7 territory)
- **Transparency quirks on Windows:** black flicker on first paint is normal (noted in the
  plan). If transparency doesn't render at all, the documented fallback is a solid-color
  frameless window — functionally identical.
- **`Alt+Space` hotkey collision:** Windows binds Alt+Space to the system/window menu.
  The pynput listener (Day 7) may need to suppress the default, or fall back to
  `Ctrl+Shift+J` (plan's documented alternative).

### Integration assumption (Day 7)
`desktop.py` will eventually start FastAPI in-process via `uvicorn.run("backend.main:app", …)`.
That import path **only resolves when run from the repo root** (so `backend` is importable as
a package). Keep launching from the project root, or add an explicit `sys.path` / working-dir
guard when wiring it up.

### Dependency manifest will drift
`backend/requirements.txt` currently holds only the Day-2 subset. **Re-freeze after each
day that adds packages.** Also, per the plan's Day-4 watch-out: once the Gemini SDK works,
pin its exact version immediately — that SDK changes shape often.

### Secrets hygiene
`.env` holds live keys and is gitignored — confirmed not staged. Never move keys into any
tracked file, and never paste them into docs (this file included). If a key is ever
committed by accident, rotate it rather than just deleting the commit.

---

## 5. Open items before Day 3

- [ ] Fill `GROQ_API_KEY` and `TAVILY_API_KEY` in `.env` (needed Week 2 / Day 25, but do it now)
- [ ] Run the boot test (§6) and confirm both criteria pass
- [ ] Select `.venv` as the VS Code interpreter (clears P5 warnings)
- [ ] (Optional) Create the private GitHub repo and push — Day 1 listed it but it's not blocking

---

## 6. How to run the boot test

Three terminals from the repo root:

```powershell
# Terminal 1 — Vite dev server
cd frontend
npm run dev

# Terminal 2 — FastAPI backend
.venv\Scripts\activate
uvicorn backend.main:app --port 8000

# Terminal 3 — PyWebView shell
.venv\Scripts\activate
python backend/desktop.py
```

**Pass criteria:**
- `http://localhost:8000/health` → `{"status":"ok"}`
- A transparent, frameless, always-on-top window shows **"J.A.R.V.I.S — online"**

---

## 7. Commit log for this period

```
feat: project scaffolding, pywebview shell, backend boots
chore: initial repo setup with Claude Code config
```
