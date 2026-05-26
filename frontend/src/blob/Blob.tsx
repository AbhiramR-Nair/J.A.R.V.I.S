// Blob.tsx — the animated orb that is J.A.R.V.I.S's visual face.
//
// Two animation layers cooperate:
//   1. Framer Motion animate(): smoothly lerps colors/scale/opacity between states (~600ms).
//      Uses useMotionValue internally — no DOM side effects, just updates MotionValue state.
//   2. requestAnimationFrame loop: continuous per-frame DOM writes (morph, conic rotation,
//      specular drift). Reads .get() on the motion values each frame, so both systems
//      cooperate without conflicts — Framer Motion owns the numbers, rAF owns the DOM.
//
// Layer order (back → front): conic gradient → radial regions → specular → grain

import { useCallback, useEffect, useRef } from "react";
import { animate, useMotionValue } from "framer-motion";

import type { VoiceStateLiteral } from "../hooks/useWebSocket";
import { BLOB_STATES, mapVoiceStateToVisualState } from "./BlobStates";
import type { BlobStateConfig } from "./BlobStates";

interface BlobProps {
  voiceState: VoiceStateLiteral;
  size?: number;  // diameter in px; default 180
}

export function Blob({ voiceState, size = 180 }: BlobProps) {
  // --- DOM refs: the rAF loop writes directly to these elements ---
  const wrapperRef   = useRef<HTMLDivElement>(null);
  const conicRef     = useRef<HTMLDivElement>(null);
  const radialRef    = useRef<HTMLDivElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);
  const grainRef     = useRef<SVGSVGElement>(null);

  // Accumulated conic rotation angle in degrees, persisted across frames
  const conicAngleRef = useRef(0);

  // Current config for the rAF loop. Updated on state change so the loop
  // always uses fresh morph/speed params without needing them in its deps.
  const configRef = useRef<BlobStateConfig>(BLOB_STATES.idle);

  // --- Motion values: Framer Motion lerps these; rAF reads .get() each frame ---
  const idle = BLOB_STATES.idle;

  const mvScale        = useMotionValue(idle.scale);
  const mvOpacity      = useMotionValue(idle.opacity);
  const mvGrainOp      = useMotionValue(idle.grainOpacity);
  const mvHighlightOp  = useMotionValue(idle.highlightOpacity);
  const mvConicBlur    = useMotionValue(idle.conicBlur);

  // Three radial region colors
  const mvR0 = useMotionValue(idle.radialColors[0]);
  const mvR1 = useMotionValue(idle.radialColors[1]);
  const mvR2 = useMotionValue(idle.radialColors[2]);

  // Six conic gradient stops (all states use ≤6; shorter arrays repeat last stop)
  const mvC0 = useMotionValue(idle.conicColors[0]);
  const mvC1 = useMotionValue(idle.conicColors[1]);
  const mvC2 = useMotionValue(idle.conicColors[2]);
  const mvC3 = useMotionValue(idle.conicColors[3]);
  const mvC4 = useMotionValue(idle.conicColors[4]);
  const mvC5 = useMotionValue(idle.conicColors[5]);

  // Animate all motion values toward a new config over 600ms.
  // useCallback with stable motion-value refs as deps — created only once.
  const animateTo = useCallback((cfg: BlobStateConfig) => {
    configRef.current = cfg;
    const T = { duration: 0.6, ease: "easeInOut" } as const;

    animate(mvScale,       cfg.scale,            T);
    animate(mvOpacity,     cfg.opacity,          T);
    animate(mvGrainOp,     cfg.grainOpacity,     T);
    animate(mvHighlightOp, cfg.highlightOpacity, T);
    animate(mvConicBlur,   cfg.conicBlur,        T);

    animate(mvR0, cfg.radialColors[0], T);
    animate(mvR1, cfg.radialColors[1], T);
    animate(mvR2, cfg.radialColors[2], T);

    // Pad conic stops to 6: if a state has fewer, repeat the last stop
    const c = cfg.conicColors;
    animate(mvC0, c[0],        T);
    animate(mvC1, c[1] ?? c[0], T);
    animate(mvC2, c[2] ?? c[0], T);
    animate(mvC3, c[3] ?? c[0], T);
    animate(mvC4, c[4] ?? c[0], T);
    animate(mvC5, c[5] ?? c[0], T);
  }, [
    mvScale, mvOpacity, mvGrainOp, mvHighlightOp, mvConicBlur,
    mvR0, mvR1, mvR2, mvC0, mvC1, mvC2, mvC3, mvC4, mvC5,
  ]);

  // On voice state change: transition to new config.
  // Error state auto-recovers to idle visually after 3s (errors are transient).
  useEffect(() => {
    const visual = mapVoiceStateToVisualState(voiceState);
    animateTo(BLOB_STATES[visual]);

    if (visual !== "error") return;
    const timer = setTimeout(() => animateTo(BLOB_STATES.idle), 3000);
    return () => clearTimeout(timer);
  }, [voiceState, animateTo]);

  // rAF loop: continuous per-frame DOM updates.
  // Empty deps: starts once on mount, cleaned up on unmount.
  // Motion values are stable refs — reading .get() here is safe with no stale closure.
  useEffect(() => {
    let rafId: number;
    let frame = 0;

    function tick() {
      frame++;
      const cfg = configRef.current;

      // --- Border-radius morphing ---
      // Must use the full 8-value form (A% B% C% D% / E% F% G% H%) — browsers
      // cannot interpolate between mixed or 4-value forms smoothly.
      // Different phases per corner prevent a pulsing-circle look.
      const t = frame * 0.016 * cfg.morphSpeed;
      const i = cfg.morphIntensity;
      const v = [
        50 + i * Math.sin(t * 1.00),
        50 + i * Math.cos(t * 0.90),
        50 + i * Math.sin(t * 0.80 + 1.0),
        50 + i * Math.cos(t * 1.10 + 0.5),
        50 + i * Math.cos(t * 0.70),
        50 + i * Math.sin(t * 1.20),
        50 + i * Math.cos(t * 0.85 + 0.8),
        50 + i * Math.sin(t * 0.95 + 1.2),
      ].map(n => `${Math.max(30, Math.min(70, n)).toFixed(1)}%`);

      if (wrapperRef.current) {
        wrapperRef.current.style.borderRadius =
          `${v[0]} ${v[1]} ${v[2]} ${v[3]} / ${v[4]} ${v[5]} ${v[6]} ${v[7]}`;
        wrapperRef.current.style.transform = `scale(${mvScale.get().toFixed(3)})`;
        wrapperRef.current.style.opacity   = mvOpacity.get().toFixed(3);
      }

      // --- Conic gradient layer ---
      conicAngleRef.current += cfg.conicSpeed;
      if (conicRef.current) {
        const stops = [mvC0, mvC1, mvC2, mvC3, mvC4, mvC5].map(m => m.get()).join(", ");
        conicRef.current.style.background =
          `conic-gradient(from ${conicAngleRef.current.toFixed(1)}deg, ${stops}, ${mvC0.get()})`;
        conicRef.current.style.filter = `blur(${mvConicBlur.get().toFixed(1)}px)`;
      }

      // --- Radial gradient layer (three watercolor regions) ---
      if (radialRef.current) {
        radialRef.current.style.background = [
          `radial-gradient(circle at 30% 35%, ${mvR0.get()} 0%, transparent 55%)`,
          `radial-gradient(circle at 70% 60%, ${mvR1.get()} 0%, transparent 55%)`,
          `radial-gradient(circle at 50% 80%, ${mvR2.get()} 0%, transparent 50%)`,
        ].join(", ");
      }

      // --- Specular highlight (Lissajous drift) ---
      // Coprime period ratio (1 : 0.7) means x and y never complete a cycle at the
      // same time, so the path never obviously repeats over human-observable timescales.
      const hs = frame * cfg.highlightSpeed;
      const hx = (50 + 30 * Math.cos(hs)).toFixed(1);
      const hy = (50 + 25 * Math.sin(hs * 0.7)).toFixed(1);
      if (highlightRef.current) {
        highlightRef.current.style.background =
          `radial-gradient(circle at ${hx}% ${hy}%, ` +
          `rgba(255,255,255,${mvHighlightOp.get().toFixed(2)}) 0%, transparent 35%)`;
      }

      // --- Grain opacity ---
      if (grainRef.current) {
        grainRef.current.style.opacity = mvGrainOp.get().toFixed(2);
      }

      rafId = requestAnimationFrame(tick);
    }

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Motion values are stable MotionValue refs — safe to read in rAF without listing as deps.

  return (
    // Outer wrapper: clipping boundary for all layers. border-radius written by rAF.
    // WebkitAppRegion no-drag: leaves room for a future tap-to-mute gesture (Day 17).
    <div
      ref={wrapperRef}
      style={{
        width: size,
        height: size,
        position: "relative",
        overflow: "hidden",
        flexShrink: 0,
        borderRadius: "50%",  // initial value; overwritten by rAF on first frame
        WebkitAppRegion: "no-drag",
      } as React.CSSProperties}
    >
      {/* Layer 1 (back): rotating conic gradient — the main color wash */}
      <div
        ref={conicRef}
        style={{
          position: "absolute",
          inset: -30,       // oversized so the blurred edge doesn't show a hard clip
          borderRadius: "50%",
          pointerEvents: "none",
        }}
      />

      {/* Layer 2: radial color regions — watercolor depth and warmth */}
      <div
        ref={radialRef}
        style={{
          position: "absolute",
          inset: 0,
          mixBlendMode: "screen",
          pointerEvents: "none",
        } as React.CSSProperties}
      />

      {/* Layer 3: specular highlight — drifting catch-light for a polished-glass feel */}
      <div
        ref={highlightRef}
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
        }}
      />

      {/* Layer 4 (front): grain overlay — SVG feTurbulence for watercolor/paper texture */}
      <svg
        ref={grainRef}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          mixBlendMode: "overlay",
          pointerEvents: "none",
        } as React.CSSProperties}
      >
        <filter id="blob-grain">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.75"
            numOctaves="3"
            stitchTiles="stitch"
          />
          {/* Desaturate the turbulence noise to a neutral gray before overlaying */}
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter="url(#blob-grain)" />
      </svg>
    </div>
  );
}
