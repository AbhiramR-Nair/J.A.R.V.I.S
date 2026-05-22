# Claude Code Instructions — research-jarvis

> This file is automatically read by Claude Code on every session. It defines how I want you to work with me on this project. Read carefully and follow.

## Project context

**research-jarvis** is a voice-first AI research and productivity assistant for Windows. It runs as a floating, always-on-top desktop overlay. Single user (me). Hybrid architecture: local for always-on tasks (hotkey listening, TTS playback, UI), cloud for heavy work (STT via Groq, LLM via Gemini, web search via Tavily).

This is a personal daily-driver tool AND a portfolio piece for computational drug discovery / bioinformatics roles. Code quality matters; my comprehension of the code matters more.

**Full architecture and folder structure:** see `.claude/skills/project-architecture/SKILL.md`.

## About me (the user)

I'm an Integrated MSc Biochemistry student with strong applied ML experience in computational biology (kinase resistance prediction, protein stability, DTI pipelines). I can read Python and modify existing code, but I do not write code fluently from a blank file. **I am explicitly using this project to build that skill while shipping the product.**

This means how you help me matters as much as what you produce. Please respect the working style below.

## Working style — non-negotiable

### 1. Explain as you build

For every non-trivial code block you write, **add a short explanation comment block above it** (3-5 lines max) describing what it does and why. Example:

```python
# This decorator registers the function as a tool that Gemini can call.
# JSON Schema in `parameters` tells Gemini what args to pass.
# Async because all tool handlers run in the FastAPI event loop.
@registry.register(
    name="get_current_time",
    description="...",
    parameters={...}
)
async def get_current_time() -> str:
    return datetime.now().isoformat()
```

These comments are for my future re-reading. Do not strip them in later edits. Don't comment every line — only blocks where the *why* isn't obvious.

### 2. Suggest, don't just write

For any non-trivial choice (data structure, library, architecture, error-handling approach), tell me the options and trade-offs *before* writing the code. Example:

> "For storing per-tool config, I see two options: (a) one YAML file per tool, simpler to edit; (b) single `tools.yaml` with all configs, easier to atomically update. (a) is better if I plan to add many tools; (b) is fine for the current ~6 tools. Which do you prefer?"

Don't ask about trivial choices (variable names, formatting). Do ask about anything that's hard to reverse later.

### 3. Minimal diffs

When modifying existing files, change only what's necessary. Don't reformat unrelated lines, don't refactor unless I asked. Small focused diffs are easier for me to review and understand.

### 4. Verify versions before suggesting code

Before suggesting code that uses a library API, **check the actual installed version** in `requirements.txt` or `package.json`. SDKs change. Gemini's Python SDK in particular has shifted API shape multiple times. If you're not sure of the current API, say so and ask me to check the docs, or read the installed package version yourself.

### 5. When uncertain, ask

If I give an ambiguous request or there are multiple reasonable interpretations, ask before writing 200 lines I'll have to delete. Examples of good clarifying questions:

- "When you say 'add the timer tool,' should it persist across restarts or be in-memory only?"
- "For the PDF drop handler, should it auto-summarize on drop or wait for a voice command?"

### 6. Stay in the read-review loop

After writing code, briefly tell me what to test to verify it works. Example: "Try `python -m backend.tests.smoke_test` — should print 'OK'. Then in the React app, click 'Send' and confirm a response appears." This keeps me in the loop instead of accepting blind.

### 7. Hard rules (never violate)

- **NEVER suggest local LLMs in v1.** Cloud only (Gemini, OpenAI fallback). I know about Ollama. It's not for now.
- **NEVER add a feature not in `Day_by_Day_Plan_v2.md` without asking.** Scope creep kills this project.
- **ALL memory operations must be project-scoped** — every read/write to SQLite memory or ChromaDB takes a `project_id`.
- **ALL external API calls need graceful error handling** — if Groq/Gemini/Tavily fail, the app shows a clear message, doesn't crash, doesn't silently swallow errors.
- **NEVER use Tauri or Three.js.** This project uses **PyWebView** and **SVG/CSS animations** by deliberate choice. If you find tutorials suggesting Tauri or Three.js for similar problems, ignore them.
- **NEVER store sensitive data unencrypted** beyond `.env`. API keys go in `.env`, period.

### 8. Tech stack — locked, do not propose changes

- Python 3.12, FastAPI, Pydantic v2, SQLite, ChromaDB, loguru
- PyWebView for desktop shell, pynput for global hotkeys
- React + Vite + TypeScript + Tailwind for frontend (inside PyWebView)
- Framer Motion for animations (no Three.js, no shaders)
- Voice: sounddevice (audio I/O), Groq Whisper-large-v3 (STT), Piper (TTS)
- LLM: Gemini primary, OpenAI fallback. Native function calling
- Tools: Tavily (web search), pymupdf (PDF), arxiv (arxiv lookup), plyer (notifications), subprocess (app launching)
- Wake word (Week 4 optional): openWakeWord

If I ask you to use something not on this list, gently remind me of the locked stack first.

## Codebase conventions

- **Naming:** `snake_case.py` files, `PascalCase` classes, `snake_case` functions
- **Type hints everywhere.** Even on internal helpers. Pydantic models for all API I/O
- **Async-first.** FastAPI, voice loop, all external API calls
- **Logging:** loguru, with request IDs flowing through related operations
- **Error handling:** try/except around every external call (network, file, subprocess); user-facing errors should be human-readable, not raw stack traces
- **Tests:** lightweight. No pytest-everything. Just the `demo_script.md` manual checklist (Day 28) and small smoke tests where critical
- **Imports:** standard library → third-party → local, with blank lines between
- **No magic numbers.** Hard-coded values go in `backend/config/settings.py`

## File organization rules

- No loose `.py` files in repo root
- No `jarvis_v2.py`, `working_final.py`, `voice_test_old.py`. If I create files like this, please tell me to delete them
- Runtime data goes in `data/` (gitignored)
- Tests in `backend/tests/`
- Scripts in `scripts/`
- Skills you write or update go in `.claude/skills/<name>/SKILL.md`

## When I'm stuck (and I will be)

If I paste an error message and ask for help, **do not immediately rewrite the code.** Instead:

1. Tell me what the error actually means in plain English
2. List 2-3 likely causes, ordered by probability
3. Ask me to run a specific diagnostic (print a value, check a config, test a smaller case)
4. Once we know the cause, *then* propose a fix

This is how I learn to debug. If you skip to "here's the fix," I won't.

## When I'm vibe-coding (please call me out)

If I ask you to write something and you sense I haven't read the previous code carefully (e.g., I'm asking for changes to a function I shouldn't have forgotten), gently ask: "Do you want me to walk through the existing code first?" before writing more.

Better to slow down for 5 minutes than to add another layer of code I don't understand.

## Memory of past sessions

Anthropic's Claude Code may have memory of past sessions. If I reference past work ("the tool registry we built," "the audio bug from yesterday"), trust that reference. If you genuinely don't have that context, ask me to summarize briefly rather than guessing.

## When this file is wrong

If a rule here ever conflicts with what I'm actually telling you in the current session, the current session wins — but flag it. Example: "You said in CLAUDE.md to never suggest Tauri, but you're now asking about Tauri. Want to override that rule for this conversation?"

Same for skill files — if a SKILL.md says X but I'm asking for Y, ask before assuming the skill is stale.

---

**Bottom line:** I want to ship Jarvis in 4 weeks AND have it be code I can read, debug, and explain. Help me hit both goals. The how matters as much as the what.
