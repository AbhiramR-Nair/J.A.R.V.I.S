import { AnimatePresence } from "framer-motion";
import { useEffect, useState } from "react";

import { API_BASE } from "./api/config";
import { Blob } from "./blob/Blob";
import { ChatPanel, type ChatMessage } from "./components/ChatPanel";
import { HeaderBand } from "./components/HeaderBand";
import { SettingsPanel } from "./components/SettingsPanel";
import { StatusBar } from "./components/StatusBar";
import { useVoiceEvents } from "./hooks/useWebSocket";
import type { VoiceStateLiteral } from "./hooks/useWebSocket";

function App() {
  // Day 9: in-memory chat history; persisted to SQLite from Day 11 onward.
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Error toast — shown for 3s then auto-cleared.
  const [errorToast, setErrorToast] = useState<string | null>(null);
  // Day 11: voice state from the orchestrator's state_changed events.
  const [voiceState, setVoiceState] = useState<VoiceStateLiteral>("idle");
  // Controls the settings panel; toggled by the gear icon in HeaderBand.
  const [settingsOpen, setSettingsOpen] = useState(false);
  // useVoiceEvents returns a FIFO queue + dispatch. Consume events[0] one at a time.
  const { events, dispatch, amplitudeRef } = useVoiceEvents();

  // Process the head of the event queue. Dispatches event_consumed at the end so
  // the next event becomes events[0] and triggers another run of this effect.
  useEffect(() => {
    const event = events[0];
    if (!event) return;

    if (event.type === "mute_toggle") {
      // mute state is driven by state_changed events from the orchestrator
    } else if (event.type === "recording_saved") {
      console.log("recording saved:", event.path);
    } else if (event.type === "transcription_complete") {
      console.log(`transcript: "${event.text}" (${event.latency_ms.toFixed(0)}ms)`);
      setMessages((prev) => [...prev, { role: "user", text: event.text }]);
    } else if (event.type === "transcription_failed") {
      setErrorToast(event.error);
    } else if (event.type === "state_changed") {
      setVoiceState(event.state);
      // Zero amplitude when leaving listening/speaking so the orb doesn't ghost
      // stale reactivity into thinking/idle while the EMA decays.
      const audioActive = event.state === "listening" || event.state === "speaking";
      if (!audioActive) amplitudeRef.current = 0;
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

  // Auto-clear the error toast after 3 seconds.
  useEffect(() => {
    if (!errorToast) return;
    const timer = setTimeout(() => setErrorToast(null), 3000);
    return () => clearTimeout(timer);
  }, [errorToast]);

  // Close settings on Escape. Listener is only active while settings is open.
  useEffect(() => {
    if (!settingsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSettingsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen]);

  // Tells the backend to exit, which kills the whole process cleanly.
  async function closeApp() {
    await fetch(`${API_BASE}/shutdown`, { method: "POST" }).catch(() => {});
  }

  return (
    <div className="flex flex-col h-screen relative bg-[#060d14]">
      <HeaderBand
        onToggleSettings={() => setSettingsOpen((s) => !s)}
        onClose={closeApp}
      />

      <div className="flex flex-col items-center flex-1 gap-4 pt-6 pb-20 overflow-y-auto">
        <Blob voiceState={voiceState} size={180} amplitudeRef={amplitudeRef} />

        {/* AnimatePresence intercepts the unmount so SettingsPanel's exit animation fires */}
        <AnimatePresence>
          {settingsOpen && (
            <SettingsPanel onClose={() => setSettingsOpen(false)} />
          )}
        </AnimatePresence>

        <ChatPanel messages={messages} />

        {errorToast && (
          <div className="bg-red-900/60 backdrop-blur-sm rounded-lg px-4 py-2 text-red-200 font-mono text-xs max-w-sm text-center">
            {errorToast}
          </div>
        )}
      </div>

      {/* Status bar: absolute so it floats over the bottom of the content area
          without affecting the flex layout above. pb-20 on the content div
          prevents the chat panel from sliding under it. */}
      <div className="absolute bottom-3 left-3 right-3">
        <StatusBar voiceState={voiceState} amplitudeRef={amplitudeRef} />
      </div>
    </div>
  );
}

export default App;
