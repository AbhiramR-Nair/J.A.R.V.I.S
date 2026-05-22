# Version 1 Plan — Jarvis-like AI Companion (Revised)

**A voice-first, project-aware research and productivity assistant for daily use on a Windows i3 laptop (16 GB RAM, no GPU). Built with AI-pair-programming using Claude Code.**

> **Revision notes vs initial plan:**
> - Desktop shell: **PyWebView** (Python) replaces Tauri (Rust) — pure-Python stack, no Rust learning curve
> - Blob graphics: **SVG/CSS + Framer Motion** replaces Three.js shader — readable, debuggable
> - Voice activation: **Push-to-talk first**; wake word moved to Week 4 as optional
> - Fallback chains simplified — Groq only for STT (no faster-whisper fallback), Piper only for TTS (no ElevenLabs toggle in v1)
> - Testing: manual `demo_script.md` checklist replaces formal eval harness
>
> **Why these changes:** the original plan assumed a developer who hand-codes Python and Rust independently. This revised plan assumes AI-pair-programming with Claude Code as primary workflow. Cuts target features whose debugging depends on independent coding fluency, recovering ~4 days of buffer redirected to Week 4 substance.

---

## Project Objective

Build a **daily-driver AI companion** that runs as a floating, always-on-top desktop overlay on Windows. By the end of Month 1, the assistant should:

- Activate via push-to-talk hotkey (Alt+Space); wake word ("Hey Jarvis") optional in Week 4
- Transcribe speech reliably, including technical vocabulary (gene names, drug names, etc.)
- Respond intelligently using cloud LLMs (Gemini primary, OpenAI fallback)
- Speak responses back with a natural-sounding voice (Piper TTS)
- Maintain persistent, project-scoped memory
- Perform real work: web search, PDF/arxiv summarization, app launching, voice notes, timers
- React visually through an animated SVG/CSS blob
- Run comfortably on an i3 laptop by offloading heavy work (STT, LLM) to cloud APIs

### Hybrid Architecture Philosophy

- **Local (always-on, low compute):** hotkey listening, TTS playback, blob rendering, UI, vector DB
- **Cloud (heavy, on-demand):** speech-to-text (Groq), LLM inference (Gemini), web search (Tavily)

This split keeps the i3 cool and responsive while delivering quality that local-only would not.

### Out of Scope for Month 1

- Local LLM inference
- Webcam vision / face recognition
- Autonomous multi-agent orchestration
- Calendar (Google) and Gmail integration — deferred to Month 2 due to OAuth overhead
- Always-on proactive intelligence
- General desktop automation beyond app launching from a whitelist
- Tauri / Rust toolchain
- Three.js / GLSL shaders

---

## Definition of Done

By Day 30, the following workflow works end-to-end on the i3 laptop:

1. Hit Alt+Space, ask "What are the latest ABL1 inhibitor papers?" → grounded web search → spoken response
2. Drag a PDF onto the blob → "Summarize this paper" → structured spoken summary with key claims, methods, results, limitations
3. Say "Switch to fitness project, log this: bench press 3 sets of 8 at 70 kg" → project-scoped memory write
4. Say "Open VS Code" → application launches via subprocess
5. Say "Set a Pomodoro for 25 minutes" → Windows toast notification fires when done
6. Hit Ctrl+Alt+J → assistant mutes; blob dims

**Stretch (Day 27, only if on track):** Wake word activation working — "Hey Jarvis" replaces Alt+Space for hands-free invocation.

All of the above with sub-5-second end-to-end latency for typical queries.

---

## Working Method: AI-Pair-Programming with Claude Code

This project is built primarily with **Claude Code for VS Code**, not by hand-coding from scratch. Two companion documents define the workflow:

- **`CLAUDE.md`** at repo root — repo-wide instructions Claude Code reads on every session
- **`.claude/skills/project-architecture/SKILL.md`** — full architectural context

**The daily loop:**

1. Write the docstring/signature yourself before asking Claude to implement
2. Read every line Claude generates before accepting
3. Ask Claude to explain any single line you don't understand
4. Type at least one line of accepted code yourself (hands learn idioms)
5. Run it, verify it works, commit logical chunks

This is ~30% slower than pure vibe-coding but keeps comprehension debt at zero. By Week 3, fluency builds naturally as a side effect.

**Non-negotiable rule:** never accept code you can't explain. If Claude writes 50 lines and you can't say what each section does, ask before accepting.

---

## Tech Stack (Locked)

