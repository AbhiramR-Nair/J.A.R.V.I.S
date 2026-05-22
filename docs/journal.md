# Build Journal

One line per day. What got done, what broke, what's next.

- 2026-05-22 — Repo structure scaffolded from Version_1_plan.md. Folders + stub files in place.
- 2026-05-22 — Day 3: FastAPI skeleton, Pydantic Settings, loguru + request-ID ContextVar, all endpoints stubbed with real response shapes, frontend ping + WebSocket wiring.
- 2026-05-22 — Day 4: LLM provider abstraction. Switched deprecated google-generativeai → google-genai. GeminiProvider + OpenAIProvider + LLMRouter + cost_tracker stub. /chat returns real Gemini answers; fallback chain confirmed. ~270 LOC across 5 new files.
