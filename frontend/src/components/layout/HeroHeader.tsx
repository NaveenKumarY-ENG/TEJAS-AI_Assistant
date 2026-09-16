import { useMemo, useState, type ReactNode } from "react";
import { Sparkles } from "lucide-react";
import { useAssistantStore } from "../../store/assistantStore";
import { timeOfDayGreeting } from "../../utils/greeting";

/** The Home page's cinematic welcome banner — TopBar's old plain greeting
 *  row, upgraded to a glass "hero" card with a deep-space background (no
 *  image asset: CSS gradients/masks for the sky/glow/ring, a feTurbulence
 *  "terrain" texture on the planet, animated meteors/a comet, and glowing
 *  clouds that occasionally flash with lightning — see index.css's
 *  matching comments for how each is built). Only rendered when TopBar has
 *  no `header` override (i.e. on Home) — every other page's compact title
 *  row is untouched. `controls` (model/voice selectors, mute,
 *  notifications) is TopBar's own markup, passed in and rendered inside
 *  this card rather than duplicated — same controls, same behavior, just
 *  one shared glass surface with the greeting instead of two stacked
 *  bars. */
export function HeroHeader({ controls }: { controls: ReactNode }) {
  const assistantName = useAssistantStore((s) => s.assistantName);
  const connection = useAssistantStore((s) => s.connection);
  const greeting = useMemo(() => timeOfDayGreeting(), []);
  const [imgFailed, setImgFailed] = useState(false);

  const online = connection === "online";

  return (
    <section className="hero relative overflow-hidden rounded-2xl border border-white/[0.08] shadow-[0_0_40px_-16px_color-mix(in_srgb,var(--color-primary)_45%,transparent)] backdrop-blur-2xl">
      {/* Decorative layers only — never intercept clicks, never read by a screen reader. */}
      <div className="hero-glow pointer-events-none absolute inset-0 z-0" aria-hidden="true" />
      <div className="hero-moon pointer-events-none absolute z-0" aria-hidden="true" />
      {/* The planet: .hero-planet is a proper lit-hemisphere gradient (a
          large directional light falloff, not just a thin rim), and the
          SVG on top adds a procedural "terrain" texture via feTurbulence —
          real per-pixel noise shaped into continent-like mottling, still
          zero external image assets. Painted directly after .hero-planet
          so its mix-blend-mode composites against the lit gradient right
          below it, not the sky behind. */}
      <div className="hero-planet pointer-events-none absolute z-0" aria-hidden="true" />
      <svg
        className="hero-planet-texture pointer-events-none absolute z-0"
        viewBox="0 0 220 220"
        aria-hidden="true"
      >
        <defs>
          <filter id="heroPlanetTerrain" x="-20%" y="-20%" width="140%" height="140%">
            <feTurbulence type="fractalNoise" baseFrequency="0.012 0.02" numOctaves="4" seed="7" result="noise" />
            <feColorMatrix
              in="noise"
              type="matrix"
              values="0 0 0 0 0.04  0 0 0 0 0.08  0 0 0 0 0.2  0 0 0 0.85 0"
            />
          </filter>
        </defs>
        <circle cx="110" cy="110" r="110" filter="url(#heroPlanetTerrain)" />
      </svg>
      <div className="hero-ring-glow pointer-events-none absolute z-0" aria-hidden="true" />
      <div className="hero-ring pointer-events-none absolute z-0" aria-hidden="true" />
      <div className="hero-starfield pointer-events-none absolute inset-0 z-0" aria-hidden="true" />
      {/* Meteors + one slower comet — thin streaks with a fading tail,
          animated diagonally across the card on a loop. Each gets its own
          fixed start position/angle/timing (varied via inline style rather
          than more CSS classes, since these are one-off per-instance
          values, not a reusable pattern) so they don't all move in lockstep. */}
      <div
        className="hero-meteor pointer-events-none absolute z-0"
        style={{ top: "8%", left: "18%", animationDelay: "0s", animationDuration: "5.5s" }}
        aria-hidden="true"
      />
      <div
        className="hero-meteor pointer-events-none absolute z-0"
        style={{ top: "22%", left: "48%", animationDelay: "2.4s", animationDuration: "6.5s" }}
        aria-hidden="true"
      />
      <div
        className="hero-meteor pointer-events-none absolute z-0"
        style={{ top: "4%", left: "70%", animationDelay: "4.6s", animationDuration: "5s" }}
        aria-hidden="true"
      />
      <div
        className="hero-comet pointer-events-none absolute z-0"
        style={{ top: "14%", left: "-6%", animationDelay: "1s" }}
        aria-hidden="true"
      />
      {/* Glowing clouds along the bottom, in place of the mountains — soft
          blurred violet/blue blobs, with an occasional lightning flash
          "generated" from inside them (a brief bright flicker plus a jagged
          bolt shape, both on their own looping timers so the two clouds
          don't flash in sync). */}
      <div className="hero-cloud hero-cloud--a pointer-events-none absolute z-0" aria-hidden="true" />
      <div className="hero-cloud hero-cloud--b pointer-events-none absolute z-0" aria-hidden="true" />
      <div className="hero-lightning-flash hero-lightning-flash--a pointer-events-none absolute z-0" aria-hidden="true" />
      <div className="hero-lightning-flash hero-lightning-flash--b pointer-events-none absolute z-0" aria-hidden="true" />
      <svg
        className="hero-lightning-bolt hero-lightning-bolt--a pointer-events-none absolute z-0"
        viewBox="0 0 40 70"
        aria-hidden="true"
      >
        <path d="M18,0 L2,38 L16,38 L8,70 L34,26 L18,26 Z" fill="#eaf1ff" />
      </svg>
      <svg
        className="hero-lightning-bolt hero-lightning-bolt--b pointer-events-none absolute z-0"
        viewBox="0 0 40 70"
        aria-hidden="true"
      >
        <path d="M18,0 L2,38 L16,38 L8,70 L34,26 L18,26 Z" fill="#eaf1ff" />
      </svg>
      <div className="hero-scrim pointer-events-none absolute inset-0 z-[1]" aria-hidden="true" />

      <div className="relative z-10 flex flex-col gap-2.5 px-5 py-3.5 sm:px-8 sm:py-4">
        <div className="flex justify-end">{controls}</div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.28em] text-primary/70 [text-shadow:0_0_12px_color-mix(in_srgb,var(--color-primary)_55%,transparent)]">
              Welcome back
            </p>
            <h1 className="mt-1.5 text-[21px] font-semibold leading-tight tracking-tight text-white sm:text-[24px]">
              {greeting}.{" "}
              <span className="bg-gradient-to-r from-primary via-accent to-[#6fb8ff] bg-clip-text text-transparent [text-shadow:0_0_30px_color-mix(in_srgb,var(--color-primary)_35%,transparent)]">
                I'm {assistantName}.
              </span>
            </h1>
            <p className="mt-1.5 flex items-center gap-2 text-[13px] text-white/55">
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  online ? "hero-dot-online bg-success shadow-[0_0_8px_rgba(0,255,200,0.8)]" : "bg-white/25"
                }`}
                aria-hidden="true"
              />
              How can I help you today?
            </p>
          </div>

          <div className="hidden shrink-0 items-center gap-2.5 sm:flex">
            {imgFailed ? (
              <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-primary to-secondary">
                <Sparkles size={19} className="text-white" strokeWidth={2} />
              </div>
            ) : (
              <img
                src="/brand/tejas-emblem.png"
                alt=""
                className="h-10 w-10 shrink-0 object-contain drop-shadow-[0_0_16px_color-mix(in_srgb,var(--color-primary)_45%,transparent)]"
                onError={() => setImgFailed(true)}
              />
            )}
            <div className="leading-tight">
              <div className="text-[12px] font-bold tracking-[0.15em] text-white/90">TEJAS</div>
              <div className="text-[8.5px] font-medium tracking-[0.2em] text-white/40">YOUR AI COMPANION</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
