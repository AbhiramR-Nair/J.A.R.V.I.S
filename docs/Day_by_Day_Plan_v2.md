# Day-by-Day Execution Plan v2 — Jarvis V1 (Revised for AI-Pair Workflow)

**Revision notes vs v1:**
- Tauri → **PyWebView** (pure Python, no Rust learning curve)
- Three.js shader blob → **SVG/CSS animated blob** (readable, debuggable)
- Wake word moved from Week 2 to Week 4 (optional, cut first if behind)
- STT/TTS fallback chains removed — just Groq and Piper
- Eval harness replaced with manual `demo_script.md`
- ~4 days of buffer recovered from Weeks 1-3, redirected to Week 4 substance

**Companion documents:**
- `Version_1_plan.md` — full plan
- `CLAUDE.md` — repo-root file for Claude Code (workflow rules)
- `.claude/skills/project-architecture/SKILL.md` — architectural context for Claude Code

---

## How to Use This Plan with Claude Code

Every day follows this loop:

1. **Morning (5 min):** read the day's plan, pull latest from main
2. **For each task:** write the docstring/signature yourself → ask Claude Code to implement → **read every line** → ask Claude to explain anything unclear → type one line yourself (re-type, in your own keystrokes — your hands learn idioms this way) → run, verify
3. **Stuck for >30 min?** Walk away for 10 minutes. If still stuck after 1 hour, descope rather than thrash
4. **Evening (10 min):** commit, write one line in `docs/journal.md`, glance at tomorrow's plan

**The non-negotiable rule:** never accept code you can't explain. If Claude writes 50 lines and you can't say what each section does, ask. This is what keeps the project from collapsing in Week 3.

---

# WEEK 1 — Foundation + PyWebView Shell

> **Week 1 goal:** transparent always-on-top window with global hotkey, FastAPI backend, LLM working, memory schema in place.

---

## Day 1 — Environment Setup

**Goal:** every tool installed, every API key obtained, repo created.

**Tasks:**
- Install Python 3.12 (official installer, check "Add to PATH")
- Install Node.js LTS (v20+) — needed for the frontend build, not for Tauri anymore
- Install Git, VS Code
- **Install Claude Code for VS Code** (extension)
- VS Code extensions: Python, Pylance, Black Formatter, ESLint, Prettier, Tailwind IntelliSense
- Sign up for API keys: Gemini (Google AI Studio), Groq, Tavily, OpenAI (fallback)
- Create GitHub repo `research-jarvis` (private)
- Create `.env.example` with placeholder keys; `.env` with real keys (in `.gitignore`)
- **Drop in the three starter files:** `CLAUDE.md`, `.claude/skills/project-architecture/SKILL.md`, this file as `docs/Day_by_Day_Plan_v2.md`

**Completion Criteria:**
- [ ] `python --version` shows 3.12.x
- [ ] `node --version` shows v20+
- [ ] All 4 API keys saved in `.env`
- [ ] Empty repo pushed to GitHub with `.gitignore`, `README.md`, `CLAUDE.md`, and the skill file
- [ ] Claude Code extension installed and signed in

**Git Commit:** `chore: initial repo setup with Claude Code config`

**Time Budget:** 3 hours

**Watch Out For:** Don't skip the Claude Code starter files. They're what make the daily workflow actually work.

---

## Day 2 — Repo Scaffolding

**Goal:** full folder structure created; backend boots; PyWebView opens a window.

**Tasks:**
- Create the full folder structure (see `project-architecture/SKILL.md`)
- Backend venv: `python -m venv .venv`, activate
- Install backend deps:
  ```
  pip install fastapi uvicorn pydantic pydantic-settings python-dotenv loguru httpx websockets sqlite-utils chromadb pywebview pynput
  pip freeze > backend/requirements.txt
  ```
- Frontend setup (lightweight — no Tauri):
  - `mkdir frontend && cd frontend && npm create vite@latest . -- --template react-ts`
  - Install: `npm install tailwindcss framer-motion`
  - `npx tailwindcss init -p`
