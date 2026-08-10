import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const WAVE_COUNT = 3;
const PERIOD = 4.2; // seconds per wave cycle
const MAX_RADIUS = 4.5;

/** Layer 7: expanding scan rings that pulse outward and fade every few seconds. */
export function ScanPulse({
  color = "#00e5ff",
  intensityRef,
}: {
  color?: string;
  intensityRef: React.MutableRefObject<number>;
}) {
  const refs = useRef<THREE.Mesh[]>([]);
  const offsets = useMemo(() => Array.from({ length: WAVE_COUNT }, (_, i) => (i / WAVE_COUNT) * PERIOD), []);

  useFrame((state) => {
    const speedMul = 1 + intensityRef.current * 0.8;
    refs.current.forEach((mesh, i) => {
      if (!mesh) return;
      const t = ((state.clock.elapsedTime * speedMul + offsets[i]) % PERIOD) / PERIOD;
      const radius = 0.6 + t * MAX_RADIUS;
      mesh.scale.setScalar(radius);
      const mat = mesh.material as THREE.MeshBasicMaterial;
      mat.opacity = (1 - t) * 0.35;
    });
  });

  return (
    <group rotation={[Math.PI / 2, 0, 0]}>
      {offsets.map((_, i) => (
        <mesh key={i} ref={(el) => { if (el) refs.current[i] = el; }}>
          <ringGeometry args={[0.97, 1, 64]} />
          <meshBasicMaterial color={color} transparent opacity={0} side={THREE.DoubleSide} depthWrite={false} />
        </mesh>
      ))}
    </group>
  );
}
