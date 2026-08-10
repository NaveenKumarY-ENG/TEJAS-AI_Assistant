import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

interface Arc {
  life: number; // 0..1, resets when it exceeds 1
  duration: number;
  from: THREE.Vector3;
  to: THREE.Vector3;
  mid: THREE.Vector3;
}

function randomPointOnShell(radius: number) {
  const theta = Math.random() * Math.PI * 2;
  const phi = Math.acos(2 * Math.random() - 1);
  return new THREE.Vector3(
    radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.sin(phi) * Math.sin(theta),
    radius * Math.cos(phi)
  );
}

function spawnArc(radius: number): Arc {
  const from = randomPointOnShell(radius);
  const to = randomPointOnShell(radius);
  const mid = from.clone().add(to).multiplyScalar(0.5).multiplyScalar(0.7 + Math.random() * 0.5);
  return { life: Math.random(), duration: 0.5 + Math.random() * 0.7, from, to, mid };
}

/** Layer 4: sporadic electrical arcs jumping between points on the core's shell. */
export function EnergyArcs({
  count = 10,
  radius = 1.3,
  color = "#7df9ff",
  intensityRef,
}: {
  count?: number;
  radius?: number;
  color?: string;
  intensityRef: React.MutableRefObject<number>;
}) {
  const arcsRef = useRef<Arc[] | null>(null);
  if (!arcsRef.current) arcsRef.current = Array.from({ length: count }, () => spawnArc(radius));
  const arcs = arcsRef.current;

  const lines = useMemo(
    () =>
      arcs.map((arc) => {
        const geometry = new THREE.BufferGeometry().setFromPoints([arc.from, arc.mid, arc.to]);
        const material = new THREE.LineBasicMaterial({
          color,
          transparent: true,
          opacity: 0.4,
          blending: THREE.AdditiveBlending,
        });
        return new THREE.Line(geometry, material);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  useFrame((_, delta) => {
    const active = 0.3 + intensityRef.current * 0.7;

    lines.forEach((line, i) => {
      const arc = arcs[i];
      arc.life += delta / arc.duration;
      if (arc.life >= 1) {
        Object.assign(arc, spawnArc(radius));
        (line.geometry as THREE.BufferGeometry).setFromPoints([arc.from, arc.mid, arc.to]);
      }
      const mat = line.material as THREE.LineBasicMaterial;
      const flicker = Math.sin(arc.life * Math.PI); // smooth fade in then out, no per-frame jitter
      mat.opacity = Math.max(0, flicker) * active * 0.6;
    });
  });

  return (
    <group>
      {lines.map((line, i) => (
        <primitive key={i} object={line} />
      ))}
    </group>
  );
}
