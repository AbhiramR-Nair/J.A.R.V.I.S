# Skill: Project Architecture (research-jarvis)

## When this applies

Whenever you're working on this project and need to know:
- Where a new file should go
- How components communicate
- What tech stack is locked
- What the high-level data flow looks like
- Naming conventions
- The hybrid local/cloud philosophy

Read this skill before adding any new feature, file, or module.

## One-line description

A voice-first, project-aware AI assistant running as a floating Windows overlay. Python backend (FastAPI + memory + voice + tools) drives a React frontend hosted inside PyWebView. Cloud APIs do the heavy lifting; the i3 laptop handles only always-on local tasks.

## High-level data flow

```
                  ┌──────────────────────────────────────────┐
                  │  PyWebView Window (transparent overlay)  │
                  │  ┌────────────────────────────────────┐  │
                  │  │  React app (Vite dev or built)     │  │
                  │  │  - Animated SVG blob               │  │
                  │  │  - Chat panel                      │  │
                  │  │  - Settings                        │  │
                  │  └─────────────┬──────────────────────┘  │
                  └────────────────│─────────────────────────┘
                                   │ WebSocket + HTTP
                                   ▼
                  ┌──────────────────────────────────────────┐
                  │  FastAPI backend (localhost:8000)        │
                  │                                          │
                  │  ┌─ services/conversation.py             │
                  │  │  Orchestrates: state machine,         │
                  │  │  voice loop, LLM + tool calls         │
                  │  │                                       │
                  │  ├─ voice/ ─ wake_word, stt, tts, audio  │
                  │  ├─ llm/ ─── gemini, openai, router      │
                  │  ├─ memory/ ─ sqlite_store, vector_store │
                  │  ├─ tools/ ── registry + individual tools│
                  │  └─ database/ jarvis.db (SQLite)         │
                  │                + data/chroma/ (Chroma)   │
                  └──────────────┬───────────────────────────┘
                                 │
                                 │ External APIs:
                                 │ - Groq (STT)
                                 │ - Gemini (LLM + embeddings + grounding)
                                 │ - Tavily (web search)
                                 │ - OpenAI (fallback)
                                 ▼
                          (cloud services)

                  ┌──────────────────────────────────────────┐
                  │  pynput global hotkey listener           │
                  │  Alt+Space  → ptt_start / ptt_end events │
                  │  Ctrl+Alt+J → mute_toggle event          │
                  │  (Sends events via WebSocket to React)   │
                  └──────────────────────────────────────────┘
```

## Voice loop state machine

```
idle ──(wake_word OR ptt_start)──> listening
listening ──(silence OR ptt_end)──> transcribing
transcribing ──(stt done)──> thinking
thinking ──(llm + tools done)──> speaking
speaking ──(tts done)──> idle

muted ──(ctrl+alt+j)──> idle
idle ──(ctrl+alt+j)──> muted

any state ──(error)──> error ──(3s timeout)──> idle
```

The current state is broadcast over WebSocket to the React frontend on every transition. Blob visual state mirrors this.

## Hybrid architecture philosophy

| Concern | Local | Cloud | Why |
|---|---|---|---|
| Wake word (when added) | ✓ | | Always-on; can't be cloud-bound |
| Hotkey detection | ✓ | | OS-level |
| TTS playback | ✓ (Piper) | | Low latency, free, sounds good |
| UI rendering | ✓ | | Obviously |
| STT | | ✓ (Groq) | Quality + speed; local Whisper is too slow on i3 |
| LLM | | ✓ (Gemini) | Local LLM not feasible on i3 |
| Web search | | ✓ (Tavily) | Same |
| Embeddings | | ✓ (Gemini) | Free tier covers it |
| Vector storage | ✓ (Chroma) | | Single user, local data is fine |
| Relational storage | ✓ (SQLite) | | Same |

If any rule above is challenged, default to keeping it as-is. Reasons it was chosen this way are deliberate.

## Folder structure (canonical)

