# research-jarvis

A voice-first, project-aware AI research and productivity assistant for Windows. Runs as a
floating, always-on-top desktop overlay. Hybrid architecture: local for always-on tasks
(hotkey listening, TTS playback, UI), cloud for heavy work (STT via Groq, LLM via Gemini,
web search via Tavily).

> Status: scaffolding. See [docs/Version_1_plan.md](docs/Version_1_plan.md) for the full plan and
> [docs/Day_by_Day_Plan_v2.md](docs/Day_by_Day_Plan_v2.md) for the daily breakdown.

## Stack

- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLite, ChromaDB, loguru
- **Desktop shell:** PyWebView + pynput global hotkeys
- **Frontend:** React + Vite + TypeScript + Tailwind, Framer Motion (SVG/CSS blob)
- **Voice:** sounddevice, Groq Whisper-large-v3 (STT), Piper (TTS)
- **LLM:** Gemini primary, OpenAI fallback (native function calling)

## Setup

```powershell
# 1. Backend deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt

# 2. Frontend (Day 2 — scaffolds React via Vite)
cd frontend
npm install

# 3. Secrets
copy .env.example .env   # then fill in API keys
```

See [docs/setup.md](docs/setup.md) for full setup notes.