### Backend

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Single language across backend + desktop shell |
| API framework | FastAPI | Async, fast, good DX, WebSocket support |
| Validation | Pydantic v2 + Pydantic Settings | Type-safe config and request validation |
| Relational DB | SQLite | Zero-config, perfect for single-user local app |
| Vector DB | ChromaDB | Embedded, no separate server, semantic memory |
| Logging | loguru | Drop-in upgrade over stdlib logging |

### Desktop Shell

| Layer | Choice | Rationale |
|---|---|---|
| Framework | **PyWebView** | Pure Python; no Rust toolchain or compilation. Transparent always-on-top frameless windows on Windows. Single-language stack |
| Hotkeys | **pynput** | Pure Python global hotkey listener |
| Frontend | React 18 + Vite + TypeScript + TailwindCSS | Familiar stack, fast HMR; runs inside PyWebView's webview |
| Animation | **Framer Motion + SVG/CSS** | Readable animations. No Three.js, no shaders. Anything you can't read, you can't debug |
| IPC | WebSocket between React and FastAPI backend | One channel for both data and voice state events |

### Voice Stack (Hybrid, Simplified)

| Component | Choice | Mode | Why |
|---|---|---|---|
| Hotkey trigger | pynput global shortcut (Alt+Space) | Local | Push-to-talk: zero false positives, works in meetings |
| Wake word (Day 27, optional) | openWakeWord | Local, always-on | Free, open, customizable — only if Week 4 has time |
| STT | **Groq Whisper-large-v3** | Cloud only | ~300 ms latency, handles technical vocab. No local fallback in v1 — if Groq fails, show error |
| TTS | **Piper** (`en_US-lessac-medium`) | Local only | Sounds natural, fast on CPU. No ElevenLabs toggle in v1 |
| Audio I/O | **sounddevice** | Local | Cleaner than PyAudio on Windows |

### LLM Layer

| Component | Choice | Role |
|---|---|---|
| Primary LLM | Gemini 2.0 (Flash + Pro tiers) | Generous free tier, native function calling, built-in grounded search |
| Fallback LLM | OpenAI GPT-4o / 4o-mini | When Gemini quota hit or specific tasks |
| Function calling | Gemini native tool API | Powers all assistant actions |
| Embeddings | Gemini `text-embedding-004` | Free, used in ChromaDB |

### Tools and Integrations

| Tool | Library | Purpose |
|---|---|---|
| Web search | Tavily API + Gemini grounded search | "What are the latest papers on X?" |
| App launcher | `subprocess.Popen` + `apps.yaml` whitelist | "Open VS Code" — simpler and more reliable than pywinauto for launch-only |
| Notifications | plyer | Windows toast for timers/reminders |
| PDF parsing | pymupdf (fitz) | Paper summarization input |
| Arxiv lookup | arxiv (Python wrapper) | Fetch by ID |
| HTTP | httpx (async) | All external API calls |

### Why these specific swaps from the initial plan

- **PyWebView instead of Tauri** — Tauri requires Rust toolchain + Tauri 2.x API (still evolving; 1.x tutorials mislead). PyWebView is pure Python, works immediately, and AI assistance can reason about the whole stack in one language. Trade-off: heavier binary (~80 MB vs ~10 MB), but irrelevant for a personal daily-driver
- **SVG/CSS blob instead of Three.js shader** — GLSL shaders are debugging nightmares without expertise. SVG paths with Framer Motion are readable, animatable, and achieve 80% of the visual appeal at 20% of the complexity
- **PTT-first, wake word optional** — wake word adds always-on audio listening, false-positive tuning, mute-state management, pause-during-PTT logic. ~2-3 days of complexity for a "nice to have." PTT via Alt+Space is dramatically simpler and works perfectly as primary trigger
- **No STT fallback chain** — implementing "Groq → local Whisper" doubled the voice-pipeline complexity. v1 just uses Groq; if it fails, user sees "couldn't hear you, try again." Realistic for personal use
- **No TTS premium toggle** — building settings UI to swap TTS providers is sub-project work. Lock to Piper, ship it
- **subprocess instead of pywinauto** — for launch-only use cases, `subprocess.Popen` with paths from `apps.yaml` is simpler, more reliable, and easier to debug
- **Manual demo script instead of eval harness** — formal pytest-based regression suite is overkill for a single-user project. A 10-prompt manual checklist run after each change catches 95% of regressions for 10% of the effort

---

## Folder Structure

