import { Bell, Volume2, VolumeX, type LucideIcon } from "lucide-react";
import { useAssistantStore } from "../../store/assistantStore";
import { ModelSelector } from "../ui/ModelSelector";
import { VoiceSelector } from "../ui/VoiceSelector";
import { HeroHeader } from "./HeroHeader";

interface TopBarHeader {
  icon: LucideIcon;
  title: string;
  subtitle: string;
}

export function TopBar({
  onSoonClick,
  voiceOutputEnabled,
  onToggleVoiceOutput,
  onModelError,
  header,
}: {
  onSoonClick: (label: string) => void;
  voiceOutputEnabled: boolean;
  onToggleVoiceOutput: () => void;
  onModelError: (message: string) => void;
  // Swaps the default greeting for a page-specific title (icon + title +
  // subtitle) — e.g. the Knowledge Base page. The right-side controls
  // (model/voice selectors, mute, notifications) never change, so every
  // page shares exactly one model selector / notification control.
  header?: TopBarHeader;
}) {
  const assistantName = useAssistantStore((s) => s.assistantName);

  // Shared by both branches below — the compact page-header row and
  // HeroHeader's card render the exact same controls, same behavior, just
  // in different containers. Defined once here so there's only ever one
  // real implementation of them.
  const controls = (
    <div className="flex flex-wrap items-center gap-2.5">
      <ModelSelector onError={onModelError} />
      <VoiceSelector onError={onModelError} />

      <button
        type="button"
        onClick={onToggleVoiceOutput}
        aria-label={voiceOutputEnabled ? "Mute spoken replies" : "Enable spoken replies"}
        aria-pressed={voiceOutputEnabled}
        title={voiceOutputEnabled ? `${assistantName} speaks replies aloud` : "Spoken replies are muted"}
        className={`grid h-9 w-9 place-items-center rounded-xl border transition-colors ${
          voiceOutputEnabled
            ? "border-primary/40 bg-primary/10 text-primary"
            : "border-white/[0.08] bg-white/[0.03] text-white/50 hover:border-primary/40 hover:text-white"
        }`}
      >
        {voiceOutputEnabled ? <Volume2 size={16} strokeWidth={1.8} /> : <VolumeX size={16} strokeWidth={1.8} />}
      </button>

      <button
        type="button"
        onClick={() => onSoonClick("Notifications")}
        aria-label="Notifications"
        className="grid h-9 w-9 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03] text-white/50 transition-colors hover:border-primary/40 hover:text-white"
      >
        <Bell size={16} strokeWidth={1.8} />
      </button>
    </div>
  );

  return (
    <header className={`px-4 sm:px-8 ${header ? "pt-6 pb-3" : "pt-4 pb-2"}`}>
      {header ? (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-primary/25 bg-primary/10 text-primary">
              <header.icon size={19} strokeWidth={1.8} />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-[20px] font-semibold tracking-tight text-white">{header.title}</h1>
              <p className="mt-0.5 truncate text-[13px] text-white/45">{header.subtitle}</p>
            </div>
          </div>
          {controls}
        </div>
      ) : (
        <HeroHeader controls={controls} />
      )}
    </header>
  );
}
