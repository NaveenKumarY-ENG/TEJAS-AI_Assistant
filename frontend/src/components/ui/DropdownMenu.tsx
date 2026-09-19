import { createPortal } from "react-dom";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** A trigger-anchored dropdown list rendered via a portal into document.body
 *  (position: fixed, computed from the trigger's own screen position) so it
 *  can never be clipped by an ancestor's overflow-hidden.
 *
 *  Confirmed live as a real bug: ModelSelector/VoiceSelector's dropdowns
 *  used to be plain `absolute`-positioned <ul>s next to their trigger
 *  button. That trigger now lives inside HeroHeader's `.hero` card, which
 *  needs `overflow: hidden` for its decorative space-scene layers (planet,
 *  meteors, starfield, ...) — so the dropdown's lower options (past
 *  whatever fit inside the hero's own, much shorter height) were silently
 *  clipped, with no scrollbar and no way to reach them. Rendering outside
 *  that DOM subtree entirely (this portal) is the actual fix — raising or
 *  removing overflow-hidden on the hero isn't an option (it's load-bearing
 *  for the background), and an internal scrollbar alone wouldn't have
 *  helped either, since the clipping happens at the hero's own boundary,
 *  not inside the dropdown. */
export function DropdownMenu({
  open,
  anchorRef,
  onClose,
  align = "right",
  children,
}: {
  open: boolean;
  anchorRef: React.RefObject<HTMLElement | null>;
  onClose: () => void;
  align?: "left" | "right";
  children: ReactNode;
}) {
  const menuRef = useRef<HTMLUListElement>(null);
  const [position, setPosition] = useState<{ top: number; left?: number; right?: number } | null>(null);

  // Recomputed every time the menu opens (not just once) — the trigger's
  // on-screen position can change between opens (sidebar collapse/expand,
  // window resize), and a stale position is worse than not opening at all.
  useLayoutEffect(() => {
    if (!open || !anchorRef.current) {
      setPosition(null);
      return;
    }
    const rect = anchorRef.current.getBoundingClientRect();
    setPosition(
      align === "right"
        ? { top: rect.bottom + 6, right: window.innerWidth - rect.right }
        : { top: rect.bottom + 6, left: rect.left }
    );
  }, [open, anchorRef, align]);

  // Same document-level listener + ref pattern already proven for the
  // pre-portal version of these dropdowns (see ModelSelector.tsx's own
  // history) — a "fixed inset-0" overlay loses DOM-order z-index ties
  // against other z-10 content. The anchor itself is excluded from the
  // "outside" check since it's a separate DOM subtree now (portaled), not
  // an ancestor of the menu — without this, the trigger's own onClick
  // toggle and this listener would both fire on the same click.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (anchorRef.current?.contains(target)) return;
      if (menuRef.current && !menuRef.current.contains(target)) onClose();
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, anchorRef, onClose]);

  if (!open || !position) return null;

  return createPortal(
    <ul
      ref={menuRef}
      style={{ position: "fixed", top: position.top, left: position.left, right: position.right }}
      className="z-50 max-h-[min(320px,70vh)] w-56 overflow-y-auto rounded-xl border border-white/[0.08] bg-[#0a0e14]/95 py-1 shadow-[0_0_30px_-8px_color-mix(in_srgb,var(--color-primary)_30%,transparent)] backdrop-blur-2xl"
    >
      {children}
    </ul>,
    document.body
  );
}