```text
research-jarvis/
│
├── CLAUDE.md                       # Claude Code workspace instructions
├── README.md                       # Public-facing
├── .env.example
├── .env                            # Real API keys (gitignored)
├── .gitignore
├── pyproject.toml
│
├── .claude/
│   └── skills/
│       ├── project-architecture/SKILL.md   # Day 1
│       ├── tool-calling-pattern/SKILL.md   # Day 20 (after building registry)
│       └── (more added as built — voice, memory, gotchas)
│
├── backend/
│   ├── main.py                     # FastAPI entry
│   ├── desktop.py                  # PyWebView launcher
│   ├── requirements.txt
│   │
│   ├── api/                        # FastAPI routes
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   ├── memory.py
│   │   ├── voice.py
│   │   └── health.py
│   │
│   ├── llm/                        # LLM provider abstraction
│   │   ├── __init__.py
│   │   ├── base.py                 # BaseProvider interface
│   │   ├── gemini.py               # Gemini implementation
│   │   ├── openai.py               # OpenAI fallback
│   │   └── router.py               # Provider selection + fallback logic
│   │
│   ├── voice/                      # Voice pipeline
│   │   ├── __init__.py
│   │   ├── audio.py                # sounddevice wrapper
│   │   ├── stt.py                  # Groq Whisper (no local fallback in v1)
│   │   ├── tts.py                  # Piper
│   │   └── wake_word.py            # openWakeWord (Day 27, optional)
│   │
│   ├── memory/                     # Persistent + semantic memory
│   │   ├── __init__.py
│   │   ├── sqlite_store.py
│   │   ├── vector_store.py         # ChromaDB
│   │   ├── importance.py           # LLM-based scoring
│   │   └── projects.py             # Active project management
│   │
│   ├── tools/                      # LLM-callable tools (function calling)
│   │   ├── __init__.py
│   │   ├── registry.py             # ToolRegistry
│   │   ├── web_search.py
│   │   ├── pdf_summarize.py
│   │   ├── app_launcher.py
│   │   ├── timer.py
│   │   ├── memory_tools.py
│   │   └── apps.yaml               # Whitelist of launchable apps
│   │
│   ├── services/                   # Cross-cutting business logic
│   │   ├── __init__.py
│   │   ├── conversation.py         # Voice loop orchestrator + state machine
│   │   └── cost_tracker.py         # API token + cost logging
│   │
│   ├── desktop/                    # Desktop integration
│   │   ├── __init__.py
│   │   └── hotkeys.py              # pynput global hotkeys
│   │
│   ├── models/                     # Pydantic request/response models
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   ├── memory.py
│   │   └── voice.py
│   │
│   ├── database/                   # DB schemas + migrations
│   │   ├── schema.sql
│   │   └── migrations/
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py             # Pydantic Settings
│   │
│   └── tests/
│       └── (smoke tests as needed)
│
├── frontend/                       # React app, runs inside PyWebView
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── components/
│   │   │   ├── ChatPanel.tsx
│   │   │   ├── StatusBar.tsx
│   │   │   └── SettingsPanel.tsx
│   │   ├── blob/                   # SVG + CSS animated blob (NOT Three.js)
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
├── wake_word_models/               # openWakeWord models (Week 4 only)
│
├── data/                           # Runtime data (gitignored)
│   ├── jarvis.db                   # SQLite
│   ├── chroma/                     # Vector store
│   ├── recordings/                 # Debug audio (temp)
│   └── logs/
│
├── docs/
│   ├── journal.md                  # Daily build journal (one line per day)
│   ├── Day_by_Day_Plan_v2.md       # Daily execution plan
│   ├── architecture.md
│   ├── setup.md
│   └── demo_script.md              # Day 28 manual regression checklist
│
└── scripts/
    ├── setup_windows.ps1           # One-shot environment setup
    ├── download_models.py          # Fetch Piper + (optionally) openWakeWord
    └── set_project.py
```

**Rules:**
- No loose `.py` files at repo root
- No `jarvis_v2.py`, `working_final.py`, `voice_test_old.py` clutter — use git for versions
- Tests live in `backend/tests/`
- Runtime data (`data/`) is gitignored
- New top-level folders require explicit decision (ask in CLAUDE.md conversation first)

---

## Implementation Strategy — Week by Week

Full daily breakdown is in `docs/Day_by_Day_Plan_v2.md`. Summary here.

### Week 1 — Foundation + PyWebView Shell (7 days)

