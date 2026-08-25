import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { useAssistantStore } from "../../store/assistantStore";

/** Lets the user switch between Ollama models (qwen2.5:7b, gemma2:9b, ...)
 * and Anthropic at runtime. The backend reads config fresh on every LLM
 * call, so a switch takes effect on the very next turn — no reconnect. */
export function ModelSelector({ onError }: { onError: (message: string) => void }) {
  const availableModels = useAssistantStore((s) => s.availableModels);
  const modelId = useAssistantStore((s) => s.modelId);
  const setActiveModel = useAssistantStore((s) => s.setActiveModel);
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const active = availableModels.find((m) => m.id === modelId);

  // A "fixed inset-0" click-outside overlay (the original approach here)
  // only works if it's guaranteed to paint above everything else it
  // overlaps — but it and ConversationPanel's wrapper both sit at z-index
  // 10 in the same stacking context, and CSS breaks that tie by DOM order,
  // not z-index value alone. ConversationPanel's div comes later in the
  // tree, so it silently intercepted clicks in the whole chat/hologram
  // area instead of the overlay — confirmed live via
  // document.elementFromPoint() returning the conversation wrapper, not the
  // overlay, for a click there while the dropdown was open. A document-level
  // listener + ref sidesteps stacking-context/z-index fights entirely
  // (same fix already applied to ProfileCard for a different root cause —
  // backdrop-filter's containing-block effect on `fixed` descendants).
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

  if (availableModels.length === 0) return null;

  const handlePick = async (id: string) => {
    setOpen(false);
    if (id === modelId || switching) return;
    setSwitching(true);
    try {
      const res = await fetch("/api/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to switch model");
      setActiveModel(data.active, data.model);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to switch model");
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
        aria-label="Switch model"
        title="Switch model"
        className="flex h-9 items-center gap-1.5 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 text-[12.5px] text-white/70 transition-colors hover:border-primary/40 hover:text-white disabled:opacity-50"
      >
        <span className="mono max-w-[130px] truncate">{switching ? "Switching…" : (active?.label ?? "Select model")}</span>
        <ChevronDown size={13} strokeWidth={1.8} className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <ul className="absolute right-0 z-20 mt-1.5 w-56 overflow-hidden rounded-xl border border-white/[0.08] bg-[#0a0e14]/95 py-1 shadow-[0_0_30px_-8px_color-mix(in_srgb,var(--color-primary)_30%,transparent)] backdrop-blur-2xl">
          {availableModels.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                onClick={() => handlePick(m.id)}
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[12.5px] text-white/70 transition-colors hover:bg-primary/10 hover:text-white"
              >
                <span className="truncate">{m.label}</span>
                {m.id === modelId && <Check size={13} strokeWidth={2} className="shrink-0 text-primary" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
