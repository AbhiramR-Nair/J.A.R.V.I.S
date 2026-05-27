import { Mic } from "lucide-react";
import { useEffect, useRef } from "react";

import type { VoiceStateLiteral } from "../hooks/useWebSocket";
import { HudFrame } from "./HudFrame";

interface StatusBarProps {
  voiceState: VoiceStateLiteral;
  amplitudeRef: { current: number };
}

// Per-state visual config: accent color (text + mic icon), and operational subtext.
const STATE_CONFIG: Record<VoiceStateLiteral, { color: string; subtext: string }> = {
  idle:         { color: "#7CD4E8",               subtext: "READY · ALT+SPACE"    },
  listening:    { color: "#9FE1CB",               subtext: "MIC OPEN · GROQ STT"  },
  transcribing: { color: "#9FE1CB",               subtext: "TRANSCRIBING…"         },
  thinking:     { color: "#B5D4F4",               subtext: "GEMINI 2.5 FLASH"      },
  speaking:     { color: "#C0DD97",               subtext: "TTS · PIPER"           },
  muted:        { color: "rgba(229,240,245,0.4)", subtext: "MUTED · CTRL+ALT+J"    },
  error:        { color: "#F09595",               subtext: "ERROR · RETRY IN 3s"   },
};

const BAR_COUNT = 7;
const BAR_MIN_H = 3;   // px — resting height for all bars
const BAR_MAX_AMP = 11; // px of extra height at amplitude=1

export function StatusBar({ voiceState, amplitudeRef }: StatusBarProps) {
  // One ref per waveform bar. DOM writes happen directly in the RAF loop for
  // the same reason as the blob — avoids React re-render overhead at 60 fps.
  const barRefs = useRef<(HTMLDivElement | null)[]>([]);
  // Store voiceState in a ref so the RAF closure reads the latest value without
  // needing to be recreated on every state change.
  const voiceStateRef = useRef(voiceState);
  voiceStateRef.current = voiceState;

  // RAF loop: reads amplitudeRef + voiceStateRef each frame, writes bar heights.
  // Shares amplitudeRef with the blob — two consumers, one ref, no coordination needed.
  useEffect(() => {
    let rafId: number;

    function tick() {
      const amp = amplitudeRef.current;
      const isAudioActive =
        voiceStateRef.current === "listening" ||
        voiceStateRef.current === "speaking";

      for (let i = 0; i < BAR_COUNT; i++) {
        const el = barRefs.current[i];
        if (!el) continue;
        // Center-weighted: center bar (i=3) responds fully, edge bars at 60%.
        const centerDist = Math.abs(i - 3) / 3;
        const factor = 1 - centerDist * 0.4;
        const h = isAudioActive
          ? Math.max(BAR_MIN_H, BAR_MIN_H + amp * BAR_MAX_AMP * factor)
          : BAR_MIN_H;
        el.style.height = `${h}px`;
      }

      rafId = requestAnimationFrame(tick);
    }

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [amplitudeRef]);

  const { color, subtext } = STATE_CONFIG[voiceState];

  return (
    <HudFrame padding="6px 10px">
      <div className="flex items-center gap-2">
        {/* Mic icon — color tracks accent */}
        <Mic size={14} style={{ color, flexShrink: 0 }} />

        {/* Waveform: 7 bars driven by the RAF loop above */}
        <div className="flex items-end gap-px" style={{ height: 14 }}>
          {Array.from({ length: BAR_COUNT }).map((_, i) => (
            <div
              key={i}
              ref={(el) => { barRefs.current[i] = el; }}
              style={{
                width: 2,
                height: BAR_MIN_H,
                background: color,
                borderRadius: 1,
                opacity: 0.7,
                transition: "background 0.3s",
              }}
            />
          ))}
        </div>

        {/* Right cluster: state label + subtext */}
        <div className="flex flex-col items-end ml-auto">
          <span
            style={{ color, letterSpacing: "0.18em", fontSize: 10 }}
            className="font-mono uppercase"
          >
            ● {voiceState}
          </span>
          <span
            style={{ color, letterSpacing: "0.10em", fontSize: 9, opacity: 0.4 }}
            className="font-mono uppercase"
          >
            {subtext}
          </span>
        </div>
      </div>
    </HudFrame>
  );
}