**Goal:** all infrastructure working; transparent always-on-top window with global hotkey registered.

| Day | Focus | Key Deliverable |
|---|---|---|
| 1 | Environment | Python 3.12, Node LTS, Git, VS Code, **Claude Code extension installed**. All API keys obtained. Starter files (`CLAUDE.md`, skill file) in place |
| 2 | Scaffolding | Folder structure. Backend venv + deps. React+Vite+TS+Tailwind. PyWebView opens transparent window loading React app |
| 3 | FastAPI base | Endpoints `/health`, `/chat`, `/memory`, `/voice-state`, `/ws/voice`. Pydantic Settings. Loguru with request IDs. CORS for Vite dev server |
| 4 | LLM abstraction | `BaseProvider`, Gemini + OpenAI implementations, fallback router. Cost-tracking infrastructure |
| 5 | SQLite schema | Tables: `projects` (with `is_active`), `conversations`, `messages`, `memory`, `tasks`, `cost_log`. Default "general" project. Project-aware from day one |
| 6 | ChromaDB | Importance scoring via LLM (1-10 scale). Project-scoped semantic retrieval. Skip trivial messages |
| 7 | **PyWebView shell** | **Most important Week 1 deliverable.** Transparent, always-on-top, frameless window. Global hotkey (Alt+Space, Ctrl+Alt+J) via pynput. Drag-to-move |

---

### Week 2 — Voice Pipeline, PTT-Only (7 days)

**Goal:** complete voice loop with push-to-talk — STT → LLM → TTS — working end-to-end. Wake word deliberately deferred.

| Day | Focus | Key Deliverable |
|---|---|---|
| 8 | Audio capture (PTT) | Hold Alt+Space → record audio; release → save WAV buffer. 16 kHz mono. Mic device selection in settings |
| 9 | STT via Groq | `STTService.transcribe(audio)` using Groq Whisper-large-v3. Latency logging. Graceful error on API failure (no fallback in v1) |
| 10 | TTS via Piper | Piper subprocess with `en_US-lessac-medium`. Stream WAV to sounddevice. Latency < 1s to first audio |
| 11 | Full voice loop | `services/conversation.py` orchestrator with state machine: idle → listening → transcribing → thinking → speaking → idle. Mute via Ctrl+Alt+J |
| 12 | Audio robustness | Mic disconnect handling, permission errors, "Test mic" button. Multi-device testing (built-in + USB/Bluetooth) |
| 13 | Buffer day 1 | Latency optimization, polish, demo recording |
| 14 | Buffer day 2 | Tag `v0.2.0-voice-loop`; optionally start Week 3 early |

**Latency targets:**
- STT (Groq): < 1 s for typical utterance
- LLM (Gemini Flash, no tools): < 1.5 s
- TTS (Piper): < 500 ms to first audio
- **Total end-to-end: under 4 seconds**

---

### Week 3 — SVG/CSS Blob + Overlay Polish (5 days)

**Goal:** the assistant has a polished visual presence. Compressed from 7 to 5 days since SVG/CSS is dramatically faster to build than shader-based.

| Day | Focus | Key Deliverable |
|---|---|---|
| 15 | SVG/CSS blob | Single `Blob.tsx` component. SVG path or CSS clip-path with state-based animations. 6 states: idle, listening, thinking, speaking, muted, error. Framer Motion transitions |
| 16 | Audio reactivity | Mic input amplitude + TTS output amplitude broadcast via WebSocket (throttled to ~20 Hz). Blob CSS variable updates from amplitude |
| 17 | Window polish | Drag-to-move, snap-to-corner, settings panel (mic device, voice selection, project switcher), chat history (last 5 messages) |
| 18-19 | Buffer days | Anything Week 3 didn't finish, OR start Week 4 (Day 20 tool-calling) early. Tag `v0.3.0-blob` |

**Visual design constraints:**
- No Iron Man / sci-fi HUD — time sink
- Inspiration: Siri orb, OpenAI voice orb, Nothing OS glow
- Single accent color (soft cyan recommended); transparent background
- CPU usage < 10% while idle

---

### Week 4 — Tool-Calling Assistant (11 days, the substantive week)

**Goal:** Jarvis stops being a chatbot and becomes an assistant. This is what makes it career-relevant.

