import { useEffect, useRef, useState } from "react";
import { Check, Mic2 } from "lucide-react";
import { useAssistantStore } from "../../store/assistantStore";

/** Lets the user switch between neural TTS voices (Kokoro) at runtime — same
 * pattern as ModelSelector. Voice is a per-call parameter on the backend
 * (agent/tts.py), not baked into a loaded model, so a switch takes effect on
 * the very next spoken reply — no reconnect. */
export function VoiceSelector({ onError }: { onError: (message: string) => void }) {
  const ttsVoices = useAssistantStore((s) => s.ttsVoices);
  const ttsVoiceId = useAssistantStore((s) => s.ttsVoiceId);
  const setActiveTtsVoice = useAssistantStore((s) => s.setActiveTtsVoice);
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const active = ttsVoices.find((v) => v.id === ttsVoiceId);

  // Document-level listener + ref instead of a "fixed inset-0" overlay — see
  // ModelSelector.tsx's comment: that overlay ties in z-index with
  // ConversationPanel's wrapper and loses the DOM-order tiebreak, so it
  // never actually received clicks in the chat/hologram area.
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

  if (ttsVoices.length === 0) return null;

  const handlePick = async (id: string) => {
    setOpen(false);
    if (id === ttsVoiceId || switching) return;
    setSwitching(true);
    try {
      const res = await fetch("/api/tts/voices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to switch voice");
      setActiveTtsVoice(data.active);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to switch voice");
    } finally {
      setSwitching(false);
    }
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={switching}
        aria-label="Switch voice"
        title="Switch voice"
        className="flex h-9 items-center gap-1.5 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 text-[12.5px] text-white/70 transition-colors hover:border-primary/40 hover:text-white disabled:opacity-50"
      >
        <Mic2 size={13} strokeWidth={1.8} className="shrink-0" />
        <span className="mono max-w-[110px] truncate">{switching ? "Switching…" : (active?.label ?? "Voice")}</span>
      </button>

      {open && (
        <ul className="absolute right-0 z-20 mt-1.5 w-48 overflow-hidden rounded-xl border border-white/[0.08] bg-[#0a0e14]/95 py-1 shadow-[0_0_30px_-8px_rgba(0,229,255,0.3)] backdrop-blur-2xl">
          {ttsVoices.map((v) => (
            <li key={v.id}>
              <button
                type="button"
                onClick={() => handlePick(v.id)}
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[12.5px] text-white/70 transition-colors hover:bg-primary/10 hover:text-white"
              >
                <span className="truncate">{v.label}</span>
                {v.id === ttsVoiceId && <Check size={13} strokeWidth={2} className="shrink-0 text-primary" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
