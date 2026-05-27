import { motion } from "framer-motion";
import { Settings } from "lucide-react";
import { useEffect, useState } from "react";

import { API_BASE } from "../api/config";
import { HudFrame } from "./HudFrame";

interface DeviceInfo {
  index: number;
  name: string;
  channels: number;
}

interface ProjectInfo {
  id: number;
  name: string;
  is_active: boolean;
}

interface TestMicResult {
  success: boolean;
  peak_amplitude: number;
  duration_ms: number;
  error: string | null;
}

type TestPhase = "idle" | "testing" | "done";

interface SettingsPanelProps {
  onClose: () => void;
}

// Mono uppercase label + right-aligned value — the HUD row pattern used throughout.
function HudRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1">
      <span
        style={{ letterSpacing: "0.10em", fontSize: 10, color: "rgba(124,212,232,0.5)" }}
        className="font-mono uppercase shrink-0"
      >
        {label}
      </span>
      <div style={{ fontSize: 10 }} className="font-mono text-cyan-200">
        {children}
      </div>
    </div>
  );
}

export function SettingsPanel({ onClose: _onClose }: SettingsPanelProps) {
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [activeProject, setActiveProject] = useState<string>("general");

  const [phase, setPhase] = useState<TestPhase>("idle");
  const [result, setResult] = useState<TestMicResult | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Fetch device list, current device, and project list when the panel mounts.
  useEffect(() => {
    async function loadSettings() {
      try {
        const [devListRes, devCurrentRes, projectsRes] = await Promise.all([
          fetch(`${API_BASE}/audio/devices`),
          fetch(`${API_BASE}/audio/device`),
          fetch(`${API_BASE}/projects`),
        ]);
        const devList: DeviceInfo[] = await devListRes.json();
        const devCurrent: DeviceInfo | null = await devCurrentRes.json();
        const projectList: ProjectInfo[] = await projectsRes.json();

        setDevices(devList);
        if (devCurrent !== null) setActiveIndex(devCurrent.index);
        else if (devList.length > 0) setActiveIndex(devList[0].index);

        setProjects(projectList);
        const active = projectList.find((p) => p.is_active);
        if (active) setActiveProject(active.name);
      } catch {
        // Non-fatal — dropdowns will be empty; user can still use the panel.
      }
    }
    loadSettings();
  }, []);

  async function handleProjectChange(name: string) {
    setActiveProject(name);
    try {
      await fetch(`${API_BASE}/projects/active`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
    } catch {
      // Optimistic update — backend will persist on next successful call.
    }
  }

  async function handleDeviceChange(index: number) {
    setActiveIndex(index);
    try {
      await fetch(`${API_BASE}/audio/device`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index }),
      });
    } catch {
      // Optimistic update — device index is already set in local state.
      // On next open the backend will return the actual saved value.
    }
  }

  async function runMicTest() {
    setPhase("testing");
    setResult(null);
    setFetchError(null);
    try {
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
      setResult(await res.json());
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Network error.");
    } finally {
      setPhase("done");
    }
  }

  function MicTestResult() {
    if (fetchError) return <span style={{ color: "#F09595" }}>✗ {fetchError}</span>;
    if (!result) return null;
    if (!result.success) return <span style={{ color: "#F09595" }}>✗ {result.error}</span>;
    if (result.peak_amplitude < 0.05) {
      return <span style={{ color: "#F0D090" }}>⚠ Silent (peak: {result.peak_amplitude.toFixed(3)})</span>;
    }
    return <span style={{ color: "#9FE1CB" }}>✓ peak: {result.peak_amplitude.toFixed(3)}</span>;
  }

  const SECTIONS = 3; // MIC DEVICE, PROJECT, HOTKEY

  return (
    // motion.div is the direct child of AnimatePresence in App.tsx — required for
    // exit animation to fire on unmount. Without this being a motion component,
    // AnimatePresence cannot intercept the unmount and the exit lerp never runs.
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 4 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="w-full max-w-xs"
    >
      <HudFrame>
        {/* Panel header row */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <Settings size={10} style={{ color: "rgba(124,212,232,0.5)" }} />
            <span
              style={{ letterSpacing: "0.18em", fontSize: 10, color: "rgba(124,212,232,0.7)" }}
              className="font-mono uppercase"
            >
              Settings
            </span>
          </div>
          <span
            style={{ letterSpacing: "0.10em", fontSize: 9, color: "rgba(124,212,232,0.35)" }}
            className="font-mono"
          >
            [{SECTIONS} ITEMS]
          </span>
        </div>

        {/* MIC DEVICE row */}
        <HudRow label="Mic Device">
          <select
            value={activeIndex ?? ""}
            onChange={(e) => handleDeviceChange(Number(e.target.value))}
            style={{
              background: "rgba(0,0,0,0.4)",
              border: "1px solid rgba(124,212,232,0.2)",
              color: "#7CD4E8",
              fontSize: 10,
              fontFamily: "monospace",
              borderRadius: 2,
              padding: "1px 4px",
              maxWidth: 160,
            }}
          >
            {devices.map((d) => (
              <option key={d.index} value={d.index}>
                {d.name}
              </option>
            ))}
          </select>
        </HudRow>

        {/* PROJECT row */}
        <HudRow label="Project">
          <select
            value={activeProject}
            onChange={(e) => handleProjectChange(e.target.value)}
            style={{
              background: "rgba(0,0,0,0.4)",
              border: "1px solid rgba(124,212,232,0.2)",
              color: "#9FE1CB",
              fontSize: 10,
              fontFamily: "monospace",
              borderRadius: 2,
              padding: "1px 4px",
              maxWidth: 160,
            }}
          >
            {projects.map((p) => (
              <option key={p.id} value={p.name}>
                {p.name}
              </option>
            ))}
          </select>
        </HudRow>

        {/* HOTKEY row — display only */}
        <HudRow label="Hotkey">
          <span>ALT + SPACE</span>
        </HudRow>

        {/* Divider */}
        <div style={{ borderTop: "1px solid rgba(124,212,232,0.08)", margin: "8px 0" }} />

        {/* Mic test */}
        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={runMicTest}
            disabled={phase === "testing"}
            style={{
              border: "1px solid rgba(124,212,232,0.3)",
              color: phase === "testing" ? "rgba(124,212,232,0.4)" : "#7CD4E8",
              fontSize: 10,
              fontFamily: "monospace",
              letterSpacing: "0.10em",
              background: "transparent",
              padding: "3px 8px",
              borderRadius: 2,
              cursor: phase === "testing" ? "not-allowed" : "pointer",
            }}
          >
            {phase === "testing" ? "RECORDING…" : "TEST MIC"}
          </button>
          {phase === "done" && (
            <span style={{ fontSize: 10, fontFamily: "monospace" }}>
              <MicTestResult />
            </span>
          )}
        </div>
      </HudFrame>
    </motion.div>
  );
}
