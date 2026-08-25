import { useEffect, useRef, useState } from "react";
import { Mail } from "lucide-react";
import { PROFILE } from "../../constants/profile";

// lucide-react (current major version) dropped third-party brand/logo icons
// (Github, Linkedin, ...) — these are small hand-inlined marks rather than
// pulling in a whole separate icon package for two icons.
interface IconProps {
  size?: number;
  className?: string;
}

function GithubIcon({ size = 14, className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 .5C5.73.5.5 5.73.5 12c0 5.08 3.29 9.39 7.86 10.91.57.1.78-.25.78-.55 0-.27-.01-1.16-.02-2.11-3.2.7-3.88-1.36-3.88-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.05-.72.08-.71.08-.71 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11.1 11.1 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.12 3.05.74.81 1.18 1.84 1.18 3.1 0 4.43-2.7 5.4-5.26 5.69.42.36.78 1.07.78 2.16 0 1.56-.01 2.82-.01 3.2 0 .31.2.66.79.55A10.52 10.52 0 0 0 23.5 12C23.5 5.73 18.27.5 12 .5z" />
    </svg>
  );
}

function LinkedinIcon({ size = 14, className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" className={className} aria-hidden="true">
      <path d="M20.45 20.45h-3.55v-5.57c0-1.33-.02-3.03-1.85-3.03-1.85 0-2.14 1.45-2.14 2.94v5.66H9.36V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.38-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12zM7.12 20.45H3.56V9h3.56v11.45z" />
    </svg>
  );
}

const LINKS = [
  { label: PROFILE.email, href: `mailto:${PROFILE.email}`, icon: Mail, external: false },
  { label: "GitHub", href: PROFILE.github, icon: GithubIcon, external: true },
  { label: "LinkedIn", href: PROFILE.linkedin, icon: LinkedinIcon, external: true },
];

/** The sidebar's "N" avatar, made clickable — opens a small popup with the
 *  creator's name and contact links. Same click-outside dropdown mechanics
 *  as ModelSelector/VoiceSelector (proven pattern in this codebase), just
 *  anchored bottom-left instead of top-right. */
export function ProfileCard() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // A "fixed inset-0" click-outside overlay (what ModelSelector/VoiceSelector
  // originally used too, before hitting their own version of this problem)
  // breaks here for a different reason: this component sits inside
  // Sidebar's <aside>, which has backdrop-blur-2xl, and a backdrop-filter
  // (like a regular filter or transform) on an ancestor makes IT the
  // containing block for any `position: fixed` descendant, per the CSS
  // spec. That silently shrank the overlay down to the sidebar's own
  // ~230px width instead of the full viewport, so clicking anywhere in the
  // actual page never closed the popup. A document-level listener + ref
  // sidesteps the whole containing-block issue instead of fighting it.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Profile"
        title="Profile"
        className="relative grid h-9 w-9 shrink-0 place-items-center rounded-full border border-primary/50 bg-primary/5 shadow-[0_0_16px_-2px_color-mix(in_srgb,var(--color-primary)_55%,transparent)] transition-shadow hover:shadow-[0_0_20px_-2px_color-mix(in_srgb,var(--color-primary)_80%,transparent)]"
      >
        <div className="absolute inset-0 rounded-full bg-primary/15 blur-md" />
        <span className="relative text-[14px] font-bold text-primary">N</span>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-2 w-64 overflow-hidden rounded-2xl border border-white/[0.08] bg-[#0a0e14]/95 p-4 shadow-[0_0_30px_-8px_color-mix(in_srgb,var(--color-primary)_30%,transparent)] backdrop-blur-2xl">
          <div className="mb-3 flex items-center gap-3">
            <div className="relative grid h-11 w-11 shrink-0 place-items-center rounded-full border border-primary/50 bg-primary/5">
              <div className="absolute inset-0 rounded-full bg-primary/15 blur-md" />
              <span className="relative text-[16px] font-bold text-primary">N</span>
            </div>
            <div className="min-w-0">
              <div className="truncate text-[13.5px] font-semibold text-white">{PROFILE.name}</div>
              <div className="truncate text-[11.5px] text-white/45">Creator</div>
            </div>
          </div>

          <div className="space-y-1">
            {LINKS.map(({ label, href, icon: Icon, external }) => (
              <a
                key={label}
                href={href}
                {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[12.5px] text-white/70 transition-colors hover:bg-white/[0.06] hover:text-white"
              >
                <Icon size={14} className="shrink-0 text-primary/70" />
                <span className="truncate">{label}</span>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
