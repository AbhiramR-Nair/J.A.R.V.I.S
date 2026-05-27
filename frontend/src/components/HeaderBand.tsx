import { Settings, X } from "lucide-react";

interface HeaderBandProps {
  onToggleSettings: () => void;
  onClose: () => void;
  /** Dot color reflecting connection state. Forward-prepared; defaults to cyan today. */
  statusDotColor?: string;
}

export function HeaderBand({
  onToggleSettings,
  onClose,
  statusDotColor = "#7CD4E8",
}: HeaderBandProps) {
  // The entire band is a native drag handle via WebkitAppRegion.
  // Buttons override with "no-drag" so their click events aren't swallowed.
  const bandStyle: React.CSSProperties = {
    WebkitAppRegion: "drag",
    borderBottom: "1px solid rgba(124, 212, 232, 0.10)",
  } as React.CSSProperties;

  const noDragBtn: React.CSSProperties = {
    WebkitAppRegion: "no-drag",
  } as React.CSSProperties;

  return (
    <div
      style={bandStyle}
      className="h-8 w-full flex items-center justify-between px-3 shrink-0"
    >
      {/* Left: status dot + identity string */}
      <div className="flex items-center gap-2">
        <div
          style={{ width: 6, height: 6, borderRadius: "50%", background: statusDotColor }}
        />
        <span
          style={{ color: "#7CD4E8", letterSpacing: "0.18em", fontSize: 11 }}
          className="font-mono uppercase select-none"
        >
          Jarvis // MK 0
        </span>
      </div>

      {/* Right: gear + close buttons */}
      <div className="flex items-center gap-1">
        <button
          style={noDragBtn}
          onClick={onToggleSettings}
          className="w-6 h-6 flex items-center justify-center rounded text-cyan-400/50 hover:text-cyan-300 hover:bg-white/10 transition-colors"
          aria-label="Toggle settings"
        >
          <Settings size={14} />
        </button>
        <button
          style={noDragBtn}
          onClick={onClose}
          className="w-6 h-6 flex items-center justify-center rounded text-cyan-400/50 hover:text-cyan-300 hover:bg-white/10 transition-colors"
          aria-label="Close"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
