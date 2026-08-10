import { useMemo } from "react";
import type { CoreState } from "../../store/assistantStore";

const SYMBOLS = ["01", "10", "FF", "0A", "Δ", "Σ", "λ", "∴", "⌬", "◈", "01", "AE", "7F", "∇", "◇", "10"];

interface Glyph {
  xPct: number;
  yPct: number;
  symbol: string;
  delay: number;
  duration: number;
}

/** Layer 5: tiny binary/glyph/HUD fragments scattered across the core's
 *  full-bleed backdrop, flickering independently. */
export function HUDGlyphs({ count = 22, coreState }: { count?: number; coreState: CoreState }) {
  const glyphs = useMemo<Glyph[]>(
    () =>
      Array.from({ length: count }, () => ({
        xPct: 6 + Math.random() * 88,
        yPct: 8 + Math.random() * 84,
        symbol: SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)],
        delay: Math.random() * 3,
        duration: 1.8 + Math.random() * 2.2,
      })),
    [count]
  );

  const active = coreState === "thinking" || coreState === "speaking";

  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden="true">
      {glyphs.map((g, i) => (
        <span
          key={i}
          className="mono absolute text-[10px] tracking-wider"
          style={{
            left: `${g.xPct}%`,
            top: `${g.yPct}%`,
            transform: "translate(-50%, -50%)",
            color: i % 3 === 0 ? "#ffd699" : "#ff8a2e",
            opacity: 0,
            animation: `hud-flicker ${g.duration}s ease-in-out ${g.delay}s infinite`,
            filter: active ? "brightness(1.4)" : "none",
          }}
        >
          {g.symbol}
        </span>
      ))}
    </div>
  );
}