```
research-jarvis/
│
├── CLAUDE.md                       # Instructions for Claude Code
├── README.md                       # Public-facing
├── .env                            # Real API keys (gitignored)
├── .env.example                    # Template
├── .gitignore
├── pyproject.toml
│
├── .claude/
│   └── skills/
│       ├── project-architecture/SKILL.md   # This file
│       ├── tool-calling-pattern/SKILL.md   # Added Day 20
│       └── (more added as built)
│
├── backend/
│   ├── main.py                     # FastAPI entry
│   ├── desktop.py                  # PyWebView launcher
│   ├── requirements.txt
│   │
│   ├── api/                        # FastAPI routes
│   │   ├── chat.py
│   │   ├── memory.py
│   │   ├── voice.py
│   │   └── health.py
│   │
│   ├── llm/                        # LLM provider abstraction
│   │   ├── base.py
│   │   ├── gemini.py
│   │   ├── openai.py
│   │   └── router.py
│   │
│   ├── voice/
│   │   ├── audio.py                # sounddevice wrapper
│   │   ├── stt.py                  # Groq Whisper
│   │   ├── tts.py                  # Piper
│   │   └── wake_word.py            # openWakeWord (Day 27, optional)
│   │
│   ├── memory/
│   │   ├── sqlite_store.py
│   │   ├── vector_store.py         # ChromaDB
│   │   ├── importance.py           # LLM-based scoring
│   │   └── projects.py             # Active project management
│   │
│   ├── tools/                      # LLM-callable tools (function calling)
│   │   ├── registry.py             # ToolRegistry
│   │   ├── web_search.py
│   │   ├── pdf_summarize.py
│   │   ├── app_launcher.py
│   │   ├── timer.py
│   │   ├── memory_tools.py
│   │   └── apps.yaml               # Whitelist of launchable apps
│   │
│   ├── services/
│   │   ├── conversation.py         # Voice loop orchestrator
│   │   └── cost_tracker.py
│   │
│   ├── desktop/
│   │   └── hotkeys.py              # pynput global hotkeys
│   │
│   ├── models/                     # Pydantic models
│   │   ├── chat.py
│   │   ├── memory.py
│   │   └── voice.py
│   │
│   ├── database/
│   │   ├── schema.sql
│   │   └── migrations/
│   │
│   ├── config/
│   │   └── settings.py             # Pydantic Settings
│   │
│   └── tests/
│       └── (smoke tests as needed)
│
├── frontend/                       # React + Vite + TS + Tailwind
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── components/
│   │   │   ├── ChatPanel.tsx
│   │   │   ├── StatusBar.tsx
│   │   │   └── SettingsPanel.tsx
│   │   ├── blob/                   # SVG + CSS animated blob
│   │   │   ├── Blob.tsx
│   │   │   └── BlobStates.ts
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts
│   │   │   └── useVoiceState.ts
│   │   └── websocket/
│   │       └── client.ts
│   ├── public/
│   ├── package.json
│   └── vite.config.ts
│
├── piper/                          # Piper TTS binary (downloaded)
│   └── piper.exe
│
├── piper_voices/                   # Downloaded ONNX voices
│   └── en_US-lessac-medium.onnx
│
├── wake_word_models/               # openWakeWord models (Week 4)
│
├── data/                           # Runtime data (gitignored)
│   ├── jarvis.db
│   ├── chroma/
│   ├── recordings/                 # Debug audio (temp)
│   └── logs/
│
├── docs/
│   ├── journal.md                  # Daily build journal
│   ├── architecture.md
│   ├── setup.md
│   └── demo_script.md              # Day 28 manual checklist
│
└── scripts/
    ├── setup_windows.ps1
    ├── download_models.py
    └── set_project.py
```

**Rules:**
- New backend modules go inside one of the existing folders. If something seems to need a new top-level folder, ask first
- No loose `.py` files at repo root
- No `jarvis_v2.py`, `working_final.py`, version-suffixed files. Use git for versions
- Tests live in `backend/tests/`; runtime data lives in `data/` (gitignored)

## Naming conventions

| Item | Convention | Example |
|---|---|---|
| Python files | `snake_case.py` | `vector_store.py` |
| Python classes | `PascalCase` | `ToolRegistry` |
| Python functions | `snake_case` | `set_active_project` |
| React components | `PascalCase.tsx` | `ChatPanel.tsx` |
| React hooks | `useCamelCase.ts` | `useWebSocket.ts` |
| Constants | `UPPER_SNAKE` | `MAX_TOOL_CALLS` |
| Config keys in YAML | `snake_case` | `wake_word_enabled` |
| DB table names | `snake_case` plural | `messages`, `cost_log` |
| WebSocket event names | `snake_case` | `wake_word`, `ptt_start` |

## Locked tech stack

**Do not propose alternatives without explicit ask.**

