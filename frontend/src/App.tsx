// Day 2 placeholder — transparent window loads this via PyWebView.
// Day 3 adds a temporary "Ping backend" button and a WebSocket smoke test.
// Day 7 adds drag bar, close button, visible test shape, and voice event status badge.
// Day 11: useVoiceEvents refactored to reducer queue; handles assistant_message +
//         state_changed + speaking_failed from the new state machine.
// Real UI (blob, chat panel, settings) is built in Weeks 2-3.
import { useEffect, useState } from "react";

import { API_BASE } from "./api/config";
import { Blob } from "./blob/Blob";
import { ChatPanel, type ChatMessage } from "./components/ChatPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { useVoiceEvents } from "./hooks/useWebSocket";
import type { VoiceStateLiteral } from "./hooks/useWebSocket";

function App() {
  // Holds the /health result + the X-Request-ID we read off the response header.
  const [ping, setPing] = useState<string | null>(null);
  // Briefly shows the saved filename after a recording completes; clears via separate effect.
  const [lastRecording, setLastRecording] = useState<string | null>(null);
  // Day 9: in-memory chat history; persisted to SQLite from Day 11 onward.
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [transcribing, setTranscribing] = useState(false);
  // Error toast — shown for 3s then auto-cleared.
  const [errorToast, setErrorToast] = useState<string | null>(null);
  // Day 11: voice state from the orchestrator's state_changed events.
  const [voiceState, setVoiceState] = useState<VoiceStateLiteral>("idle");

  // useVoiceEvents returns a FIFO queue + dispatch. Consume events[0] one at a time.
  const { events, dispatch } = useVoiceEvents();

  // Process the head of the event queue. Dispatches event_consumed at the end so
  // the next event becomes events[0] and triggers another run of this effect.
  useEffect(() => {
    const event = events[0];
    if (!event) return;

    if (event.type === "mute_toggle") {
      // mute state is driven by state_changed events from the orchestrator
    } else if (event.type === "recording_saved") {
      const filename = event.path.split(/[\\/]/).pop() ?? event.path;
      console.log("recording saved:", event.path);
      setLastRecording(filename);
    } else if (event.type === "transcribing") {
      setTranscribing(true);
    } else if (event.type === "transcription_complete") {
      setTranscribing(false);
      console.log(`transcript: "${event.text}" (${event.latency_ms.toFixed(0)}ms)`);
      setMessages((prev) => [...prev, { role: "user", text: event.text }]);
    } else if (event.type === "transcription_failed") {
      setTranscribing(false);
      setErrorToast(event.error);
    } else if (event.type === "state_changed") {
      // Orchestrator state drives the status label from Day 11 onward.
      setVoiceState(event.state);
    } else if (event.type === "assistant_message") {
      setMessages((prev) => [...prev, { role: "assistant", text: event.text }]);
    } else if (event.type === "speaking_failed") {
      setErrorToast(event.reason);
    } else if (event.type === "recording_cap_hit") {
      setErrorToast("Recording stopped at 30s limit.");
    } else if (event.type === "audio_device_recovered") {
      setErrorToast("Switched to default microphone.");
    }

    dispatch({ type: "event_consumed" });
  }, [events, dispatch]);

  // Auto-clear lastRecording badge after 2s.
  useEffect(() => {
    if (!lastRecording) return;
    const timer = setTimeout(() => setLastRecording(null), 2000);
    return () => clearTimeout(timer);
  }, [lastRecording]);

  // Auto-clear the error toast after 3 seconds.
  useEffect(() => {
    if (!errorToast) return;
    const timer = setTimeout(() => setErrorToast(null), 3000);
    return () => clearTimeout(timer);
  }, [errorToast]);

  // Call /health, then render both the JSON body and the X-Request-ID header.
  // Reading the header proves the backend's CORS expose_headers is set right.
  async function pingBackend() {
    try {
      const res = await fetch(`${API_BASE}/health`);
      const requestId = res.headers.get("X-Request-ID");
      const body = await res.json();
      setPing(`${JSON.stringify(body)}  |  X-Request-ID: ${requestId}`);
    } catch (err) {
      setPing(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // Derive a human-readable status label from the orchestrator's voiceState.
  const statusLabel = voiceState !== "idle"
    ? voiceState
    : transcribing
    ? "transcribing…"
    : lastRecording
    ? `saved: ${lastRecording}`
    : "idle";

  // Tells the backend to exit, which kills the whole process cleanly.
  async function closeApp() {
    await fetch(`${API_BASE}/shutdown`, { method: "POST" }).catch(() => {});
  }

  return (
    <div className="flex flex-col h-screen">
      {/*
        Drag bar: -webkit-app-region is a Chromium-specific CSS property that
        Edge WebView2 (PyWebView's engine on Windows) honours. The 'drag' value
        makes the entire bar act as a native window drag handle. The close button
        overrides this with 'no-drag' so its click isn't swallowed by the drag handler.
      */}
      <div
        style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
        className="h-8 w-full flex items-center justify-end px-2 bg-black/20 shrink-0"
      >
        <button
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
          onClick={closeApp}
          className="text-white/50 hover:text-white/90 font-mono text-xs w-6 h-6 flex items-center justify-center rounded hover:bg-white/10"
        >
          ✕
        </button>
      </div>

      {/* Main content area */}
      <div className="flex flex-col items-center justify-center flex-1 gap-4">
        {/* Animated orb — visual face of the assistant */}
        <Blob voiceState={voiceState} size={180} />

        {/* Dev-only state cycler: force any voice state without triggering the backend.
            Removed on Day 17 polish. Only renders when Vite's DEV flag is true. */}
        {import.meta.env.DEV && (
          <div className="flex gap-1 flex-wrap justify-center">
            {(["idle", "listening", "thinking", "speaking", "muted", "error"] as VoiceStateLiteral[]).map((s) => (
              <button
                key={s}
                onClick={() => setVoiceState(s)}
                className={`font-mono text-xs px-2 py-1 rounded border transition-colors ${
                  voiceState === s
                    ? "bg-cyan-600/60 border-cyan-400 text-cyan-100"
                    : "bg-black/30 border-white/10 text-white/50 hover:text-white/80 hover:border-white/30"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {/* Status badge — debug aid while blob is new; demoted/removed on Day 17 */}
        <div className="bg-black/30 backdrop-blur-sm rounded-lg px-4 py-2 text-cyan-300 font-mono text-sm">
          Status: {statusLabel}
        </div>

        {/* Day 11: live state label fed by state_changed events from the orchestrator.
            Blob animations (Week 3) will replace this; for now it's the verification tool. */}
        <div className="text-xs opacity-60 text-cyan-200 font-mono">
          {voiceState === "muted" ? "Muted" : voiceState}
        </div>

        <div className="bg-black/30 backdrop-blur-sm rounded-2xl px-6 py-4 text-cyan-400 font-mono text-sm">
          J.A.R.V.I.S — online
        </div>
        <button
          onClick={pingBackend}
          className="bg-cyan-600/40 hover:bg-cyan-600/60 text-cyan-100 font-mono text-xs px-4 py-2 rounded-lg"
        >
          Ping backend
        </button>
        {ping && (
          <div className="bg-black/30 backdrop-blur-sm rounded-lg px-4 py-2 text-cyan-300 font-mono text-xs max-w-md break-all">
            {ping}
          </div>
        )}

        {/* Error toast — auto-fades after 3s via the useEffect above */}
        {errorToast && (
          <div className="bg-red-900/60 backdrop-blur-sm rounded-lg px-4 py-2 text-red-200 font-mono text-xs max-w-sm text-center">
            {errorToast}
          </div>
        )}

        {/* Chat history — user transcripts + assistant replies from Day 11 */}
        <ChatPanel messages={messages} />

        {/* Settings panel — mic test for now; full settings drawer on Day 17 */}
        <SettingsPanel />
      </div>
    </div>
  );
}

export default App;
