import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { AssistantCore } from "../core/AssistantCore";
import { ConversationPanel } from "../ui/ConversationPanel";
import { ModelSelector } from "../ui/ModelSelector";
import { VoiceSelector } from "../ui/VoiceSelector";
import { MicButton } from "./MicButton";
import { VoiceWaveform, type WaveformMode } from "./VoiceWaveform";
import { useSpeechRecognition } from "../../hooks/useSpeechRecognition";
import { useAssistantStore, type CoreState } from "../../store/assistantStore";
import { getVoiceModelId, setVoiceModelId } from "../../utils/voicePreference";
import type { TtsBoundarySignal } from "../../hooks/useSpeechSynthesis";

interface VoiceModeProps {
  coreState: CoreState;
  onSend: (text: string, opts?: { speak?: boolean }) => void;
  stopSpeaking: () => void;
  ttsBoundaryRef: React.RefObject<TtsBoundarySignal | null>;
  onExit: () => void;
}

// Fastest local option — used as the very first default before the user has
// ever picked a voice model of their own.
const DEFAULT_VOICE_MODEL_ID = "ollama:qwen2.5:7b";

// Coordinates with the hands-free auto-relisten effect below.
const REPLY_BUSY_STATES: CoreState[] = ["thinking", "searching", "speaking"];

/**
 * Fullscreen, hands-free voice conversation view — a distinct experience
 * from the inline mic in ChatInput, but a view over the SAME session: it
 * reuses the sendMessage/stopSpeaking/coreState/timeline already owned by
 * useAssistantSocket (via App.tsx), it doesn't own a second WebSocket.
 */
