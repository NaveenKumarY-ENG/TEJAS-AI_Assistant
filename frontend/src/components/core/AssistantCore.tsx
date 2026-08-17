import { Suspense, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { EnergySphere } from "./EnergySphere";
import { ParticleField } from "./ParticleField";
import { EnergyArcs } from "./EnergyArcs";
import { OuterShell } from "./OuterShell";
import { ScanPulse } from "./ScanPulse";
import { BackgroundField } from "./BackgroundField";
import { HUDGlyphs } from "./HUDGlyphs";
import { HUDStatus } from "./HUDStatus";
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
// distinct from the cyan chrome used across the rest of the dashboard.
const CORE_COLORS = {
  a: "#ff7a1a",
  b: "#ffd699",
  amber: "#ffb454",
};

/** Smoothly eases the shared intensity ref toward the target for the current state. */
function IntensityDriver({
  coreState,
  intensityRef,
}: {
  coreState: CoreState;
  intensityRef: React.MutableRefObject<number>;
}) {
  useFrame((_, delta) => {
    const target = TARGET_INTENSITY[coreState];
    intensityRef.current += (target - intensityRef.current) * Math.min(delta * 2.2, 1);
  });
  return null;
}

function Scene({ coreState, intensityRef }: { coreState: CoreState; intensityRef: React.MutableRefObject<number> }) {
  return (
    <>
      <IntensityDriver coreState={coreState} intensityRef={intensityRef} />
      <BackgroundField />
      <ambientLight intensity={0.12} />
      <EnergySphere intensityRef={intensityRef} colorA={CORE_COLORS.a} colorB={CORE_COLORS.b} />
      <ParticleField intensityRef={intensityRef} color={CORE_COLORS.amber} />
      <EnergyArcs intensityRef={intensityRef} color={CORE_COLORS.b} />
      <OuterShell intensityRef={intensityRef} color={CORE_COLORS.a} />
      <ScanPulse intensityRef={intensityRef} color={CORE_COLORS.a} />
    </>
  );
}

/**
 * The AI Core hero visual: a full-bleed layered Three.js scene (energy
 * sphere, orbital rings, particle field, arcs, segmented shell, scan pulses)
 * that fills its entire parent as a background layer, reacting to the
 * assistant's idle/listening/thinking/speaking state. The chat UI is meant
 * to be layered on top of this (absolutely positioned, z-0) rather than
 * stacked next to it in the page flow.
 */
export function AssistantCore({ coreState }: { coreState: CoreState }) {
  const intensityRef = useRef(0.12);

  return (
    <div className="absolute inset-0 z-0">
      <Canvas
        camera={{ position: [0, 0, 6], fov: 42 }}
        dpr={[1, 1.75]}
        gl={{
          antialias: true,
          alpha: true,
          toneMapping: THREE.ACESFilmicToneMapping,
          toneMappingExposure: 1,
        }}
        style={{
          filter: "drop-shadow(0 0 34px rgba(255,122,26,0.35)) drop-shadow(0 0 70px rgba(255,85,0,0.2))",
        }}
      >
        <Suspense fallback={null}>
          <Scene coreState={coreState} intensityRef={intensityRef} />
        </Suspense>
      </Canvas>
      <HUDGlyphs coreState={coreState} />
      <HUDStatus coreState={coreState} />
    </div>
  );
}