| Day | Focus | Key Deliverable |
|---|---|---|
| 20 | **Tool-calling architecture** | `ToolRegistry` with JSON schema per tool. Gemini native function calling wired up. Multi-tool loop with safety limit. **After this works, write `.claude/skills/tool-calling-pattern/SKILL.md`** |
| 21 | Project memory tools | `set_active_project`, `list_projects`, `log_to_project`, `recall_from_project`. Voice grammar: "switch to X", "log this: ...", "what did we conclude about Y?" |
| 22-24 | **PDF + arxiv summarization** | **Centerpiece feature.** Drag-drop PDF onto blob → pymupdf parse → chunked summary with structured output. Arxiv ID lookup via API. Hierarchical for long papers. 3 days because this is the resume-worthy differentiator |
| 25 | Web search | Tavily API tool + Gemini grounded search toggle. "Latest ABL1 inhibitor papers?" |
| 26 | App launcher + timers | `subprocess.Popen` from `apps.yaml` whitelist. `set_timer` with plyer toast notifications |
| 27 | **Wake word (optional)** | openWakeWord integration with "Hey Jarvis." **Only do this if Days 20-26 went smoothly.** If skipping, use as extra polish day |
| 28 | Manual demo script | 10-prompt regression checklist. Run, fix any failures. `demo_script.md` committed |
| 29 | README + polish | Public-facing README, install script, troubleshooting, license |
| 30 | Demo + ship | 3-5 minute demo video. LinkedIn post draft. Tag `v1.0.0`. Repo public |

---

## Tool-Calling Architecture (Day 20 — the key design)

Every assistant capability is a tool the LLM can invoke. This is what separates Jarvis from a chatbot.

```python
# backend/tools/registry.py
from typing import Callable, Awaitable
from pydantic import BaseModel

class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: dict   # JSON Schema
    handler: Callable[..., Awaitable]

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSchema] = {}

    def register(self, schema: ToolSchema):
        self._tools[schema.name] = schema

    def gemini_function_schemas(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    async def execute(self, name: str, args: dict):
        tool = self._tools[name]
        return await tool.handler(**args)
```

**Conversation flow:**

```text
User audio (PTT or wake word) → STT → Text
    ↓
Memory: semantic search for relevant context (project-scoped)
    ↓
LLM call with tool schemas + context attached
    ↓
LLM returns: text response  OR  tool_call(name, args)
    ↓
If tool_call → execute → feed result back into LLM (max 5 iterations)
    ↓
Final text response → TTS → Speak
    ↓
Important info → memory (project-scoped) with importance score
```

Adding a new feature in Month 2 = writing a new tool, registering it. No core changes.

---

## Fallback Plan

Real projects hit walls. Here are the planned exits if specific pieces fail.

### If PyWebView transparency or hotkeys misbehave (Days 2, 7)

**Symptom:** transparent background doesn't render, or pynput hotkeys conflict with system shortcuts.

**Fallback:**
- For transparency: try alternative configs; failing that, accept a solid-color frameless window — visually less elegant but functionally identical
- For hotkey conflicts: change to less-conflicted combos (Ctrl+Shift+J for PTT, Ctrl+Shift+M for mute); document in README

### If Groq STT has issues (Day 9)

**Symptom:** API errors, rate limits, accuracy poor for technical vocab.

**Fallback options (pick one):**
1. **Deepgram Nova-2** — also fast cloud STT, good with technical terms (similar integration cost)
2. **OpenAI Whisper API** — slower but reliable
3. **Accept the failure mode** — when STT fails, app shows "couldn't hear, try again." For v1, this is acceptable

Do NOT add local faster-whisper as a fallback in v1 — too much complexity for an i3.

### If Piper sounds rough (Day 10)

**Symptom:** robotic or laggy output.

