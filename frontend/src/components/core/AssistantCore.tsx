import { Suspense, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { EnergySphere } from "./EnergySphere";
import { ParticleField } from "./ParticleField";
import { OrbitalRings } from "./OrbitalRings";
import { ScanPulse } from "./ScanPulse";
import { BackgroundField } from "./BackgroundField";
import { HUDGlyphs } from "./HUDGlyphs";
import { HUDStatus } from "./HUDStatus";
import { CoreEmblem } from "./CoreEmblem";
import { readRmsLevel } from "../../utils/audio";
import { type CoreState } from "../../store/assistantStore";

const TARGET_INTENSITY: Record<CoreState, number> = {
  idle: 0.12,
  listening: 0.5,
  processing: 0.6,
  thinking: 0.95,
  searching: 0.85,
  speaking: 0.7,
  error: 0.4,
};

// Warm energy-core palette (matches the reference hologram image) — kept
// distinct from the violet chrome used across the rest of the dashboard.
// A touch brighter/richer than the original #ff7a1a. Deliberately NOT
// pushed toward a more saturated pure-red-orange (e.g. #ff6a00) — confirmed
// live that a lower-green, more saturated hex clipped through the ACES tone
// mapper into looking red rather than orange once the new multi-ring
// additive glow stacked on top of it.
const CORE_COLORS = {
  a: "#ff8a2e",
  b: "#ffe3b3",
  amber: "#ffae42",
};

const RMS_BUFFER = new Uint8Array(256);

/**
 * Smoothly eases the shared intensity ref toward the target for the current
 * coreState, and — separately — eases the shared voice-level ref toward the
 * user's real live mic volume while coreState is "listening" (decays back to
 * 0 otherwise). The two refs are read every frame by the scene layers below
 * instead of being React state, so a busy voice turn doesn't trigger a
 * React re-render on every animation frame.
 */
function StateDriver({
  coreState,
  intensityRef,
  voiceLevelRef,
  micAnalyserRef,
}: {
  coreState: CoreState;
  intensityRef: React.MutableRefObject<number>;
  voiceLevelRef: React.MutableRefObject<number>;
  micAnalyserRef?: React.MutableRefObject<(() => AnalyserNode | null) | null>;
}) {
  useFrame((_, delta) => {
    const target = TARGET_INTENSITY[coreState];
    intensityRef.current += (target - intensityRef.current) * Math.min(delta * 2.2, 1);

    const analyser = coreState === "listening" ? micAnalyserRef?.current?.() ?? null : null;
    const liveLevel = analyser ? Math.min(readRmsLevel(analyser, RMS_BUFFER) * 2.2, 1) : 0;
    voiceLevelRef.current += (liveLevel - voiceLevelRef.current) * Math.min(delta * 8, 1);
  });
  return null;
}

function Scene({
  coreState,
  intensityRef,
  voiceLevelRef,
  micAnalyserRef,
}: {
  coreState: CoreState;
  intensityRef: React.MutableRefObject<number>;
  voiceLevelRef: React.MutableRefObject<number>;
  micAnalyserRef?: React.MutableRefObject<(() => AnalyserNode | null) | null>;
}) {
  return (
    <>
      <StateDriver
        coreState={coreState}
        intensityRef={intensityRef}
        voiceLevelRef={voiceLevelRef}
        micAnalyserRef={micAnalyserRef}
      />
      <BackgroundField />
      <ambientLight intensity={0.12} />
      <EnergySphere intensityRef={intensityRef} voiceLevelRef={voiceLevelRef} colorA={CORE_COLORS.a} colorB={CORE_COLORS.b} />
      <ParticleField intensityRef={intensityRef} color={CORE_COLORS.amber} />
      <OrbitalRings intensityRef={intensityRef} voiceLevelRef={voiceLevelRef} color={CORE_COLORS.a} />
      <ScanPulse intensityRef={intensityRef} color={CORE_COLORS.a} />
    </>
  );
}

/**
 * The AI Core hero visual: a full-bleed layered Three.js scene (energy
 * sphere, orbital rings, particle field, arcs, scan pulses) that fills its
 * entire parent as a background layer, reacting to the assistant's
 * idle/listening/thinking/speaking state — and, while actually listening,
 * to the user's real live mic volume (see micAnalyserRef). The chat UI is
 * meant to be layered on top of this (absolutely positioned, z-0) rather
 * than stacked next to it in the page flow.
 */
export function AssistantCore({
  coreState,
  fov = 37,
  emblemHeightClass,
  micAnalyserRef,
}: {
  coreState: CoreState;
  /** Vertical FOV — the sphere's rendered size is driven by canvas height
   *  at a fixed camera distance, independent of width, so this is the one
   *  knob a caller needs to compensate when its container's height differs
   *  from the default confined-panel case (e.g. VoiceMode's fullscreen,
   *  full-viewport-height overlay, which needs a wider FOV — a smaller
   *  apparent sphere — to leave room for its own controls below it;
   *  confirmed live that reusing the Home screen's default fov left no
   *  clearance there). Defaults to the Home screen's tuned value. */
  fov?: number;
  /** Passed straight through to CoreEmblem — a caller overriding fov also
   *  needs to shrink this to match (a wider fov shrinks the sphere at the
   *  same container height, so the emblem must shrink with it to stay in
   *  the same proportion). See CoreEmblem's own doc. */
  emblemHeightClass?: string;
  /** Optional getter for the live microphone AnalyserNode currently in use
   *  by whichever voice-input hook the caller owns (see ChatInput.tsx's
   *  exposeAnalyserRef) — a function rather than the AnalyserNode itself
   *  since the underlying node is created/destroyed per recording session,
   *  while this ref's identity stays stable across the whole page's life.
   *  Omitted entirely (e.g. by a caller with no mic input at all) just
   *  means the hologram never gets a live voice boost, no error. */
  micAnalyserRef?: React.MutableRefObject<(() => AnalyserNode | null) | null>;
}) {
  const intensityRef = useRef(0.12);
  const voiceLevelRef = useRef(0);

  return (
    <div className="absolute inset-0 z-0">
      <Canvas
        camera={{ position: [0, 0, 6], fov }}
        dpr={[1, 1.75]}
        gl={{
          antialias: true,
          alpha: true,
          toneMapping: THREE.ACESFilmicToneMapping,
          toneMappingExposure: 1,
        }}
        style={{
          filter: "drop-shadow(0 0 34px rgba(255,138,46,0.4)) drop-shadow(0 0 70px rgba(255,110,20,0.22))",
        }}
      >
        <Suspense fallback={null}>
          <Scene coreState={coreState} intensityRef={intensityRef} voiceLevelRef={voiceLevelRef} micAnalyserRef={micAnalyserRef} />
        </Suspense>
      </Canvas>
      <CoreEmblem heightClass={emblemHeightClass} />
      <HUDGlyphs coreState={coreState} />
      <HUDStatus coreState={coreState} />
    </div>
  );
}
