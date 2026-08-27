import type { CSSProperties } from "react";

// Shared purple-into-black radial gradient behind the AI core hologram —
// used by both the Home screen's hologram panel (App.tsx) and Voice Mode's
// fullscreen view (VoiceMode.tsx). A flat near-black background read as an
// empty void behind the hologram in both places; this pools a soft violet
// glow behind the core instead, fading to black at the edges. Kept in one
// place so the two views can't drift out of sync with each other.
export const HOLOGRAM_BACKDROP_STYLE: CSSProperties = {
  backgroundImage:
    "radial-gradient(ellipse 70% 60% at 50% 42%, color-mix(in srgb, var(--color-primary) 26%, transparent) 0%, transparent 70%), " +
    "radial-gradient(ellipse 100% 80% at 50% 100%, color-mix(in srgb, var(--color-secondary) 16%, transparent) 0%, transparent 60%)",
};
