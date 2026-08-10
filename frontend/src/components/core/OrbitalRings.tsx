import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

interface RingConfig {
  radius: number;
  tilt: [number, number, number];
  speed: number; // signed: negative = counter-clockwise
  opacity: number;
  color: string;
  thickness: number;
}

function buildConfigs(colorA: string, colorB: string, colorC: string): RingConfig[] {
  return [
    { radius: 1.35, tilt: [1.3, 0.2, 0], speed: 0.12, opacity: 0.5, color: colorA, thickness: 0.006 },
    { radius: 1.55, tilt: [0.9, 1.1, 0.4], speed: -0.09, opacity: 0.35, color: colorB, thickness: 0.004 },
    { radius: 1.75, tilt: [0.3, 0.8, 1.2], speed: 0.16, opacity: 0.3, color: colorA, thickness: 0.005 },
    { radius: 1.95, tilt: [1.5, 0.4, 0.9], speed: -0.06, opacity: 0.22, color: colorC, thickness: 0.004 },
    { radius: 2.2, tilt: [0.6, 1.4, 0.2], speed: 0.08, opacity: 0.28, color: colorB, thickness: 0.005 },
    { radius: 2.45, tilt: [1.1, 0.3, 1.4], speed: -0.14, opacity: 0.18, color: colorA, thickness: 0.003 },
    { radius: 2.7, tilt: [0.2, 1.2, 0.6], speed: 0.05, opacity: 0.16, color: colorC, thickness: 0.004 },
    { radius: 2.95, tilt: [1.4, 0.6, 0.1], speed: -0.11, opacity: 0.14, color: colorB, thickness: 0.003 },
  ];
}

function Ring({ config, intensityRef }: { config: RingConfig; intensityRef: React.MutableRefObject<number> }) {
  const ref = useRef<THREE.Mesh>(null);

  useFrame((_, delta) => {
    if (!ref.current) return;
    const speedMul = 1 + intensityRef.current * 1.8;
    ref.current.rotation.z += delta * config.speed * speedMul;
  });

  return (
    <mesh ref={ref} rotation={config.tilt}>
      <torusGeometry args={[config.radius, config.thickness, 8, 128]} />
      <meshBasicMaterial
        color={config.color}
        transparent
        opacity={config.opacity}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
      />
    </mesh>
  );
}

/** Layer 2: eight holographic rings, mixed radii/tilts/speeds/directions. */
export function OrbitalRings({
  intensityRef,
  colorA = "#00e5ff",
  colorB = "#7df9ff",
  colorC = "#6c63ff",
}: {
  intensityRef: React.MutableRefObject<number>;
  colorA?: string;
  colorB?: string;
  colorC?: string;
}) {
  const configs = useMemo(() => buildConfigs(colorA, colorB, colorC), [colorA, colorB, colorC]);

  return (
    <group>
      {configs.map((c, i) => (
        <Ring key={i} config={c} intensityRef={intensityRef} />
      ))}
    </group>
  );
}