- Create `backend/main.py` with FastAPI "hello world"
- Create `backend/desktop.py` that uses PyWebView to load `http://localhost:5173` (Vite dev server) into a transparent always-on-top window
- Boot test: run `npm run dev` in frontend, `python backend/desktop.py` in another terminal, see Vite content in transparent window

**Completion Criteria:**
- [ ] FastAPI returns `{"status": "ok"}` at `http://localhost:8000/health`
- [ ] PyWebView opens a transparent always-on-top window showing the React app
- [ ] Folder structure matches `project-architecture/SKILL.md`

**Git Commit:** `feat: project scaffolding, pywebview shell, backend boots`

**Time Budget:** 4 hours

**Watch Out For:** PyWebView transparency on Windows requires `frameless=True` AND your React root must have transparent background CSS. Test with a colored shape inside so you know it's working.

---

## Day 3 — FastAPI Base

**Goal:** backend has all core endpoints stubbed, settings load from `.env`, logs work.

**Tasks:**
- Create `backend/config/settings.py` using Pydantic Settings — load all API keys
- Endpoints:
  - `GET /health` — returns status
  - `POST /chat` — accepts `{message: str}`, returns dummy response
  - `GET /memory` — returns empty list
  - `GET /voice-state` — returns `{state: "idle"}`
  - `WS /ws/voice` — WebSocket for voice events (stub)
- Add loguru middleware with request IDs
- Add CORS for `http://localhost:5173`
- Pydantic request/response models in `backend/models/`

**Completion Criteria:**
- [ ] All endpoints respond correctly via curl
- [ ] Logs show request IDs flowing through
- [ ] React frontend can call `/health` and display result
- [ ] WebSocket connection establishes from frontend

**Git Commit:** `feat: fastapi endpoints, settings, structured logging, websocket`

**Time Budget:** 4 hours

**Workflow tip:** This is a great day to practice the "write docstring first" habit. For each endpoint, write what it takes and returns as a comment before asking Claude Code to implement.

---

## Day 4 — LLM Provider Abstraction

**Goal:** can ask Gemini a question through your code, with OpenAI as fallback.

**Tasks:**
- `backend/llm/base.py`: `BaseProvider` abstract class with `generate(prompt, tools=None) -> Response`
- `backend/llm/gemini.py`: implementation using `google-generativeai` SDK
- `backend/llm/openai.py`: fallback implementation
- `backend/llm/router.py`: try Gemini, fall back on rate limit or error
- Wire `/chat` to use the router
- `backend/services/cost_tracker.py`: logs every call to `cost_log` table (to be created Day 5)

**Completion Criteria:**
- [ ] `POST /chat` with `{"message": "Hello"}` returns real Gemini response
- [ ] Manually break Gemini (bad key briefly) → OpenAI fallback works
- [ ] Frontend chat can send a message and display response
- [ ] You can explain (out loud, to yourself) what `BaseProvider`, `GeminiProvider`, and `Router` each do

**Git Commit:** `feat: llm provider abstraction with gemini primary, openai fallback`

**Time Budget:** 5 hours

**Watch Out For:** Gemini SDK pins matter. Once it works, freeze the version in `requirements.txt`.

---

## Day 5 — SQLite Schema

**Goal:** persistent storage; project-scoped from day one.

**Tasks:**
- Create `backend/database/schema.sql` with tables: `projects`, `conversations`, `messages`, `memory`, `tasks`, `cost_log` (see V1 plan for full schema)
- `projects` has `is_active` boolean — exactly one active at a time
- `backend/memory/sqlite_store.py`: wrapper with `save_message`, `save_memory`, `get_active_project`, `set_active_project`, `list_projects`
- Insert default "general" project on first boot
- Wire `/chat` to save messages under the active project

**Completion Criteria:**
- [ ] DB file created at `data/jarvis.db` on backend boot
- [ ] Chat messages saved with correct `project_id`
- [ ] Script `python scripts/set_project.py kinase` switches active project
- [ ] Survives backend restart

**Git Commit:** `feat: sqlite schema with project-scoped storage`

**Time Budget:** 5 hours

**Watch Out For:** SQLite foreign keys are OFF by default. Enable with `PRAGMA foreign_keys = ON;` on every connection — Claude will know this if you ask.

---

## Day 6 — ChromaDB Semantic Memory

