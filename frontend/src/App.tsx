// Day 2 placeholder — transparent window loads this via PyWebView.
// Day 3 adds a temporary "Ping backend" button and a WebSocket smoke test.
// Real UI (blob, chat panel, settings) is built in Weeks 2-3.
import { useEffect, useRef, useState } from "react";

import { API_BASE } from "./api/config";
import { connectVoiceWS } from "./websocket/client";

function App() {
  // Holds the /health result + the X-Request-ID we read off the response header.
  const [ping, setPing] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Open the voice WebSocket once on mount, log inbound frames, close on unmount.
  // Note: React 18 StrictMode double-invokes effects in dev, so you'll see the
  // socket connect → disconnect → reconnect (two "connected" logs). That's expected
  // dev behaviour — the cleanup below handles teardown correctly.
  useEffect(() => {
    const ws = connectVoiceWS();
    wsRef.current = ws;
    ws.onmessage = (ev) => console.log("WS frame:", ev.data);
    ws.onopen = () => console.log("WS open");
    return () => ws.close();
  }, []);

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

  return (
    <div className="flex flex-col items-center justify-center h-screen gap-4">
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
  );
}

export default App;
