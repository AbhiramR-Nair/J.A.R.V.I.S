// Pure data file — no React, no Framer Motion.
// Blob.tsx reads these configs each frame; tuning lives here, not in the component.
// Day 16 morning: adjust per-state values after visual review.

import type { VoiceStateLiteral } from "../hooks/useWebSocket";

// The 6 visual states the blob renders. `transcribing` is collapsed to `thinking`
// because they share the same visual character (slow, introspective).
export type VisualStateLiteral =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "muted"
  | "error";

export interface BlobStateConfig {
  // --- Path morphing ---
  // Controls the animated border-radius silhouette.
  morphIntensity: number;  // 0–15; how far each corner oscillates from 50%
  morphSpeed: number;      // multiplier on the sin() timebase; 1.0 = baseline cadence

  // --- Conic gradient (color turn) ---
  // The rotating blurred layer behind the radial regions.
  conicSpeed: number;      // degrees advanced per animation frame
  conicColors: string[];   // hex stops, rendered as a conic-gradient and looped
  conicBlur: number;       // px blur applied to the conic layer; higher = softer bands

  // --- Radial gradient overlays ---
  // Three soft color regions at fixed positions inside the blob (mix-blend-mode: screen).
  radialColors: [string, string, string];

  // --- Specular highlight ---
  // Small drifting soft-white circle; gives the orb a polished/material feel.
  highlightOpacity: number;  // 0–1
  highlightSpeed: number;    // path traversal speed along the Lissajous curve

  // --- Grain overlay ---
  // Static feTurbulence SVG filter; paper/watercolor texture.
  grainOpacity: number;  // 0–1

  // --- Overall ---
  scale: number;    // CSS transform scale on the outer wrapper
  opacity: number;  // overall opacity; muted state uses ~0.4 to dim the orb
}

// ---------------------------------------------------------------------------
// Per-state configs — first-pass values. All palettes sampled from the
// ElevenLabs orb reference video. Tune on Day 16 after visual review.
// ---------------------------------------------------------------------------

export const BLOB_STATES: Record<VisualStateLiteral, BlobStateConfig> = {
  idle: {
    morphIntensity: 10,
    morphSpeed: 1.0,
    conicSpeed: 1.0,
    conicColors: ["#B5E8F8", "#3D8EAD", "#1F6D86", "#2F4B2F", "#507A59", "#B5E8F8"],
    conicBlur: 28,
    radialColors: ["#5BACC8", "#3D8EAD", "#2F4B2F"],
    highlightOpacity: 0.45,
    highlightSpeed: 0.005,
    grainOpacity: 0.65,
    scale: 1.0,
    opacity: 1.0,
  },

  listening: {
    // Brighter, more saturated version of idle — the orb "wakes up".
    morphIntensity: 12,
    morphSpeed: 1.5,
    conicSpeed: 1.5,
    conicColors: ["#C8F0FF", "#4BBCE8", "#2D9DC8", "#1F6D86", "#3D8EAD", "#C8F0FF"],
    conicBlur: 24,
    radialColors: ["#4BBCE8", "#2D9DC8", "#1F8070"],
    highlightOpacity: 0.55,
    highlightSpeed: 0.007,
    grainOpacity: 0.65,
    scale: 1.05,
    opacity: 1.0,
  },

  thinking: {
    // Slower, deeper, indigo-leaning — the orb is concentrating.
    morphIntensity: 8,
    morphSpeed: 0.6,
    conicSpeed: 0.4,
    conicColors: ["#3D4A8A", "#2D3A7A", "#1D2A6A", "#3D3D8A", "#2D2D7A", "#3D4A8A"],
    conicBlur: 32,
    radialColors: ["#3D4A8A", "#2D3A7A", "#1D2A6A"],
    highlightOpacity: 0.3,
    highlightSpeed: 0.003,
    grainOpacity: 0.7,
    scale: 0.98,
    opacity: 1.0,
  },

  speaking: {
    // Idle palette with a brighter highlight. Edge deformation added Day 16.
    morphIntensity: 11,
    morphSpeed: 1.2,
    conicSpeed: 1.2,
    conicColors: ["#C0EEFF", "#4AB8D8", "#2F96B8", "#1F6D86", "#2F4B2F", "#C0EEFF"],
    conicBlur: 26,
    radialColors: ["#5BACC8", "#4BACC8", "#2F5B3F"],
    highlightOpacity: 0.55,
    highlightSpeed: 0.006,
    grainOpacity: 0.6,
    scale: 1.02,
    opacity: 1.0,
  },

  muted: {
    // Desaturated grayscale of idle — visibly dimmed and still.
    morphIntensity: 6,
    morphSpeed: 0.4,
    conicSpeed: 0.3,
    conicColors: ["#606878", "#4A5668", "#384858", "#2A3848", "#384858", "#606878"],
    conicBlur: 30,
    radialColors: ["#606878", "#4A5668", "#384858"],
    highlightOpacity: 0.2,
    highlightSpeed: 0.002,
    grainOpacity: 0.5,
    scale: 0.92,
    opacity: 0.4,
  },

  error: {
    // Reds and burnt oranges; agitated morphing. Auto-recovers to idle after 3s.
    morphIntensity: 14,
    morphSpeed: 2.0,
    conicSpeed: 2.5,
    conicColors: ["#D45020", "#B83010", "#C84020", "#A02000", "#C85030", "#D45020"],
    conicBlur: 22,
    radialColors: ["#D45020", "#B83010", "#A83020"],
    highlightOpacity: 0.5,
    highlightSpeed: 0.01,
    grainOpacity: 0.65,
    scale: 1.0,
    opacity: 1.0,
  },
};

// Maps the 7 backend VoiceStateLiteral values to the 6 visual states.
// `transcribing` shares the `thinking` visual (same character: slow, inward).
export function mapVoiceStateToVisualState(vs: VoiceStateLiteral): VisualStateLiteral {
  if (vs === "transcribing") return "thinking";
  return vs as VisualStateLiteral;
}
