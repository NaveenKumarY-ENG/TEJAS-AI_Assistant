import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { energyFragmentShader, energyVertexShader } from "../../shaders/energySphere";

interface EnergySphereProps {
  radius?: number;
  intensityRef: React.MutableRefObject<number>;
  colorA?: string;
  colorB?: string;
}

/** Layer 1: the glowing, breathing energy sphere at the heart of the core,
 *  plus a crisp low-poly wireframe cage layered around it for a hologram
 *  "schematic" read rather than a solid glowing blob. */
export function EnergySphere({ radius = 1, intensityRef, colorA = "#00e5ff", colorB = "#6c63ff" }: EnergySphereProps) {
  const materialRef = useRef<THREE.ShaderMaterial>(null);
  const groupRef = useRef<THREE.Group>(null);
  const cageRef = useRef<THREE.LineSegments>(null);
  const lightRef = useRef<THREE.PointLight>(null);

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uIntensity: { value: 0 },
      uColorA: { value: new THREE.Color(colorA) },
      uColorB: { value: new THREE.Color(colorB) },
    }),
    [colorA, colorB]
  );

  const cageGeometry = useMemo(() => new THREE.IcosahedronGeometry(radius * 1.18, 1), [radius]);
  const cageEdges = useMemo(() => new THREE.EdgesGeometry(cageGeometry), [cageGeometry]);

  useFrame((state, delta) => {
    const intensity = intensityRef.current;
    if (materialRef.current) {
      materialRef.current.uniforms.uTime.value = state.clock.elapsedTime;
      materialRef.current.uniforms.uIntensity.value = intensity;
    }
    if (groupRef.current) {
      groupRef.current.rotation.y += delta * (0.06 + intensity * 0.12);
      const breathe = 1 + Math.sin(state.clock.elapsedTime * 0.9) * (0.02 + intensity * 0.03);
      groupRef.current.scale.setScalar(breathe);
    }
    if (cageRef.current) {
      cageRef.current.rotation.y -= delta * (0.1 + intensity * 0.22);
      cageRef.current.rotation.x += delta * (0.04 + intensity * 0.08);
      const mat = cageRef.current.material as THREE.LineBasicMaterial;
      mat.opacity = 0.35 + intensity * 0.35;
    }
    if (lightRef.current) {
      lightRef.current.intensity = 2.2 + intensity * 3;
    }
  });

  return (
    <group ref={groupRef}>
      <mesh>
        <icosahedronGeometry args={[radius, 24]} />
        <shaderMaterial
          ref={materialRef}
          vertexShader={energyVertexShader}
          fragmentShader={energyFragmentShader}
          uniforms={uniforms}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          side={THREE.DoubleSide}
        />
      </mesh>
      <lineSegments ref={cageRef} geometry={cageEdges}>
        <lineBasicMaterial color={colorA} transparent opacity={0.4} blending={THREE.AdditiveBlending} depthWrite={false} />
      </lineSegments>
      <pointLight ref={lightRef} color={colorA} intensity={2.2} distance={6} decay={2} />
    </group>
  );
}
