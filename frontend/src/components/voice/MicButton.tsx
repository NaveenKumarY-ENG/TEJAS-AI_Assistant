import { Mic, MicOff } from "lucide-react";

export function MicButton({
  supported,
  listening,
  disabled = false,
  onToggle,
}: {
  supported: boolean;
  listening: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  const usable = supported && !disabled;
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!usable}
      aria-label={listening ? "Stop listening" : "Start voice input"}
      aria-pressed={listening}
      className="relative grid h-9 w-9 shrink-0 place-items-center rounded-full text-white/60 transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-30"
      style={{ animation: !listening && usable ? "mic-pulse 2.6s ease-in-out infinite" : undefined }}
    >
      {listening && (
        <>
          <span className="absolute inset-0 rounded-full border border-primary/60" style={{ animation: "mic-ripple 1.4s ease-out infinite" }} />
          <span
            className="absolute inset-0 rounded-full border border-primary/60"
            style={{ animation: "mic-ripple 1.4s ease-out infinite", animationDelay: "0.5s" }}
          />
        </>
      )}
      {supported ? (
        <Mic size={17} strokeWidth={1.8} className={listening ? "text-primary drop-shadow-[0_0_6px_rgba(0,229,255,0.8)]" : undefined} />
      ) : (
        <MicOff size={17} strokeWidth={1.8} />
      )}
    </button>
  );
}
