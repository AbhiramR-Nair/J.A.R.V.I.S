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
  // Active project name — fetched on mount, updated live via project_changed WS events.
  const [activeProject, setActiveProject] = useState<string>("");
  // useVoiceEvents returns a FIFO queue + dispatch. Consume events[0] one at a time.
  const { events, dispatch, amplitudeRef, connectionState, send } = useVoiceEvents();
  // True for ~30s after a PDF is dragged onto the window; cleared on voice summarize or timeout.
  const [pdfPending, setPdfPending] = useState(false);
  // Latest search source links from web_search / grounded_search tool calls.
  // Replaced on each new search; cleared when the user starts a new voice turn.
  const [searchSources, setSearchSources] = useState<{ title: string; url: string }[]>([]);

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
      // Clear previous search sources when a new user turn begins.
      setSearchSources([]);
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
    } else if (event.type === "project_changed") {
      setActiveProject(event.name);
    } else if (event.type === "pdf_pending") {
      // Backend confirmed the dropped PDF path is stored and ready.
      setPdfPending(true);
    } else if (event.type === "search_results") {
      // Replace the sources list; latest search wins.
      setSearchSources(event.sources);
    }

    dispatch({ type: "event_consumed" });
  }, [events, dispatch]);

  // Fetch the active project name once on mount.
  // GET /projects returns all projects; find is_active to get the current one.
  useEffect(() => {
    fetch(`${API_BASE}/projects`)
      .then((r) => r.json())
      .then((projects: { name: string; is_active: boolean }[]) => {
        const active = projects.find((p) => p.is_active);
        if (active) setActiveProject(active.name);
      })
      .catch(() => {});
  }, []);

  // Auto-clear the error toast after 3 seconds.
  useEffect(() => {
    if (!errorToast) return;
    const timer = setTimeout(() => setErrorToast(null), 3000);
    return () => clearTimeout(timer);
  }, [errorToast]);

  // Auto-clear the PDF pending cue after 30s if the user hasn't acted on it.
  useEffect(() => {
    if (!pdfPending) return;
    const timer = setTimeout(() => setPdfPending(false), 30000);
    return () => clearTimeout(timer);
  }, [pdfPending]);

  // Drag-and-drop: user drops a PDF onto the window.
  // WebView2 does NOT expose file.path (unlike Electron), so we upload the file
  // content to POST /pdf/drop. The backend saves it to data/dropped/ and gets a
  // real filesystem path, then broadcasts pdf_pending via WebSocket.
  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (!file || !file.name.toLowerCase().endsWith(".pdf")) return;
    const form = new FormData();
    form.append("file", file);
    fetch(`${API_BASE}/pdf/drop`, { method: "POST", body: form }).catch((err) =>
      console.error("pdf drop upload failed:", err)
    );
    // pdf_pending is broadcast by the backend on successful upload — no need to
    // set pdfPending here; it arrives via the WebSocket event consumer above.
  }

  // Close settings on Escape. Listener is only active while settings is open.
  useEffect(() => {
    if (!settingsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSettingsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen]);

  // Hides the window into the system tray. Backend + hotkey listener keep running.
  // Real quit is only available via the tray menu's Quit item.
  async function closeApp() {
    await fetch(`${API_BASE}/window/hide`, { method: "POST" }).catch(() => {});
  }

  return (
    <div
      className="flex flex-col h-screen relative bg-[#060d14]"
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <HeaderBand
        connectionState={connectionState}
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

        <ChatPanel messages={messages} sources={searchSources} />

        {pdfPending && (
          <div className="bg-cyan-900/60 backdrop-blur-sm rounded-lg px-4 py-2 text-cyan-200 font-mono text-xs max-w-sm text-center">
            PDF ready — say "summarize this"
          </div>
        )}

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
        <StatusBar voiceState={voiceState} amplitudeRef={amplitudeRef} activeProject={activeProject} />
      </div>
    </div>
  );
}

export default App;
