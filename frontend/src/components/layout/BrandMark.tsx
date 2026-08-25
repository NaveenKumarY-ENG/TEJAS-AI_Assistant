import { useState } from "react";
import { Sparkles } from "lucide-react";

/** The sidebar's top-left brand identity: emblem image (frontend/public/
 *  brand/tejas-emblem.png) + "TEJAS / AI ASSISTANT" text lockup. Falls back
 *  to a small inline glyph via onError if that file is ever missing/moved,
 *  rather than showing a broken-image icon. */
export function BrandMark({ collapsed }: { collapsed: boolean }) {
  const [imgFailed, setImgFailed] = useState(false);

  // Collapsed rail is only 76px wide — ProfileCard's own avatar already
  // fills nearly all of it, so the emblem (not just the text lockup) hides
  // too rather than wrapping/overflowing next to it.
  if (collapsed) return null;

  return (
    <div className="flex min-w-0 items-center gap-2.5">
      {imgFailed ? (
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-primary to-secondary">
          <Sparkles size={19} className="text-white" strokeWidth={2} />
        </div>
      ) : (
        <img
          src="/brand/tejas-emblem.png"
          alt=""
          className="h-10 w-10 shrink-0 object-contain"
          onError={() => setImgFailed(true)}
        />
      )}
      <div className="min-w-0 leading-tight">
        <div className="truncate text-[13px] font-bold tracking-[0.15em] text-white/90">TEJAS</div>
        <div className="truncate text-[9px] font-medium tracking-[0.2em] text-white/40">AI ASSISTANT</div>
      </div>
    </div>
  );
}
