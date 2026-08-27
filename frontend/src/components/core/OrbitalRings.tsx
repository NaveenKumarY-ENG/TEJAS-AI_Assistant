import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const VERTEX = /* glsl */ `
  varying vec3 vPos;
  void main() {
    vPos = position;
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mvPosition;
  }
`;

// Two things happen at once, independent of each other: a segmented "broken
// ring" base structure (arcs with gaps, not a solid outline and not dots),
// and 3 bright energy comets continuously sweeping around it. uIntensity
// (the assistant's idle/listening/thinking/speaking state) and uVoiceLevel
// (live mic RMS while actually listening — see AssistantCore's
// micAnalyserRef plumbing) both speed up and brighten the comets, so the
// ring visibly reacts to voice/chat activity in real time, not just a
// discrete before/after state change.
const FRAGMENT = /* glsl */ `
  uniform float uTime;
  uniform float uIntensity;
  uniform float uVoiceLevel;
  uniform float uSegments;
  uniform float uSpeed;
  uniform vec3 uColor;
  varying vec3 vPos;

  const float PI = 3.14159265;

  void main() {
    float angle = atan(vPos.y, vPos.x);
    float a01 = angle / (2.0 * PI) + 0.5;

    float segPos = fract(a01 * uSegments);
    float segMask = smoothstep(0.0, 0.07, segPos) * smoothstep(1.0, 0.93, segPos);

    float speed = uSpeed * (0.15 + uIntensity * 0.55 + uVoiceLevel * 1.3);
    float comet = 0.0;
    for (int i = 0; i < 3; i++) {
      float offset = float(i) / 3.0;
      float d = fract(a01 - uTime * speed - offset);
      d = min(d, 1.0 - d);
      comet += smoothstep(0.06, 0.0, d);
    }
    comet = min(comet, 1.0);

    // Kept deliberately modest — additive blending stacks fast where rings
    // cross in screen space, and pushing this too bright clipped through
    // the tone mapper into looking red instead of orange. Confirmed live.
    float base = (0.09 + uIntensity * 0.05) * segMask;
    float glow = comet * (0.3 + uIntensity * 0.3 + uVoiceLevel * 0.45);
    float alpha = clamp(base + glow, 0.0, 0.85);

    gl_FragColor = vec4(uColor, alpha);
  }
`;

interface RingConfig {
  radius: number;
  tube: number;
  segments: number;
  speed: number;
  tilt: [number, number, number];
  spin: number;
  /** Slow, independent rotation on the OTHER two axes, on top of `spin`'s
   *  z-axis rotation — real 3-axis tumbling (like a gyroscope/armillary
   *  sphere), not a flat spin. This is what produces the "swings in close
   *  to the core, then opens back out and drifts away" look: an edge-on
   *  ring collapses visually into a thin arc that grazes the sphere, then
   *  keeps tumbling back into a wide halo. Deliberately irrational-ish
   *  ratios between rings' tumbleX/tumbleY so the three never fall into a
   *  repeating shared pattern. */
  tumbleX: number;
  tumbleY: number;
}

// Radius/tube pushed up a bit from the first pass — confirmed live the
// original read as too thin/faint to register as a deliberate structure at
// a glance. Tilts are just each ring's STARTING orientation now; the actual
// look comes from the continuous tumble applied in useFrame below.
const RINGS: RingConfig[] = [
  { radius: 1.5, tube: 0.018, segments: 7, speed: 1, tilt: [Math.PI / 2.5, 0.1, 0], spin: 0.05, tumbleX: 0.09, tumbleY: 0.13 },
  { radius: 2.0, tube: 0.015, segments: 10, speed: -0.7, tilt: [Math.PI / 2.2, -0.3, 0.2], spin: -0.035, tumbleX: -0.06, tumbleY: 0.08 },
  { radius: 2.55, tube: 0.012, segments: 5, speed: 0.5, tilt: [Math.PI / 2.1, 0.4, -0.3], spin: 0.022, tumbleX: 0.045, tumbleY: -0.07 },
];

function Ring({
  config,
  color,
  intensityRef,
  voiceLevelRef,
}: {
  config: RingConfig;
  color: string;
  intensityRef: React.MutableRefObject<number>;
  voiceLevelRef: React.MutableRefObject<number>;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const materialRef = useRef<THREE.ShaderMaterial>(null);

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uIntensity: { value: 0 },
      uVoiceLevel: { value: 0 },
      uSegments: { value: config.segments },
      uSpeed: { value: config.speed },
      uColor: { value: new THREE.Color(color) },
    }),
    [config.segments, config.speed, color]
  );

  useFrame((state, delta) => {
    if (materialRef.current) {
      materialRef.current.uniforms.uTime.value = state.clock.elapsedTime;
      materialRef.current.uniforms.uIntensity.value = intensityRef.current;
      materialRef.current.uniforms.uVoiceLevel.value = voiceLevelRef.current;
    }
    if (groupRef.current) {
      const boost = 1 + intensityRef.current * 0.6;
      groupRef.current.rotation.x += delta * config.tumbleX * boost;
      groupRef.current.rotation.y += delta * config.tumbleY * boost;
      groupRef.current.rotation.z += delta * config.spin * boost;
    }
  });

  return (
    <group ref={groupRef} rotation={config.tilt}>
      <mesh>
        <torusGeometry args={[config.radius, config.tube, 10, 180]} />
        <shaderMaterial
          ref={materialRef}
          vertexShader={VERTEX}
          fragmentShader={FRAGMENT}
          uniforms={uniforms}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </group>
  );
}

/** Layer 6: three concentric "broken ring" HUD reticles at different tilts
 *  and speeds — a segmented energy structure with traveling light comets,
 *  deliberately not a row of dots and not a plain solid line. Reacts live to
 *  both the assistant's coreState (via intensityRef) and, while actually
 *  listening, the user's real mic volume (via voiceLevelRef). */
export function OrbitalRings({
  color = "#ff7a1a",
  intensityRef,
  voiceLevelRef,
}: {
  color?: string;
  intensityRef: React.MutableRefObject<number>;
  voiceLevelRef: React.MutableRefObject<number>;
}) {
  return (
    <>
      {RINGS.map((config, i) => (
        <Ring key={i} config={config} color={color} intensityRef={intensityRef} voiceLevelRef={voiceLevelRef} />
      ))}
    </>
  );
}
