// Single source of truth for the backend URLs.
// Hardcoded for v1 (dev only) — wiring up Vite env vars is overkill until we
// have a non-dev build. Backend binds to 127.0.0.1, same machine as the frontend.
export const API_BASE = "http://localhost:8000";
// Use 127.0.0.1 (IPv4) explicitly for WebSocket — Edge WebView2 resolves
// 'localhost' to ::1 (IPv6) for WS connections, but uvicorn only binds to
// 127.0.0.1, so ws://localhost fails with ERR_CONNECTION_REFUSED.
export const WS_VOICE_URL = "ws://127.0.0.1:8000/ws/voice";