**Goal:** assistant can recall relevant past info via vector search, scoped to active project.

**Tasks:**
- `backend/memory/vector_store.py`: wraps ChromaDB with `add(text, project_id, metadata)`, `search(query, project_id, k=5)`
- Use Gemini `text-embedding-004` (free)
- `backend/memory/importance.py`: LLM-scored 1-10. Skip storage if < 4
- Update `/chat` flow: semantic search before LLM call (include top 3 as context) → after response, score and store if worth it
- Test:
  - Store: "I'm working on ABL1 kinase resistance prediction"
  - Later query: "What was my project?" → retrieves the fact

**Completion Criteria:**
- [ ] ChromaDB persists in `data/chroma/`, survives restart
- [ ] 3 stored facts, query retrieves the right one
- [ ] Trivial messages ("hi", "ok") get importance < 4 and skip storage
- [ ] Cross-project isolation works (kinase facts don't appear in fitness queries)

**Git Commit:** `feat: semantic memory with importance scoring`

**Time Budget:** 6 hours

**Watch Out For:** ChromaDB's default embedding will try to download a sentence-transformers model. Configure Gemini embeddings from the start to skip this.

---

## Day 7 — PyWebView Shell + Global Hotkey

**Goal:** transparent always-on-top frameless window with working push-to-talk hotkey.

**Tasks:**
- `backend/desktop.py`: PyWebView window config
  - `frameless=True, on_top=True, transparent=True`
  - Size 400x600, draggable via CSS region
  - Loads React app (Vite dev or built static files)
- `backend/desktop/hotkeys.py`: use `pynput` for global hotkeys
  - Alt+Space pressed → send "ptt_start" via WebSocket to frontend
  - Alt+Space released → send "ptt_end"
  - Ctrl+Alt+J → send "mute_toggle"
- React side: listen for events, log them, show visual feedback
- Add CSS `-webkit-app-region: drag` on a header area so you can move the window
- Add a small close button

**Completion Criteria:**
- [ ] Window is transparent (desktop visible behind it)
- [ ] Stays on top of all other windows
- [ ] Drag-to-move works
- [ ] Pressing Alt+Space anywhere in Windows fires an event in React (visible in dev tools)
- [ ] Releasing Alt+Space fires the release event
- [ ] Ctrl+Alt+J fires mute event
- [ ] You understand how PyWebView + pynput + WebSocket talk to each other (sketch the flow on paper)

**Git Commit:** `feat: pywebview shell - transparent always-on-top with global hotkeys`

**Time Budget:** 5-6 hours

**Watch Out For:**
- `pynput` needs to run on the main thread or in a background thread carefully — Claude will handle this if you tell it
- Alt+Space conflicts with Windows "system menu" by default — you may need to suppress the default behavior or pick a different hotkey
- Transparent windows on Windows can have rendering quirks (black flicker on first paint) — this is normal

---

# WEEK 2 — Voice Pipeline (PTT only)

> **Week 2 goal:** complete voice loop with push-to-talk. Wake word is Week 4 if time allows.

---

## Day 8 — Audio Capture via Push-to-Talk

**Goal:** holding Alt+Space records mic audio; releasing returns the audio buffer.

**Tasks:**
- Install: `pip install sounddevice numpy`
- `backend/voice/audio.py`: `AudioRecorder` class
  - `start_recording()` — begins capturing from default mic to in-memory buffer
  - `stop_recording() -> bytes` — returns WAV bytes
  - 16kHz mono (matches Groq + wake word requirements later)
- Wire WebSocket events: `ptt_start` → start recording, `ptt_end` → stop, save temp WAV file for debugging
- Settings: list available input devices, allow selection (stored in `settings.json`)

**Completion Criteria:**
- [ ] Hold Alt+Space for 3 seconds, say "test" → WAV file appears in `data/recordings/`
- [ ] File plays back correctly (16kHz mono, your voice audible)
- [ ] Releasing without holding → no file created
- [ ] Mic device can be changed in settings

**Git Commit:** `feat: ptt audio capture with sounddevice`

**Time Budget:** 4 hours

**Watch Out For:** Windows mic permissions — Python may silently fail if permission denied. Test with a clear error path.

---

## Day 9 — Speech-to-Text via Groq

**Goal:** spoken audio reliably becomes text.

**Tasks:**
- Install: `pip install groq`
- `backend/voice/stt.py`: `STTService` with `transcribe(audio_bytes) -> str`
- Use Groq Whisper-large-v3, POST audio file
- Log latency on every call
- No fallback yet — if Groq fails, return error to user ("I couldn't hear you, try again")
- Wire: PTT release → audio buffer → STT → display transcript in React chat panel
- Test with technical terms: "ABL1", "kinase inhibitor", "RNA-seq", "T315I"

**Completion Criteria:**
- [ ] Say a sentence with technical terms → accurate transcript in chat panel
- [ ] Groq latency consistently < 1.5s for 5-second utterance
- [ ] Network error → graceful "couldn't hear you" message, no crash

**Git Commit:** `feat: groq whisper stt integration`

**Time Budget:** 4 hours

**Watch Out For:** Groq expects audio in WAV/MP3/etc formats. Make sure your `AudioRecorder` outputs proper WAV with headers, not raw PCM.

---

## Day 10 — Text-to-Speech via Piper

**Goal:** assistant speaks responses with a natural voice.

**Tasks:**
- Download Piper Windows binary from GitHub releases → `piper/piper.exe`
- Download voice `en_US-lessac-medium.onnx` + `.json` → `piper_voices/`
- `backend/voice/tts.py`: `TTSService` with `speak(text)`
  - Calls Piper as subprocess (simplest), outputs WAV bytes
  - Streams to `sounddevice.play()` for output
- Wire: LLM response → TTS → audio output
- Add a manual test endpoint `POST /speak {text}` for debugging

**Completion Criteria:**
- [ ] Type a chat message → hear spoken response within 2 seconds of LLM finishing
- [ ] No clipping, stuttering, or audio glitches
- [ ] Voice sounds natural enough that you don't cringe

**Git Commit:** `feat: piper tts integration`

**Time Budget:** 5 hours

**Watch Out For:** Piper outputs raw PCM by default. You may need to wrap in WAV header before playback, or pipe directly to sounddevice. Test on a long sentence (30+ words) — short tests hide buffering bugs.

---

## Day 11 — Full Voice Loop + Mute

**Goal:** end-to-end PTT voice conversation working.

**Tasks:**
- `backend/services/conversation.py`: orchestrates the full flow
  - PTT start → state: "listening" → start recording
  - PTT end → state: "transcribing" → STT
  - STT done → state: "thinking" → LLM call (with memory context from Day 6)
  - LLM done → state: "speaking" → TTS playback
  - TTS done → state: "idle"
- Broadcast state via WebSocket; React updates UI text label (blob comes later in Week 3)
- Mute toggle (Ctrl+Alt+J): blocks the conversation loop, shows "muted" state
- Save assistant messages to memory same as user messages

**Completion Criteria:**
- [ ] Hold Alt+Space, ask "what's the capital of France?", release → hear "Paris" within 5 seconds
- [ ] State label in UI changes through listening → transcribing → thinking → speaking → idle
- [ ] Mute hotkey works; conversation blocked while muted
- [ ] Multi-turn works: ask follow-up question, context is remembered

**Git Commit:** `feat: full ptt voice loop with state machine and mute`

**Time Budget:** 5 hours

**Watch Out For:** Race conditions — make sure TTS finishes before going to idle, and that muting mid-recording cleans up properly. Ask Claude to add explicit state guards.

---

## Day 12 — Audio Robustness

**Goal:** handle real-world Windows audio mess.

**Tasks:**
- Handle mic disconnect mid-use → graceful error, retry on default device
- Handle Windows mic permission denied → clear UI error
- "Test mic" button in settings: records 3s and plays back
- Long silence in PTT (held but no speech) → 30s timeout
- Very loud input (clipping) → don't crash, let STT fail gracefully

**Completion Criteria:**
- [ ] Unplug mic mid-conversation → error shown, app doesn't crash
- [ ] Works with at least 2 different mic devices (built-in + USB/Bluetooth)
- [ ] Test mic button works
- [ ] 30s silent recording doesn't lock up app

**Git Commit:** `fix: audio device handling and edge cases`

**Time Budget:** 4-5 hours

**Watch Out For:** Bluetooth devices on Windows switch between "headset" (16kHz, mic) and "headphones" (48kHz, no mic) modes. If your code crashes on the switch, handle it.

---

## Day 13 — Buffer Day (recovered from cut wake word)

**Goal:** catch up on anything Week 2 left rough.

**Tasks (pick what's needed):**
- Latency optimization — log per-stage timing, optimize slowest stage
- UI polish on chat panel
- Better error messages
- Multi-turn conversation testing with longer dialogues
- Record a Week 2 demo video showing the voice loop

**Completion Criteria:**
- [ ] Median end-to-end latency < 4 seconds
- [ ] Week 2 demo video recorded
- [ ] No known bugs in voice loop

**Git Commit:** `chore: week 2 polish and buffer`

**Time Budget:** flexible

---

## Day 14 — Buffer Day 2

**Goal:** more buffer, or get ahead on Week 3.

**Use this day to:**
- Fix anything still broken from Week 2
- Start Week 3 (Day 15) if everything is solid
- Tag release `v0.2.0-voice-loop`

---

# WEEK 3 — SVG/CSS Blob + Window Polish (compressed to 3 days)

> **Week 3 goal:** the assistant has a polished visual presence. Compressed since SVG/CSS blob is much faster to build than a shader-based one.

---

## Day 15 — SVG/CSS Animated Blob

**Goal:** an organic-looking animated blob that visually reflects assistant state.

**Tasks:**
- `frontend/src/blob/Blob.tsx` — single React component
- SVG `<path>` with animated `d` attribute, or CSS clip-path with animated values
- States: `idle`, `listening`, `thinking`, `speaking`, `muted`, `error`
- Each state has distinct: color, animation speed, scale, blur
  - `idle`: slow soft pulse, base color (soft cyan recommended)
  - `listening`: faster pulse, brighter, slight scale up
  - `thinking`: slow rotation, medium brightness
  - `speaking`: amplitude-driven (Day 16) or fallback random pulse
  - `muted`: desaturated, low opacity, very slow
  - `error`: red tint, jittery, auto-fades after 3s
- Use Framer Motion for smooth state transitions
- Listen to `voice-state` WebSocket events; update blob

**Completion Criteria:**
- [ ] Blob renders smoothly in PyWebView window
- [ ] All 6 states visually distinct
- [ ] State transitions are smooth (lerp, not snap)
- [ ] CPU usage stays under 10% while idle (check Task Manager)
- [ ] Looks nice enough you want it on your screen

**Git Commit:** `feat: svg animated blob with state machine`

**Time Budget:** 6 hours

**Watch Out For:** Don't perfect this. "Looks good enough" today is fine. The blob is delight, not substance.

---

## Day 16 — Audio Reactivity (simple version)

**Goal:** blob deforms with mic input and TTS output amplitude.

**Tasks:**
- Backend: while recording, compute amplitude per 50ms chunk, broadcast via WebSocket (throttle to ~20 msgs/sec)
- Same for TTS output
- React: blob CSS variable `--amplitude` updated from WebSocket; SVG path or scale responds to it
- Smooth with exponential moving average to avoid jitter
- Cap maximum deformation

**Completion Criteria:**
- [ ] Speak loudly → blob visibly reacts
- [ ] Silent → blob settles to idle motion
- [ ] During Jarvis speaking → blob pulses with speech
- [ ] No jitter or seizure-looking motion

**Git Commit:** `feat: audio-reactive blob deformation`

**Time Budget:** 4 hours

**Watch Out For:** Throttle WebSocket amplitude messages or you'll flood the frontend. 20 messages/second is plenty.

---

## Day 17 — Window Polish

**Goal:** the window behaves like a proper desktop overlay.

**Tasks:**
- Snap-to-corner: detect drag near screen corner, snap into place
- Click-through when idle (PyWebView has `transparent_at_corners` and OS-specific tricks — research with Claude)
- Settings panel (expandable section or separate small window):
  - Mic device dropdown
  - Hotkey display
  - Wake word toggle (disabled in UI for now, enabled in Week 4)
  - Active project display + switcher
- Chat history panel: last 5 exchanges, scrolls behind blob
- Small minimize/close buttons

**Completion Criteria:**
- [ ] Window snaps to corners
- [ ] Settings panel works, changes persist across restart
- [ ] Last few messages visible without obscuring blob
- [ ] Window can be minimized to tray (if PyWebView supports it; otherwise hide)

**Git Commit:** `feat: window polish - snap, settings, chat history`

**Time Budget:** 5 hours

---

## Days 18-19 — Buffer Days (recovered from compressed Week 3)

**Goal:** carry forward into Week 4, or finish anything rough.

**Use these days to:**
- Anything Week 3 didn't finish
- Get a head start on Week 4 (Day 20 tool calling is critical, starting early is fine)
- Record Week 3 demo
- Tag release `v0.3.0-blob-and-polish`

---

# WEEK 4 — Tool-Calling Assistant (the substantive week, 11 days available)

> **Week 4 goal:** Jarvis stops being a chatbot, becomes an assistant. This is the resume-worthy week.

---

## Day 20 — Tool-Calling Architecture (critical)

**Goal:** clean infrastructure for LLM to invoke Python functions.

**Tasks:**
- Create `backend/tools/registry.py` — `ToolRegistry` class
- Each tool: `name`, `description`, JSON Schema for params, async handler
- Update Gemini provider to attach tool schemas to chat calls
- Handle Gemini's function-call response: parse → execute → feed result back → final response
- Multi-tool loop with max-calls=5 safety limit
- Build trivial test tool: `get_current_time()` returns time
- **After this works, write `.claude/skills/tool-calling-pattern/SKILL.md`** capturing exactly how to add a new tool. You'll use this skill 5+ times this week.

**Completion Criteria:**
- [ ] Ask "what time is it?" → LLM calls `get_current_time` tool → answer spoken
- [ ] Logs show: user → LLM → tool call → tool result → LLM → final response
- [ ] You can explain the loop on paper without looking at code
- [ ] `tool-calling-pattern/SKILL.md` written and committed

**Git Commit:** `feat: tool-calling architecture with gemini function calling`

**Time Budget:** 6 hours

**Watch Out For:** Gemini sometimes returns malformed JSON in tool calls. Wrap in try/except, reprompt on failure.

---

## Day 21 — Project Memory Tools

**Goal:** voice commands to switch projects, log notes, recall facts.

**Tasks:**
- Tools:
  - `set_active_project(name)` — switches; creates if doesn't exist
  - `list_projects() -> list[str]`
  - `log_to_project(content)` — saves with importance=10
  - `recall_from_project(query) -> list[str]` — semantic search within active project
- System prompt update: "use these tools when user says 'log this' / 'switch to X' / 'what did we conclude about Y'"
- Test voice commands:
  - "Switch to kinase project"
  - "Log this: T315I shows 40-fold resistance shift"
  - "What did we say about T315I?"

**Completion Criteria:**
- [ ] All 4 commands work end-to-end via voice
- [ ] Active project persists across restart
- [ ] Cross-project isolation maintained
- [ ] Active project visible in UI

**Git Commit:** `feat: project memory tools`

**Time Budget:** 5 hours

---

## Days 22-24 — PDF + Arxiv Summarization (centerpiece, 3 days)

**Goal:** drop PDF or paste arxiv ID → structured spoken summary. Your portfolio differentiator.

**Day 22 — PDF parsing:**
- Install `pymupdf`
- PyWebView: enable file drop, send path via WebSocket
- `backend/tools/pdf_summarize.py`:
  - `parse_pdf(path)` returns sections (title, abstract, body, captions)
  - Chunk by headers, fall back to 2000-char chunks
  - Detect scanned PDFs (no text layer), return graceful error

**Day 23 — Structured summarization:**
- Tool: `summarize_paper(path)` returns structured output via Gemini JSON mode:
  ```json
  {
    "title": "...",
    "key_claims": [...],
    "methods": "...",
    "results": "...",
    "limitations": "...",
    "relevance_to_user": "..."
  }
  ```
- Hierarchical summarization for long papers (>30 pages): summarize chunks → summarize summaries
- TTS reads `key_claims` + `relevance_to_user`; full structure shown in chat
- Save summary as memory in active project

**Day 24 — Arxiv lookup + polish:**
- Install `arxiv` package
- Tool: `fetch_arxiv(arxiv_id)` — downloads PDF, runs same pipeline
- Voice: "summarize arxiv 2403.12345"
- Test on 3 actual papers from your reading list

**Completion Criteria:**
- [ ] Drag PDF on window → "summarize this" → spoken key claims within 15s
- [ ] Arxiv ID lookup works
- [ ] Summary recallable later: "what did we read about T315I last week?"
- [ ] Long paper (50+ pages) doesn't blow context window
- [ ] You can show this to someone and have them say "wow, useful"

**Git Commits (one per day):**
- `feat: pdf parsing for summarization`
- `feat: structured paper summarization with hierarchical chunking`
- `feat: arxiv lookup + polish`

**Time Budget:** 6 hours/day, 18 total

**Watch Out For:** Gemini Flash has 1M context but quality degrades past ~200k tokens. Use hierarchical even when full paper "fits."

---

## Day 25 — Web Search

**Goal:** "what's the latest on X?" returns current spoken results.

**Tasks:**
- Install `tavily-python`
- Tool: `web_search(query, max_results=5)` using Tavily
- Alternative: `grounded_search(query)` using Gemini's built-in grounding
- LLM picks based on query type (technical/research → Tavily, general facts → grounded)
- Spoken response summarizes; visual chat shows links

**Completion Criteria:**
- [ ] "Latest ABL1 inhibitor papers?" → relevant 2025-2026 results
- [ ] "What's the weather today?" → uses grounded search
- [ ] Links visible in chat for follow-up

**Git Commit:** `feat: web search with tavily and gemini grounding`

**Time Budget:** 4 hours

---

## Day 26 — App Launcher + Timers

**Goal:** "open VS Code" works; "Pomodoro for 25 minutes" fires notification.

**Tasks:**
- Install `plyer` (notifications) — pywinauto NOT needed, `subprocess.Popen` is simpler
- Create `backend/tools/apps.yaml` whitelist:
  ```yaml
  apps:
    vscode: "C:/Users/<you>/AppData/Local/Programs/Microsoft VS Code/Code.exe"
    chrome: "..."
    obsidian: "..."
    streamlit_dashboard: "powershell -Command \"cd C:/path; streamlit run main.py\""
  ```
- Tool: `open_app(name)` — look up in yaml, launch via subprocess
- Tool: `set_timer(minutes, label="Timer")` — fires plyer toast + speaks "your timer is done"
- Voice commands tested:
  - "Open VS Code"
  - "Launch my Streamlit dashboard"
  - "Set a Pomodoro for 25 minutes"

**Completion Criteria:**
- [ ] 4+ apps launch via voice
- [ ] Timer fires at right time with notification + speech
- [ ] Multiple concurrent timers work
- [ ] Unknown app → graceful "add it to apps.yaml" response

**Git Commit:** `feat: app launcher and timers`

**Time Budget:** 4 hours

---

## Day 27 — Wake Word (optional — only if on track)

**Goal:** "Hey Jarvis" activates assistant without hotkey.

**ONLY DO THIS IF:**
- All Week 4 tools work
- Voice loop is solid
- Days 28-30 still feel sufficient for polish

**If skipping:** that's fine. PTT-only is a legitimate v1.

**Tasks (if doing):**
- Install `openwakeword`
- Download "hey_jarvis" model
- `backend/voice/wake_word.py`: background asyncio task, 80ms audio chunks → openWakeWord
- On detection: trigger same flow as PTT start; auto-end on silence (use simple amplitude threshold)
- Wake word listener pauses during PTT and during assistant speaking
- Mute hotkey now also pauses wake word

**Completion Criteria (if doing):**
- [ ] "Hey Jarvis, what time is it?" works hands-free
- [ ] Run for 1 hour during normal work — false positives < 5
- [ ] Mute hotkey disables wake word; re-enabling works

**Git Commit:** `feat: wake word detection with openwakeword`

**Time Budget:** 5 hours

**If skipping:** use this day as extra polish / bug fix time. Tag it `chore: day 27 buffer`.

---

## Day 28 — Manual Demo Script (replaces eval harness)

**Goal:** repeatable manual test you run after each change.

**Tasks:**
- Create `demo_script.md` with 10 things to manually try:
  1. "What time is it?"
  2. "Switch to kinase project"
  3. "Log this: T315I shows 40-fold shift in TKI binding"
  4. "What did we just log?"
  5. "Latest ABL1 papers" (web search)
  6. Drag a PDF, "summarize this"
  7. "Open VS Code"
  8. "Set a timer for 1 minute" (wait, verify notification)
  9. "Tell me a joke" (basic chat)
  10. Hit Ctrl+Alt+J (mute), try PTT (should still work), hit again (unmute)
- Run all 10. Document any failures. Fix them.
- Add new prompts as you encounter edge cases

**Completion Criteria:**
- [ ] All 10 pass
- [ ] `demo_script.md` committed
- [ ] You've run it twice today and on Day 29

**Git Commit:** `docs: manual demo script for regression testing`

**Time Budget:** 4 hours (mostly fixing what breaks)

---

## Day 29 — README + Polish

**Goal:** a stranger could clone and run it.

**Tasks:**
- Rewrite README:
  - What it is (1 paragraph)
  - Demo video/gif at top
  - Features list
  - Architecture diagram (Mermaid or hand-drawn)
  - Prerequisites (Python 3.12, Node, Windows version)
  - API keys needed (with links)
  - Install steps (max 10 commands)
  - First run instructions
  - Hotkey reference
  - Troubleshooting
- Create `scripts/setup_windows.ps1`:
  - Check versions
  - Create venv, install Python deps
  - npm install
  - Download Piper voice
- Test on a clean clone

**Completion Criteria:**
- [ ] README is good enough to post publicly
- [ ] Setup script works on a fresh clone
- [ ] No broken features in README
- [ ] License file added (MIT recommended)

**Git Commit:** `docs: comprehensive readme and setup script`

**Time Budget:** 5 hours

---

## Day 30 — Demo Video + Ship

**Goal:** publish.

**Tasks:**
- Record 3-5 minute demo:
  - Blob idle in corner
  - Alt+Space, "what are the latest ABL1 inhibitor papers?" → spoken answer
  - Drop a PDF, "summarize this" → structured summary
  - "Switch to fitness project, log this: ..." → confirmation
  - "Open VS Code" → app launches
  - "Set a Pomodoro for 25 minutes" → notification at the end
  - Mute hotkey demo
- Light edit (cuts only)
- Upload to YouTube unlisted or Loom
- LinkedIn post draft:
  - 1 paragraph: what it does
  - 1 paragraph: tech highlights + your computational biology angle
  - Link to repo + demo
- Tag `v1.0.0`
- Make repo public

**Completion Criteria:**
- [ ] Demo video linked in README
- [ ] LinkedIn post drafted
- [ ] `v1.0.0` on GitHub
- [ ] Repo public

**Git Commit:** `release: v1.0.0 - month 1 complete`

**Time Budget:** 4 hours

---

# Daily Discipline Reference

**Every morning (5 min):**
- Read day's plan
- Pull latest
- Run `demo_script.md` if past Day 28

**Every evening (10 min):**
- Commit (logical commits, not "wip")
- One line in `docs/journal.md`
- Glance at tomorrow's plan

**End of each week:**
- Tag release
- Record progress video
- Honest assessment: ahead / on-track / behind?
- **If behind:** descope, don't extend

---

# Drop-Cut Order (if behind on Day 22)

Cut from the bottom up:

1. Voice loop ← protect at all costs
2. Project memory + voice notes
3. PDF summarization
4. Web search
5. App launcher
6. Timers
7. Manual demo script
8. Window polish (snap-to-corner, etc.)
9. Audio-reactive blob (static blob is fine)
10. ~~Wake word (Day 27)~~ first to cut
11. ~~Polished demo video~~ rough recording fine

A working v1 with items 1-6 is genuinely impressive. Don't dilute the top to save the bottom.
