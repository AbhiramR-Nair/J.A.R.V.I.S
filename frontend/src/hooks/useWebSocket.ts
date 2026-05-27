// Voice WebSocket hook — manages connection + event queue.
// Day 7: WS connection with lastEvent pattern (single value; back-to-back events could drop).
// Day 11: refactored to useReducer queue — events consumed one at a time so rapid
//         back-to-back events from the state machine (state_changed + assistant_message
//         arriving 50ms apart) are never silently dropped.

import { useEffect, useReducer, useRef, useState } from "react";
import type { Dispatch } from "react";

import { WS_VOICE_URL } from "../api/config";

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "disconnected";

// How many consecutive WS close events before we escalate from amber → red.
const DISCONNECTED_THRESHOLD = 5;

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
  | { type: "audio_device_recovered" }
  // Day 16 — amplitude is intentionally NOT queued; see onmessage handler below.
  // High-frequency (~20Hz), latest value wins, missing one is invisible.
  | { type: "amplitude"; value: number; source: "mic" | "tts" };

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
  // Side channel for amplitude — read each frame by Blob's rAF loop.
  // { current: number } rather than MutableRefObject (deprecated in React 19).
  amplitudeRef: { current: number };
  connectionState: ConnectionState;
}

// Returns the full event queue and the dispatch function.
// Consumers read events[0] (queue head) and call dispatch({type:"event_consumed"})
// once they have processed it. This guarantees every event fires exactly once
// regardless of how quickly the backend sends them.
export function useVoiceEvents(): VoiceEventsHook {
  const [events, dispatch] = useReducer(queueReducer, []);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  // Amplitude side channel: written by onmessage, read by Blob's rAF loop each frame.
  // Never goes through the reducer queue — see onmessage handler below for the rationale.
  const amplitudeRef = useRef(0);
  // Retry counter: incremented on each close, reset to 0 on successful open.
  // Not state because it drives no render — only connectionState renders.
  const retryCountRef = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    // cancelled becomes true on unmount, preventing the retry loop from reconnecting.
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      ws = new WebSocket(WS_VOICE_URL);

      ws.onopen = () => {
        retryCountRef.current = 0;
        setConnectionState("connected");
      };

      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data) as VoiceEvent;
          if (ev.type === "amplitude") {
            // Side channel: latest value wins. Never queued because:
            // - ~20Hz rate would backlog and delay real state events
            // - missing one amplitude tick is invisible to the user
            amplitudeRef.current = ev.value;
            return;
          }
          console.log("voice event:", ev);
          dispatch({ type: "event_received", event: ev });
        } catch (err) {
          console.error("WS: bad payload:", e.data, err);
        }
      };

      ws.onclose = () => {
        // Zero amplitude before clearing the queue so the orb doesn't ghost
        // on the last value after a WebSocket drop.
        amplitudeRef.current = 0;
        // Clear stale events on disconnect so they don't replay after reconnect.
        dispatch({ type: "clear" });
        retryCountRef.current += 1;
        // Escalate from amber (reconnecting) to red (disconnected) after 5 failures.
        // Counter resets on the next successful onopen so a recovery returns to cyan.
        if (retryCountRef.current >= DISCONNECTED_THRESHOLD) {
          setConnectionState("disconnected");
        } else {
          setConnectionState("reconnecting");
        }
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

  return { events, dispatch, amplitudeRef, connectionState };
}
