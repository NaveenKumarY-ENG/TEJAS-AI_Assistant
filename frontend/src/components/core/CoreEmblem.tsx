import { useState } from "react";

/** A gold TEJAS emblem (frontend/public/brand/tejas-emblem-gold.png, a real
 *  transparent-background PNG) composited over the hologram's center,
 *  purely as a DOM/CSS overlay — no Three.js scene changes. Renders nothing
 *  at all on error rather than a broken-image icon, so the hologram falls
 *  back to looking exactly as it did before this existed.
 *
 *  Sized as a percentage of container HEIGHT, not width — the sphere's own
 *  rendered pixel diameter tracks container height (fixed vertical FOV +
 *  camera distance in AssistantCore.tsx means only canvas height
 *  determines projected size, independent of aspect ratio), so this is the
 *  unit that actually stays proportional to the sphere as the container's
 *  shape changes. A width-relative size looked right on the Home screen's
 *  confined panel but badly oversized in Voice Mode's much wider viewport
 *  — confirmed live. heightClass is still a caller-supplied prop, not a
 *  hardcoded constant, because AssistantCore's fov also varies per caller
 *  (see its own doc) — a wider fov shrinks the sphere's apparent size at
 *  the same container height, so the emblem needs to shrink to match. */
export function CoreEmblem({ heightClass = "h-[23%]" }: { heightClass?: string }) {
  const [imgFailed, setImgFailed] = useState(false);
  if (imgFailed) return null;

  return (
    <img
      src="/brand/tejas-emblem-gold.png"
      alt=""
      className={`pointer-events-none absolute left-1/2 top-1/2 w-auto -translate-x-1/2 -translate-y-1/2 opacity-95 ${heightClass}`}
      onError={() => setImgFailed(true)}
    />
  );
}
