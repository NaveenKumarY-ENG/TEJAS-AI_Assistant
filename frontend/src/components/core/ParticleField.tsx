import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const VERTEX = /* glsl */ `
  attribute float aRandom;
  attribute float aSpeed;
  uniform float uTime;
  uniform float uIntensity;
  varying float vAlpha;

  void main() {
    vec3 p = position;
    float angle = uTime * aSpeed * (0.3 + uIntensity * 0.9) + aRandom * 6.2831;
    p.x += sin(angle + aRandom * 10.0) * (0.12 + uIntensity * 0.1);
    p.y += cos(angle * 1.3 + aRandom * 5.0) * (0.12 + uIntensity * 0.1);
    p.z += sin(angle * 0.7 + aRandom * 3.0) * (0.12 + uIntensity * 0.1);

    vAlpha = 0.35 + 0.65 * (0.5 + 0.5 * sin(uTime * 2.0 + aRandom * 12.0));

    vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);
    gl_PointSize = (0.8 + aRandom * 1.6) * (14.0 / -mvPosition.z);
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  varying float vAlpha;

  void main() {
    float d = length(gl_PointCoord - vec2(0.5));
    float a = smoothstep(0.5, 0.0, d) * vAlpha;
    gl_FragColor = vec4(uColor, a);
  }
`;

interface ParticleFieldProps {
  count?: number;
  color?: string;
  intensityRef: React.MutableRefObject<number>;
}

/** Layer 3: thousands of orbiting, drifting particles around the core. */
export function ParticleField({ count = 2200, color = "#7df9ff", intensityRef }: ParticleFieldProps) {
  const materialRef = useRef<THREE.ShaderMaterial>(null);
  const pointsRef = useRef<THREE.Points>(null);

  const { positions, randoms, speeds } = useMemo(() => {
    const positions = new Float32Array(count * 3);
    const randoms = new Float32Array(count);
    const speeds = new Float32Array(count);

    for (let i = 0; i < count; i++) {
      const shellRadius = 1.6 + Math.random() * 2.2;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);

      positions[i * 3] = shellRadius * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = shellRadius * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = shellRadius * Math.cos(phi);

      randoms[i] = Math.random();
      speeds[i] = 0.15 + Math.random() * 0.6;
    }
    return { positions, randoms, speeds };
  }, [count]);

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uIntensity: { value: 0 },
      uColor: { value: new THREE.Color(color) },
    }),
    [color]
  );

  useFrame((state, delta) => {
    if (materialRef.current) {
      materialRef.current.uniforms.uTime.value = state.clock.elapsedTime;
      materialRef.current.uniforms.uIntensity.value = intensityRef.current;
    }
    if (pointsRef.current) {
      pointsRef.current.rotation.y += delta * (0.02 + intensityRef.current * 0.05);
    }
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-aRandom" args={[randoms, 1]} />
        <bufferAttribute attach="attributes-aSpeed" args={[speeds, 1]} />
      </bufferGeometry>
      <shaderMaterial
        ref={materialRef}
        vertexShader={VERTEX}
        fragmentShader={FRAGMENT}
        uniforms={uniforms}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}
