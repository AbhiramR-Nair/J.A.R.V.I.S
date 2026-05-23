// Day 2 placeholder — transparent window loads this via PyWebView.
// Day 3 adds a temporary "Ping backend" button and a WebSocket smoke test.
// Day 7 adds drag bar, close button, visible test shape, and voice event status badge.
// Real UI (blob, chat panel, settings) is built in Weeks 2-3.
import { useEffect, useState } from "react";

import { API_BASE } from "./api/config";
import { useVoiceEvents } from "./hooks/useWebSocket";

function App() {
  // Holds the /health result + the X-Request-ID we read off the response header.
  const [ping, setPing] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  // Briefly shows the saved filename after a recording completes; clears after 2s.
  const [lastRecording, setLastRecording] = useState<string | null>(null);

  // useVoiceEvents opens the WebSocket, logs every frame, and returns the last event.
  const lastEvent = useVoiceEvents();

  // mute_toggle flips the muted state; recording_saved flashes the filename for 2s.
  useEffect(() => {
    if (lastEvent?.type === "mute_toggle") {
      setMuted((m) => !m);
    } else if (lastEvent?.type === "recording_saved") {
      // Extract just the filename from the full path for a compact badge display.
      const filename = lastEvent.path.split(/[\\/]/).pop() ?? lastEvent.path;
      console.log("recording saved:", lastEvent.path);
      setLastRecording(filename);
      const timer = setTimeout(() => setLastRecording(null), 2000);
      return () => clearTimeout(timer);
    }
  }, [lastEvent]);

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

  // Derive a human-readable status label from mute + last event state.
  // recording_saved briefly overrides to show the filename, then reverts to idle.
  const statusLabel = muted
    ? "muted"
    : lastEvent?.type === "ptt_start"
    ? "listening"
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
        {/* Temporary test shape — visible proof the transparent window is working.
            Delete this on Day 15 when the real blob component lands. */}
        <div className="w-24 h-24 rounded-full bg-cyan-400/50" />

        {/* Status badge — shows current voice state driven by hotkey events */}
        <div className="bg-black/30 backdrop-blur-sm rounded-lg px-4 py-2 text-cyan-300 font-mono text-sm">
          Status: {statusLabel}
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
      </div>
    </div>
  );
}

export default App;
