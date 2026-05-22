// Single source of truth for the backend URLs.
// Hardcoded for v1 (dev only) — wiring up Vite env vars is overkill until we
// have a non-dev build. Backend binds to 127.0.0.1, same machine as the frontend.
export const API_BASE = "http://localhost:8000";
export const WS_VOICE_URL = "ws://localhost:8000/ws/voice";
