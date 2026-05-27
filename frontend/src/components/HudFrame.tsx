import type { ReactNode } from "react";

interface HudFrameProps {
  children: ReactNode;
  /** Tint of the L-shaped corner brackets. */
  cornerColor?: string;
  /** Internal padding applied to the content wrapper. */
  padding?: string;
  /** Optional background wash behind the content. */
  background?: string;
  /** Layout/spacing className passed through from the parent. */
  className?: string;
}

// Shared style for all four corner markers. Each corner overrides only the
// two border sides that form its L-shape; the other two are set to "none".
const CORNER_SIZE = 8;
const CORNER_BASE: React.CSSProperties = {
  position: "absolute",
  width: CORNER_SIZE,
  height: CORNER_SIZE,
  // borderStyle must be explicit — browsers default to "none" and the corner
  // disappears entirely without this line.
  borderStyle: "solid",
  borderWidth: 0,
};

export function HudFrame({
  children,
  cornerColor = "rgba(124, 212, 232, 0.5)",
  padding = "10px 12px",
  background = "rgba(124, 212, 232, 0.03)",
  className = "",
}: HudFrameProps) {
  const b = `1px solid ${cornerColor}`;

  return (
    <div
      style={{ position: "relative", background, padding }}
      className={className}
    >
      {/* Top-left */}
      <div style={{ ...CORNER_BASE, top: 0, left: 0, borderTop: b, borderLeft: b }} />
      {/* Top-right */}
      <div style={{ ...CORNER_BASE, top: 0, right: 0, borderTop: b, borderRight: b }} />
      {/* Bottom-left */}
      <div style={{ ...CORNER_BASE, bottom: 0, left: 0, borderBottom: b, borderLeft: b }} />
      {/* Bottom-right */}
      <div style={{ ...CORNER_BASE, bottom: 0, right: 0, borderBottom: b, borderRight: b }} />

      {children}
    </div>
  );
}
