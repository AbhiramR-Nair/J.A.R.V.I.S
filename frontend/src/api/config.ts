// Single source of truth for the backend URLs.
// Hardcoded for v1 (dev only) — wiring up Vite env vars is overkill until we
// have a non-dev build. Backend binds to 127.0.0.1, same machine as the frontend.
// Both URLs use 127.0.0.1 explicitly — Edge WebView2 resolves 'localhost'
// to ::1 (IPv6) for both HTTP and WS, but uvicorn only binds to 127.0.0.1.
// Using 'localhost' causes silent fetch failures and ERR_CONNECTION_REFUSED.
export const API_BASE = "http://127.0.0.1:8000";
export const WS_VOICE_URL = "ws://127.0.0.1:8000/ws/voice";
