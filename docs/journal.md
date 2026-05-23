# Build Journal

One line per day. What got done, what broke, what's next.

- 2026-05-22 — Repo structure scaffolded from Version_1_plan.md. Folders + stub files in place.
- 2026-05-22 — Day 3: FastAPI skeleton, Pydantic Settings, loguru + request-ID ContextVar, all endpoints stubbed with real response shapes, frontend ping + WebSocket wiring.
- 2026-05-22 — Day 4: LLM provider abstraction. Switched deprecated google-generativeai → google-genai. GeminiProvider + LLMRouter + cost_tracker stub. /chat returns real Gemini answers. Fallback switched from OpenAI (quota exhausted) to Groq llama-3.3-70b-versatile (free tier, groq_llm.py). Fallback chain confirmed.
- 2026-05-23 — Day 5: SQLite schema + project-scoped storage. 6 tables (projects, conversations, messages, memory, tasks, cost_log), default "general" project seeded. /chat now persists user + assistant turns; cost_tracker writes to cost_log. scripts/set_project.py for CLI project switching. Gotcha: PRAGMA foreign_keys = ON is a no-op inside Python's implicit transactions — fix is to run it via executescript() after the schema. CLI sqlite3 also lies about FK state (shows its own connection); verify from Python.