**Fallback:**
1. Try a different Piper voice (~20 English voices available)
2. **Edge-TTS** (Microsoft's free streaming TTS, unofficial Python wrapper) — surprisingly good, free
3. ElevenLabs API (cents/day at personal use) — easy drop-in if budget allows

### If PDF summarization is too slow on long papers (Days 22-23)

**Symptom:** 50-page paper takes > 30 seconds, blob freezes.

**Fallback:**
1. Hierarchical map-reduce: summarize chunks → summarize summaries (avoid stuffing whole paper into context)
2. Limit v1 to abstract + intro + conclusion; full-paper mode is Month 2
3. Show progress in blob (state: "thinking" with progress indicator)

### If subprocess app launching has issues (Day 26)

**Symptom:** UAC prompts, antivirus flags, app paths vary across systems.

**Fallback:**
1. Use absolute paths in `apps.yaml` (already the plan)
2. For Microsoft Store apps, use `start shell:AppsFolder\<package-id>` syntax
3. Skip "switch to running window" features for v1; launch-only is fine

### If wake word false-positives are unbearable (Day 27)

**Symptom:** "Hey Jarvis" fires too often during normal speech.

**Fallback:**
1. Tighten detection threshold
2. Train custom "Hey Jarvis" model with openWakeWord's training pipeline (~1 day)
3. **Drop wake word entirely** — PTT only. This is fine; v1 was designed to ship without it

### If the whole month is behind on Day 25

**Symptom:** voice loop solid but Week 4 tools incomplete.

**Priority order to ship (drop from the bottom up):**

1. **Voice loop** (PTT + STT + LLM + TTS) — non-negotiable
2. **Project memory** + voice notes — defines the product
3. **PDF summarization** — career-relevant centerpiece
4. **Web search** — easy win, high utility
5. **App launcher** — easy win, daily usefulness
6. **Timers** — easy win
7. **Manual demo script** — keep, very cheap
8. **Window polish** (snap, settings) — easy to defer
9. **Audio-reactive blob** — static animation is fine
10. ~~Wake word~~ — first to cut
11. ~~Polished demo video~~ — rough recording is fine

A working v1 with items 1-6 is genuinely impressive and useful. Don't sacrifice quality on the top of the list to add things at the bottom.

---

## Cost Estimate (Monthly, Personal Use)

| Service | Free Tier | Expected Usage | Cost |
|---|---|---|---|
| Gemini API | 1500 req/day on Flash | Heavy daily use | **$0** |
| Groq Whisper | Generous free tier | All voice queries | **$0** |
| Tavily Search | 1000 searches/month free | Moderate research | **$0** |
| OpenAI (fallback only) | None | Rare | < $2 |

**Realistic monthly cost: under $5** at personal-use volume.

---

## Non-Negotiable Rules

1. **No local LLMs in Month 1** — cloud APIs are the right call for an i3
2. **No proactive agents yet** — assistant responds to user; doesn't initiate
3. **No general desktop automation** — only whitelisted app launching
4. **No Tauri, no Three.js** — locked stack decision; PyWebView and SVG/CSS only
5. **All memory operations must be project-scoped** — every read/write takes `project_id`
6. **All external API calls need graceful error handling** — no silent failures
7. **Commit per logical change**, not per day
8. **README updated continuously**, not at the end
9. **Demo video at end of each week** — captures progress, motivates next week
10. **Mute toggle works** — never ship a voice assistant without an off switch
11. **Cost-logging table populated from Day 5** — know your API spend
12. **Never accept code you can't explain** — the AI-pair-programming rule

---

## Weekly Milestones — Single-Sentence Tests

End each week by checking these statements are true:

- **End of Week 1:** "I can hit Alt+Space and a transparent, always-on-top window responds — the backend logs the event."
- **End of Week 2:** "I can hold Alt+Space, ask 'what's the capital of France?', and hear a spoken answer within 4 seconds."
- **End of Week 3:** "The blob looks alive — it reacts to my voice and changes state visibly."
- **End of Week 4:** "I used Jarvis for actual work today — summarized a paper, searched the web, logged a note, opened an app."

---

## Post-Month-1 Roadmap (preview)

Documented here so it's not lost, but **not for this month**:

- **Month 2:** Wake word (if not done in Week 4), Google Calendar + Gmail (OAuth done properly), better voice command parsing, custom wake word training
- **Month 3:** Local LLM option (Ollama + Llama 3.1 8B) for privacy-sensitive queries, offline mode
- **Month 4:** Proactive features (morning briefing, paper of the day from arxiv), webcam-based presence detection
- **Month 5+:** Multi-agent orchestration for complex research workflows, possible Tauri migration if PyWebView feels limiting

---

## Final Note

The biggest risk to this plan is **not** technical difficulty — it's scope creep. The features listed for Month 1 are the ceiling, not the floor. If something stops feeling fun by Week 3, cut it from the bottom of the priority list and protect the core.

A voice loop + project memory + PDF summarization + web search + app launcher, running reliably on an i3 via AI-pair-programmed code you genuinely understand, is a daily driver and a strong portfolio piece. That's the target. Everything else is bonus.

When Day 7 or Day 11 or Day 20 goes sideways — and one of them will, in some small way — the answer is not to switch tools or rewrite from scratch. It's to take a walk, sleep on it, and come back with fresh eyes. Most "stuck" problems solve themselves overnight.
