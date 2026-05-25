// Voice WebSocket hook — manages connection + event queue.
// Day 7: WS connection with lastEvent pattern (single value; back-to-back events could drop).
// Day 11: refactored to useReducer queue — events consumed one at a time so rapid
//         back-to-back events from the state machine (state_changed + assistant_message
//         arriving 50ms apart) are never silently dropped.

import { useEffect, useReducer } from "react";
import type { Dispatch } from "react";

import { WS_VOICE_URL } from "../api/config";

// Mirrors VoiceStateLiteral in backend/models/voice.py. Keep in sync.
export type VoiceStateLiteral =
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "muted"
  | "error";

// Union of all event types the backend can push. Extend as new event types land.
export type VoiceEvent =
  | { type: "connected"; request_id: string }
  | { type: "ptt_start" }
  | { type: "ptt_end" }
  | { type: "mute_toggle" }
  | { type: "recording_saved"; path: string }
  // Day 9 — STT events
  | { type: "transcribing"; path: string }
  | { type: "transcription_complete"; text: string; latency_ms: number }
  | { type: "transcription_failed"; error: string }
  // Day 11 — conversation state machine events
  | { type: "state_changed"; state: VoiceStateLiteral; prev_state: VoiceStateLiteral }
  | { type: "assistant_message"; text: string; turn_id: string }
  | { type: "speaking_started"; turn_id: string }
  | { type: "speaking_ended"; turn_id: string }
  | { type: "speaking_failed"; reason: string; turn_id: string }
  // Day 12 — audio robustness events
  | { type: "recording_cap_hit" }
  | { type: "audio_device_recovered" };

const MAX_QUEUE = 50;

export type QueueAction =
  | { type: "event_received"; event: VoiceEvent }
  | { type: "event_consumed" }
  | { type: "clear" };

// Pure reducer — no side effects. State is the ordered event queue (FIFO).
function queueReducer(state: VoiceEvent[], action: QueueAction): VoiceEvent[] {
  switch (action.type) {
    case "event_received":
      if (state.length >= MAX_QUEUE) {
        // Drop the oldest entry rather than the newest; recent events are more actionable.
        console.warn("useVoiceEvents: queue cap reached, dropping oldest event", state[0]);
        return [...state.slice(1), action.event];
      }
      return [...state, action.event];
    case "event_consumed":
      return state.slice(1);
    case "clear":
      return [];
  }
}

export interface VoiceEventsHook {
  events: VoiceEvent[];
  dispatch: Dispatch<QueueAction>;
}

// Returns the full event queue and the dispatch function.
// Consumers read events[0] (queue head) and call dispatch({type:"event_consumed"})
// once they have processed it. This guarantees every event fires exactly once
// regardless of how quickly the backend sends them.
export function useVoiceEvents(): VoiceEventsHook {
  const [events, dispatch] = useReducer(queueReducer, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    // cancelled becomes true on unmount, preventing the retry loop from reconnecting.
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      ws = new WebSocket(WS_VOICE_URL);

      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data) as VoiceEvent;
          console.log("voice event:", ev);
          dispatch({ type: "event_received", event: ev });
        } catch (err) {
          console.error("WS: bad payload:", e.data, err);
        }
      };

      ws.onclose = () => {
        // Clear stale events on disconnect so they don't replay after reconnect.
        dispatch({ type: "clear" });
        if (!cancelled) {
          retryTimer = setTimeout(connect, 1000);
        }
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (ws) ws.close();
    };
  }, []);

  return { events, dispatch };
}
