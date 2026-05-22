// Thin WebSocket wrapper. Returns a connected socket; caller handles events.
// Backend is bound to 127.0.0.1, so no auth. URL is hardcoded for v1.
// A proper React hook (useWebSocket) lands later when the event surface is bigger.

import { WS_VOICE_URL } from "../api/config";

export function connectVoiceWS(): WebSocket {
  return new WebSocket(WS_VOICE_URL);
}
