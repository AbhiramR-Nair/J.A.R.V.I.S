// Settings panel — Day 12: mic test button.
// Day 17 will add device picker, voice selector, project switcher, and move
// this into a proper slide-out drawer. For now it renders inline below the blob.

import { useState } from "react";

import { API_BASE } from "../api/config";

interface TestMicResult {
  success: boolean;
  peak_amplitude: number;
  duration_ms: number;
  error: string | null;
}

type TestPhase = "idle" | "testing" | "done";

export function SettingsPanel() {
  const [phase, setPhase] = useState<TestPhase>("idle");
  const [result, setResult] = useState<TestMicResult | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  async function runMicTest() {
    setPhase("testing");
    setResult(null);
    setFetchError(null);

    try {
      // Endpoint takes ~6s (3s record + ~3s playback) — intentionally long.
      const res = await fetch(`${API_BASE}/audio/test-mic`, { method: "POST" });

      if (res.status === 409) {
        setFetchError("Cannot test mic while a conversation is active.");
        setPhase("done");
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setFetchError((body as { detail?: string }).detail ?? "Test failed.");
        setPhase("done");
        return;
      }

      const data: TestMicResult = await res.json();
      setResult(data);
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Network error.");
    } finally {
      setPhase("done");
    }
  }

  function ResultBadge() {
    if (fetchError) {
      return <span className="text-red-400">✗ {fetchError}</span>;
    }
    if (!result) return null;
    if (!result.success) {
      return <span className="text-red-400">✗ {result.error}</span>;
    }
    if (result.peak_amplitude < 0.05) {
      return (
        <span className="text-yellow-400">
          ⚠ Silent — check mic (peak: {result.peak_amplitude.toFixed(3)})
        </span>
      );
    }
    return (
      <span className="text-green-400">
        ✓ Working — peak: {result.peak_amplitude.toFixed(3)}
      </span>
    );
  }

  return (
    <div className="bg-black/30 backdrop-blur-sm rounded-lg px-4 py-3 font-mono text-xs w-full max-w-sm">
      <div className="text-cyan-400 text-sm mb-2">Settings</div>
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={runMicTest}
          disabled={phase === "testing"}
          className="bg-cyan-700/40 hover:bg-cyan-700/60 disabled:opacity-50 disabled:cursor-not-allowed text-cyan-100 px-3 py-1.5 rounded"
        >
          {phase === "testing" ? "Recording…" : "Test mic"}
        </button>
        {phase === "done" && <ResultBadge />}
      </div>
    </div>
  );
}