| Layer | Choice | Version notes |
|---|---|---|
| Backend language | Python | 3.12 |
| API framework | FastAPI | latest stable |
| Validation | Pydantic v2 + Pydantic Settings | |
| SQL DB | SQLite | stdlib, foreign keys ON every connection |
| Vector DB | ChromaDB | latest, configured with Gemini embeddings |
| Logging | loguru | with request IDs |
| Desktop shell | **PyWebView** | NOT Tauri |
| Frontend framework | React 18 + TypeScript | |
| Frontend build | Vite | |
| Styling | Tailwind CSS | |
| Animation | Framer Motion + SVG/CSS | NOT Three.js |
| Hotkeys | pynput | |
| Audio I/O | sounddevice | NOT PyAudio |
| Wake word | openWakeWord | Week 4 only, optional |
| STT | Groq Whisper-large-v3 | cloud only, no fallback in v1 |
| TTS | Piper | local, `en_US-lessac-medium` default |
| LLM primary | Gemini (Flash + Pro) | with native function calling |
| LLM fallback | OpenAI GPT-4o / 4o-mini | |
| Web search | Tavily + Gemini grounding | |
| PDF parsing | pymupdf (fitz) | |
| Arxiv | `arxiv` package | |
| Notifications | plyer | |
| App launching | `subprocess.Popen` | NOT pywinauto in v1 |
| HTTP client | httpx (async) | |

## Patterns to follow

### Project-scoped everything

Every memory read/write takes a `project_id`. Every tool that operates on memory or domain context routes through the active project.

```python
# RIGHT
async def log_to_project(content: str) -> str:
    project_id = await get_active_project_id()
    await memory.add(content, project_id=project_id, importance=10)
    return f"Logged to {await get_active_project_name()}"

# WRONG — bypasses project scoping
async def log_to_project(content: str) -> str:
    await memory.add(content, importance=10)
```

### Async all external calls

```python
# RIGHT
async def fetch_arxiv(arxiv_id: str) -> bytes:
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content

# WRONG — blocks event loop
def fetch_arxiv(arxiv_id: str) -> bytes:
    return requests.get(url).content
```

### Graceful fallback on external API failure

```python
# RIGHT
try:
    text = await stt.transcribe(audio)
except (httpx.HTTPError, GroqAPIError) as e:
    logger.error(f"STT failed: {e}")
    await broadcast_state("error", message="I couldn't hear that, try again")
    return

# WRONG — swallows errors silently
try:
    text = await stt.transcribe(audio)
except:
    text = ""
```

### Settings, not magic numbers

```python
# RIGHT — in settings.py
class Settings(BaseSettings):
    max_tool_calls: int = 5
    importance_threshold: float = 4.0
    wake_word_threshold: float = 0.5

# RIGHT — in tool code
if call_count >= settings.max_tool_calls:
    break

# WRONG
if call_count >= 5:  # what does 5 mean? why 5?
    break
```

## Anti-patterns to avoid

- **Don't add new top-level folders** without asking
- **Don't write monolithic files** — if a file is approaching 300 lines, split it
- **Don't use `print()` for debugging** — use `logger.debug()`; it can stay in committed code
- **Don't store API keys anywhere but `.env`** — no fallback "if env missing, use this default"
- **Don't catch `Exception` broadly** unless logging and re-raising, or at the outermost handler
- **Don't mix sync and async** in the same call chain — async all the way down for FastAPI paths
- **Don't bypass the LLM router** by calling Gemini/OpenAI directly from a route — always go through `llm/router.py`
- **Don't bypass the tool registry** by hard-coding tool dispatch — register and let the registry route

## Project-specific gotchas

- **Gemini SDK version drift** — pin in `requirements.txt` once working. The Python SDK has changed API shape multiple times. Verify against the installed version before writing new Gemini code.
- **ChromaDB default embeddings** download a sentence-transformers model on first use. Configure Gemini embeddings explicitly to skip this.
- **SQLite foreign keys** are off by default. Enable with `PRAGMA foreign_keys = ON;` on every connection.
- **PyWebView transparency on Windows** needs `transparent=True` AND React root with `background: transparent`. Test with a colored shape so you know it's working.
- **pynput on Windows** can have issues if not started on a background thread. Threading model matters.
- **Alt+Space conflicts** with Windows "system menu" by default. May need to suppress default behavior or choose a different hotkey if it misbehaves.
- **Bluetooth audio mode switching** (headset vs headphones) changes sample rates and breaks audio if not handled.
- **Gemini malformed JSON in function calls** — wrap parsing in try/except, reprompt on failure.
- **openWakeWord requires 16kHz mono** audio — wrong sample rate = silent failure.
- **Piper outputs raw PCM** by default — wrap in WAV header before playback or pipe directly to sounddevice.

## When to update this file

Update `project-architecture/SKILL.md` when:
- A new top-level folder is added (requires explicit decision)
- A locked stack item changes (very rare; requires explicit decision)
- A new project-wide pattern is established
- A new gotcha is discovered and confirmed

Do NOT update for:
- Day-to-day code changes
- Adding individual tools (the tool-calling skill covers this)
- Bug fixes