export function VoiceMode({ coreState, onSend, stopSpeaking, ttsBoundaryRef, onExit }: VoiceModeProps) {
  const [handsFree, setHandsFree] = useState(true);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const modelId = useAssistantStore((s) => s.modelId);
  const setActiveModel = useAssistantStore((s) => s.setActiveModel);

  const submitVoiceText = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed, { speak: true }); // Voice Mode always speaks, regardless of the TopBar mute toggle
  };

  const { supported, listening, processing, start, stop, cancel, analyserRef, revealText } = useSpeechRecognition(
    submitVoiceText,
    (message) => setVoiceError(message),
    { vadAutoStop: true }
  );

  // --- Model swap on enter/exit -----------------------------------------
  const previousModelIdRef = useRef<string | null>(null);
  const swapReadyRef = useRef(false);
  // Set right before the mount-time programmatic swap and consumed by the
  // watcher effect below — distinguishes "this modelId change was us" from
  // "the user picked something," which a simple "skip the first change"
  // counter can't: if the saved voice preference already matches the
  // current chat model, no swap happens at all, and a fixed "first change"
  // counter would then wrongly eat the user's actual first manual pick.
  const suppressNextPersistRef = useRef(false);

  const switchModel = async (id: string) => {
    const res = await fetch("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to switch model");
    setActiveModel(data.active, data.model);
  };

  useEffect(() => {
    const current = useAssistantStore.getState().modelId;
    const desired = getVoiceModelId() ?? DEFAULT_VOICE_MODEL_ID;
    previousModelIdRef.current = current;
    let cancelled = false;

    (async () => {
      if (desired && desired !== current) {
        suppressNextPersistRef.current = true;
        try {
          await switchModel(desired);
        } catch (err) {
          suppressNextPersistRef.current = false; // swap failed — no modelId change is coming to consume this
          setVoiceError(err instanceof Error ? err.message : "Failed to switch to voice model");
        }
      }
      swapReadyRef.current = true;
      // Voice Mode was already closed before this in-flight swap landed —
      // the unmount cleanup below ran too early to see it (the store still
      // showed the old model at that point), so undo it now instead of
      // leaving the app stuck on the voice model after exiting.
      if (cancelled) {
        const previous = previousModelIdRef.current;
        const activeNow = useAssistantStore.getState().modelId;
        if (previous && previous !== activeNow) switchModel(previous).catch(() => {});
      }
    })();

    return () => {
      cancelled = true;
      const previous = previousModelIdRef.current;
      const activeNow = useAssistantStore.getState().modelId;
      if (previous && previous !== activeNow) {
        switchModel(previous).catch(() => {}); // best-effort restore on the way out
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Any model picked from the selector WHILE in Voice Mode becomes the
  // remembered voice preference — skip only the change caused by the
  // programmatic mount-time swap above, not a real user pick.
  useEffect(() => {
    if (!swapReadyRef.current) return;
    if (suppressNextPersistRef.current) {
      suppressNextPersistRef.current = false;
      return;
    }
    if (modelId) setVoiceModelId(modelId);
  }, [modelId]);

  // --- Hands-free auto-relisten -------------------------------------------
  // A reply "finishing" doesn't always mean the last state before idle was
  // literally "speaking" — a tool call that fires AFTER some text has
  // already streamed (useAssistantSocket's "tool" case overwrites coreState
  // back to "thinking"/"searching", and it's never reset to "speaking" for
  // the remainder of that turn since beginAssistantStream only runs once
  // per turn) can leave the last busy state as "thinking" instead. Checking
  // for any of the three reply-busy states, not just "speaking", keeps
  // hands-free relisten working for that pattern too. "processing" (local
  // STT upload) is deliberately excluded — relistening immediately after
  // your OWN transcription finishes, before the assistant has even replied,
  // would start recording over the top of the turn you just sent.
  const prevCoreStateRef = useRef(coreState);
  useEffect(() => {
    const prev = prevCoreStateRef.current;
    prevCoreStateRef.current = coreState;
    if (!handsFree) return;
    if (REPLY_BUSY_STATES.includes(prev) && coreState === "idle") {
      start();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coreState, handsFree]);

  // Auto-start listening the moment Voice Mode opens.
  const hasAutoStartedRef = useRef(false);
  useEffect(() => {
    if (hasAutoStartedRef.current) return;
    hasAutoStartedRef.current = true;
    start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleMicToggle = () => {
    if (coreState === "speaking") {
      stopSpeaking();
      start();
      return;
    }
    if (listening) {
      stop();
    } else {
      start();
    }
  };

  const handleExit = () => {
    cancel(); // discard any in-progress recording rather than sending a half-spoken utterance after leaving
    stopSpeaking();
    onExit();
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleExit();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const waveformMode: WaveformMode = listening ? "microphone" : coreState === "speaking" ? "tts" : "idle";
  // Mirrors ChatInput's mic-disable rule: blocked while the assistant is
  // actively working, EXCEPT during "speaking" (clicking then interrupts
  // and starts listening — see handleMicToggle) — without this, the mic
  // stayed clickable during "thinking"/"searching" and a click there could
  // start a second recording overlapping the turn already in flight.
  const replyBusy = REPLY_BUSY_STATES.includes(coreState);
  const micDisabled = (replyBusy && coreState !== "speaking") || processing;

  return (
    <div className="fixed inset-0 z-50 bg-[#050505]">
      {/* Wider FOV than the Home screen's default (37°) — this view's canvas
          is the full viewport height (fullscreen overlay, not a confined
          panel), which otherwise renders the sphere large enough to crowd
          the hands-free/mic controls anchored below it. emblemHeightClass
          is scaled down to match (proportionally smaller sphere needs a
          proportionally smaller emblem) — see AssistantCore's prop docs. */}
      <AssistantCore coreState={coreState} fov={54} emblemHeightClass="h-[9%]" />

      <div className="relative z-10 flex h-full flex-col">
        {/* pt-14, not pt-6: AssistantCore's own HUDStatus ("CORE ONLINE" etc.)
            sits absolutely-positioned at top-5 left-5 inside the hologram
            layer beneath this row — confirmed live that pt-6 (24px) put the
            VOICE MODE pill directly on top of it, both badges illegible
            where they overlapped. Same fix shape as App.tsx's disclaimer
            paragraph, which had the identical collision on the Home screen. */}
        <div className="flex items-center justify-between px-8 pt-14">
          <span className="mono flex items-center gap-1.5 rounded-full border border-primary/25 px-3 py-1 text-[11px] tracking-wider text-primary/70">
            VOICE MODE
          </span>
          <div className="flex items-center gap-2.5">
            <ModelSelector onError={(m) => setVoiceError(m)} />
            <VoiceSelector onError={(m) => setVoiceError(m)} />
            <button
              type="button"
              onClick={handleExit}
              aria-label="Exit voice mode"
              className="grid h-9 w-9 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03] text-white/60 transition-colors hover:border-primary/40 hover:text-white"
            >
              <X size={16} strokeWidth={1.8} />
            </button>
          </div>
        </div>

        <div className="flex-1" />

        <div className="flex flex-col items-center gap-4 pb-6">
          {!supported && (
            <p className="max-w-sm text-center text-[13px] text-white/50">
              Voice input isn't available in this context — this page needs to be served over localhost or https.
            </p>
          )}
          {voiceError && <p className="max-w-sm text-center text-[12.5px] text-warning">{voiceError}</p>}

          <VoiceWaveform mode={waveformMode} analyserRef={analyserRef} boundaryRef={ttsBoundaryRef} className="scale-150" />

          {revealText && (
            <div className="max-w-[90%] truncate whitespace-nowrap rounded-full bg-black/70 px-3.5 py-1.5 text-[12px] text-primary backdrop-blur-md animate-[enter_.3s_ease-out]">
              "{revealText}"
            </div>
          )}
          {!revealText && !listening && !processing && (
            <p className="text-[12px] text-white/40">
              {handsFree ? "Listening resumes automatically after each reply" : "Click the mic to talk"}
            </p>
          )}

          <div className="flex items-center gap-5">
            <button
              type="button"
              onClick={() => setHandsFree((h) => !h)}
              className={`rounded-full border px-3 py-1.5 text-[11.5px] transition-colors ${
                handsFree
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-white/[0.08] bg-white/[0.03] text-white/50 hover:text-white/80"
              }`}
            >
              Hands-free {handsFree ? "ON" : "OFF"}
            </button>

            <div className="grid h-16 w-16 place-items-center rounded-full border border-primary/20 bg-primary/[0.04] shadow-[0_0_30px_-6px_color-mix(in_srgb,var(--color-primary)_40%,transparent)]">
              <MicButton supported={supported} listening={listening} disabled={micDisabled} onToggle={handleMicToggle} />
            </div>
          </div>
        </div>

        {/* flex + flex-col is what makes ConversationPanel's own flex-1/
            overflow-y-auto actually take effect — without a flex parent to
            size it against, it just grows with content and gets silently
            clipped by this box's max-h instead of scrolling. */}
        <div className="mx-auto mb-6 flex max-h-[26vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/[0.06] bg-black/30 backdrop-blur-md">
          <ConversationPanel />
        </div>
      </div>
    </div>
  );
}
