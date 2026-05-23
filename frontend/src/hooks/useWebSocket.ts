// Minimal voice event hook for Day 7.
// Future days will extend this: reconnect logic (Day 11), amplitude data (Day 16).

import { useEffect, useState } from "react";

import { WS_VOICE_URL } from "../api/config";

// Union of all event types the backend can push. Extend as new event types land.
export type VoiceEvent =
  | { type: "connected"; request_id: string }
  | { type: "ptt_start" }
  | { type: "ptt_end" }
  | { type: "mute_toggle" }
  | { type: "recording_saved"; path: string };

export function useVoiceEvents(): VoiceEvent | null {
  const [last, setLast] = useState<VoiceEvent | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    // Cancelled becomes true when the component unmounts, stopping retry loops.
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      ws = new WebSocket(WS_VOICE_URL);

      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data) as VoiceEvent;
          // Console log every event — primary verification tool for Day 7.
          console.log("voice event:", ev);
          setLast(ev);
        } catch (err) {
          console.error("WS: bad payload:", e.data, err);
        }
      };

      // Retry on close (covers both clean closes and error-triggered closes).
      // This handles: backend not ready on first load, backend restart mid-session.
      ws.onclose = () => {
        if (!cancelled) {
          retryTimer = setTimeout(connect, 1000);
        }
      };
    }

    connect();

    // React 18 StrictMode runs cleanup + re-mount once in dev.
    // cancelled=true stops the retry loop; ws.close() triggers onclose which
    // would retry — but cancelled is already true so it won't.
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (ws) ws.close();
    };
  }, []);

  return last;
}
