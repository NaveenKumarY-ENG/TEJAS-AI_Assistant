import { type CoreState } from "../../store/assistantStore";

const STATUS_TEXT: Record<CoreState, string> = {
  idle: "CORE ONLINE",
  listening: "LISTENING",
  processing: "PROCESSING",
  thinking: "THINKING",
  searching: "SEARCHING",
  speaking: "RESPONDING",
  error: "ERROR",
};

// Violet = neutral/system state (matches the dashboard's new primary
// accent), orange = AI activity, red = error only.
const STATUS_COLOR: Record<CoreState, string> = {
  idle: "#8b5cf6",
  listening: "#8b5cf6",
  processing: "#8b5cf6",
  thinking: "#ff8a2e",
  searching: "#ff8a2e",
  speaking: "#ff8a2e",
  error: "#ff3b3b",
};

/** Small textual status readout — HUDGlyphs is purely decorative symbols
 *  and has no status text, this fills that gap. */
export function HUDStatus({ coreState }: { coreState: CoreState }) {
  const color = STATUS_COLOR[coreState];
  return (
    <div className="pointer-events-none absolute left-5 top-5" aria-hidden="true">
      <span className="mono flex items-center gap-1.5 text-[11px] tracking-wider" style={{ color }}>
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{
            background: color,
            boxShadow: `0 0 6px ${color}`,
            animation: coreState !== "idle" ? "breathe 1.2s ease-in-out infinite" : undefined,
          }}
        />
        {coreState === "error" ? "⚠ " : "● "}
        {STATUS_TEXT[coreState]}
      </span>
    </div>
  );
}
