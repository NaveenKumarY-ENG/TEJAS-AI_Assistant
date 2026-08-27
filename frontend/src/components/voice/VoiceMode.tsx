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
import { HOLOGRAM_BACKDROP_STYLE } from "../../utils/hologramBackdrop";
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
    <div className="fixed inset-0 z-50 bg-[#050208]" style={HOLOGRAM_BACKDROP_STYLE}>
      {/* Tighter FOV than before (54° -> 46°) so the sphere reads as a big,
          wide hologram befitting a fullscreen view rather than shrinking to
          leave headroom — confirmed live the old value looked small in this
          much bigger canvas. emblemHeightClass scaled up to match (see
          AssistantCore's prop docs) plus a bit more on top per its own ask. */}
      <AssistantCore coreState={coreState} fov={46} emblemHeightClass="h-[15%]" />

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

        {/* Hands-free/mic used to float here, right below the sphere — at
            the bigger hologram size this now asks for, that row sat right
            on top of the lower half of the sphere and its emblem. Confirmed
            live. Moved into the chat box's own footer below instead, so
            nothing overlaps the hologram at all. */}
        <div className="flex flex-col items-center gap-3 pb-4">
          {/* revealText already got a dark blurred pill so it stayed legible
              over the hologram — these other status lines didn't, and sat
              directly on the bright sphere with nothing behind them.
              Confirmed live: "Click the mic to talk" and voiceError text
              were genuinely hard to read against it. Same treatment now,
              for all of them. */}
          {!supported && (
            <p className="max-w-sm rounded-full bg-black/60 px-3.5 py-1.5 text-center text-[13px] text-white/60 backdrop-blur-md">
              Voice input isn't available in this context — this page needs to be served over localhost or https.
            </p>
          )}
          {voiceError && (
            <p className="max-w-sm rounded-full bg-black/60 px-3.5 py-1.5 text-center text-[12.5px] text-warning backdrop-blur-md">
              {voiceError}
            </p>
          )}

          <VoiceWaveform mode={waveformMode} analyserRef={analyserRef} boundaryRef={ttsBoundaryRef} className="scale-150" />

          {revealText && (
            <div className="max-w-[90%] truncate whitespace-nowrap rounded-full bg-black/70 px-3.5 py-1.5 text-[12px] text-primary backdrop-blur-md animate-[enter_.3s_ease-out]">
              "{revealText}"
            </div>
          )}
          {!revealText && !listening && !processing && (
            <p className="rounded-full bg-black/60 px-3.5 py-1.5 text-[12px] text-white/50 backdrop-blur-md">
              {handsFree ? "Listening resumes automatically after each reply" : "Click the mic to talk"}
            </p>
          )}
        </div>

        {/* flex + flex-col is what makes ConversationPanel's own flex-1/
            overflow-y-auto actually take effect — without a flex parent to
            size it against, it just grows with content and gets silently
            clipped by this box's max-h instead of scrolling. */}
        {/* A near-invisible white/[0.06] border used to be the only edge
            here, so the whole panel read as a flat dark void — confirmed
            live. A gold edge (matching the hologram's own palette) plus a
            faint outer glow makes it a clearly readable, deliberate panel. */}
        <div className="mx-auto mb-6 flex max-h-[32vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-[#ffae42]/30 bg-black/40 shadow-[0_0_28px_-10px_rgba(255,174,66,0.45)] backdrop-blur-md">
          {/* min-h-0 overrides a flex child's default min-height:auto (its
              own content size) — without it, on a short enough window this
              wrapper (and ConversationPanel inside it) refused to shrink
              below their natural content height, so the footer row below
              got pushed past the box's max-h and silently clipped away by
              overflow-hidden — confirmed live as a real bug, not just a
              display glitch. With min-h-0, THIS shrinks first and
              ConversationPanel's own overflow-y-auto scrolls internally
              instead, guaranteeing the footer stays visible always. */}
          <div className="min-h-0 flex-1">
            <ConversationPanel compactEmptyHint />
          </div>

          {/* Hands-free + mic now live here, bottom-right of the chat box,
              instead of floating over the hologram — see the comment above
              this box's other JSX site for why that moved. shrink-0 so this
              row is never the one flexbox squeezes when space is tight. */}
          <div className="flex shrink-0 items-center justify-end gap-3 border-t border-white/[0.08] bg-black/20 px-4 py-3">
            <button
              type="button"
              onClick={() => setHandsFree((h) => !h)}
              className={`rounded-full border px-3 py-1.5 text-[11.5px] transition-colors ${
                handsFree
                  ? "border-primary/50 bg-primary/15 text-primary"
                  : "border-white/[0.12] bg-white/[0.05] text-white/60 hover:text-white/90"
              }`}
            >
              Hands-free {handsFree ? "ON" : "OFF"}
            </button>

            <div className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-primary/30 bg-black/60 shadow-[0_0_20px_-6px_color-mix(in_srgb,var(--color-primary)_55%,transparent)]">
              <MicButton supported={supported} listening={listening} disabled={micDisabled} onToggle={handleMicToggle} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
