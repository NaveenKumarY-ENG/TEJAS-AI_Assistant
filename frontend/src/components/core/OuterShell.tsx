import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

/** Layer 6: a segmented holographic ring whose arcs illuminate independently. */
export function OuterShell({
  segments = 48,
  radius = 3.2,
  color = "#00e5ff",
  intensityRef,
}: {
  segments?: number;
  radius?: number;
  color?: string;
  intensityRef: React.MutableRefObject<number>;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const phases = useMemo(() => Array.from({ length: segments }, () => Math.random() * 10), [segments]);

  const lines = useMemo(() => {
    const gap = 0.35; // radians of gap between segments
    const span = (Math.PI * 2) / segments - gap;
    return Array.from({ length: segments }, (_, i) => {
      const start = (i / segments) * Math.PI * 2;
      const curve = new THREE.EllipseCurve(0, 0, radius, radius, start, start + span, false, 0);
      const points = curve.getPoints(6).map((p) => new THREE.Vector3(p.x, p.y, 0));
      const geometry = new THREE.BufferGeometry().setFromPoints(points);
      const material = new THREE.LineBasicMaterial({
        color,
        transparent: true,
        opacity: 0.15,
        blending: THREE.AdditiveBlending,
      });
      return new THREE.Line(geometry, material);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segments, radius, color]);

  useFrame((state) => {
    if (!groupRef.current) return;
    groupRef.current.rotation.z = state.clock.elapsedTime * 0.02;
    lines.forEach((line, i) => {
      const mat = line.material as THREE.LineBasicMaterial;
      const flicker = 0.5 + 0.5 * Math.sin(state.clock.elapsedTime * (0.6 + intensityRef.current) + phases[i]);
      mat.opacity = 0.05 + flicker * (0.25 + intensityRef.current * 0.35);
    });
  });

  return (
    <group ref={groupRef} rotation={[Math.PI / 2.4, 0, 0]}>
      {lines.map((line, i) => (
        <primitive key={i} object={line} />
      ))}
    </group>
  );
}
