import { Bell, Volume2, VolumeX } from "lucide-react";
import { useMemo } from "react";
import { useAssistantStore } from "../../store/assistantStore";
import { ModelSelector } from "../ui/ModelSelector";
import { timeOfDayGreeting } from "../../utils/greeting";

function useGreeting() {
  return useMemo(() => timeOfDayGreeting(), []);
}

export function TopBar({
  onSoonClick,
  voiceOutputEnabled,
  onToggleVoiceOutput,
  onModelError,
}: {
  onSoonClick: (label: string) => void;
  voiceOutputEnabled: boolean;
  onToggleVoiceOutput: () => void;
  onModelError: (message: string) => void;
}) {
  const greeting = useGreeting();
  const assistantName = useAssistantStore((s) => s.assistantName);

  return (
    <header className="flex items-center justify-between px-8 pt-6 pb-3">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-white">
          {greeting}. <span className="text-white/50">I'm {assistantName}.</span>
        </h1>
        <p className="mt-0.5 text-[13px] text-white/45">How can I help you today?</p>
      </div>

      <div className="flex items-center gap-2.5">
        <ModelSelector onError={onModelError} />

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
    </header>
  );
}
