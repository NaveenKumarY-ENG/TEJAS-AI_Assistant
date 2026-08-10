import { Stars } from "@react-three/drei";

/** In-scene backdrop: distant stars + depth fog, rendered behind the AI core. */
export function BackgroundField() {
  return (
    <>
      <fog attach="fog" args={["#050505", 6, 22]} />
      <Stars radius={80} depth={40} count={2500} factor={3} saturation={0} fade speed={0.4} />
    </>
  );
}
