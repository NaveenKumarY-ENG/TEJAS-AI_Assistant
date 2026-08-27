import { useEffect, useRef, useState } from "react";
import { ArrowUp, Keyboard, Paperclip, Camera, SlidersHorizontal } from "lucide-react";
import { MicButton } from "../voice/MicButton";
import { VoiceWaveform, type WaveformMode } from "../voice/VoiceWaveform";
import { useSpeechRecognition } from "../../hooks/useSpeechRecognition";
import { useAssistantStore, type CoreState } from "../../store/assistantStore";
import type { TtsBoundarySignal } from "../../hooks/useSpeechSynthesis";

interface ChatInputProps {
  disabled: boolean;
  coreState: CoreState;
  ttsBoundaryRef: React.RefObject<TtsBoundarySignal | null>;
  stopSpeaking: () => void;
  onSend: (text: string, opts?: { speak?: boolean }) => void;
  onSoonClick: (label: string) => void;
  onVoiceError: (message: string) => void;
  /** Lets AssistantCore's hologram react live to real mic volume — see its
   *  own micAnalyserRef doc. Filled in once, on mount, with a getter closing
   *  over this hook's own analyserRef (whose .current AnalyserNode is
   *  created/destroyed per recording session, this wrapper ref's identity
   *  never changes). Optional — omitted entirely on any screen with no
   *  hologram to drive (there is none today, but this shouldn't be required). */
  exposeMicAnalyserRef?: React.MutableRefObject<(() => AnalyserNode | null) | null>;
}

export function ChatInput({
  disabled,
  coreState,
  ttsBoundaryRef,
  stopSpeaking,
  onSend,
  onSoonClick,
  onVoiceError,
  exposeMicAnalyserRef,
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Global mute toggle (TopBar's speaker icon) — TEJAS speaks every reply,
  // typed or voice, unless the user has muted it.
  const voiceOutputEnabled = useAssistantStore((s) => s.voiceOutputEnabled);

  // A sidebar quick action (e.g. "Quick calculation") asked for text to be
  // placed here for the user to finish, rather than sent immediately — see
  // QuickActions.tsx's `autoSend: false`. Store-driven since QuickActions
  // lives in the sidebar, a sibling of this component, not an ancestor.
  const pendingInput = useAssistantStore((s) => s.pendingInput);
  const clearPendingInput = useAssistantStore((s) => s.clearPendingInput);
  useEffect(() => {
    if (pendingInput === null) return;
    setValue(pendingInput);
    clearPendingInput();
    const el = textareaRef.current;
    if (!el) return;
    // React's state update above won't reach the DOM until after this
    // effect returns, so focus/selection below would otherwise act on the
    // textarea's stale (pre-update) value — write it directly first.
    el.value = pendingInput;
    el.focus();
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    el.setSelectionRange(pendingInput.length, pendingInput.length);
  }, [pendingInput, clearPendingInput]);

  const submitText = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed, { speak: voiceOutputEnabled });
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  // Deliberately bypasses the `disabled` guard above: recorder.onstop (set
  // up inside useSpeechRecognition's start()) permanently closes over
  // whichever onFinalTranscript reference existed at the moment start() was
  // called, not a live one. By the time transcription finishes, coreState
  // has already moved to "processing" (disabled=true) for EVERY voice
  // submission — so gating on `disabled` here would silently drop voice
  // messages whenever the click that started recording happened while
  // disabled was true (e.g. the click-to-interrupt-while-speaking path).
  // sendMessage() (useAssistantSocket) already guards the real invariant
  // that matters here (the socket must be open).
  const submitVoiceText = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed, { speak: voiceOutputEnabled });
  };

  // Voice replies auto-submit as soon as transcription completes — the
  // spoken query then shows up as the sent message in the conversation
  // itself, same as a real voice assistant (no extra step of reviewing text
  // in a box first). Recording auto-stops on silence (voice activity
  // detection) or on a manual click of the mic.
  const { supported, listening, processing, start, stop, analyserRef, revealText } = useSpeechRecognition(
    submitVoiceText,
    onVoiceError
  );

  useEffect(() => {
    if (exposeMicAnalyserRef) exposeMicAnalyserRef.current = () => analyserRef.current;
    // analyserRef itself is a stable ref identity for this hook's lifetime —
    // only needs wiring up once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exposeMicAnalyserRef]);

  const submit = () => submitText(value);

  // Clicking the mic while TEJAS is speaking interrupts playback and starts
  // listening immediately, instead of requiring two separate clicks.
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

  const waveformMode: WaveformMode = listening ? "microphone" : coreState === "speaking" ? "tts" : "idle";

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      // Text box on its own row, toolbar (keyboard/attach/camera/settings on
      // the left, waveform/mic/send on the right) on a second row below —
      // previously everything lived in one items-end row, so a growing
      // multi-line textarea pushed the icon row down with it and the whole
      // thing read as congested. Confirmed live.
      className="relative mx-auto flex w-full max-w-2xl flex-col gap-1.5 rounded-3xl border border-white/10 bg-white/[0.04] p-3 shadow-[0_0_30px_-10px_color-mix(in_srgb,var(--color-primary)_25%,transparent)] backdrop-blur-2xl transition-shadow focus-within:border-primary/50 focus-within:shadow-[0_0_40px_-8px_color-mix(in_srgb,var(--color-primary)_40%,transparent)]"
    >
      {listening && (
        <div className="pointer-events-none absolute -top-10 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-black/70 px-3.5 py-1.5 text-[12px] text-primary/80 backdrop-blur-md">
          Listening… click mic to stop
        </div>
      )}
      {processing && (
        <div className="pointer-events-none absolute -top-10 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-black/70 px-3.5 py-1.5 text-[12px] text-white/70 backdrop-blur-md">
          Transcribing…
        </div>
      )}
      {!listening && !processing && revealText && (
        <div className="pointer-events-none absolute -top-10 left-1/2 max-w-[90%] -translate-x-1/2 truncate whitespace-nowrap rounded-full bg-black/70 px-3.5 py-1.5 text-[12px] text-primary backdrop-blur-md animate-[enter_.3s_ease-out]">
          "{revealText}"
        </div>
      )}

      <textarea
        ref={textareaRef}
        rows={1}
        value={value}
        disabled={disabled}
        placeholder={listening || processing ? "" : "Ask me anything..."}
        className="max-h-40 w-full resize-none bg-transparent px-1 py-1 text-[15px] text-white placeholder:text-white/35 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
        onChange={(e) => {
          setValue(e.target.value);
          e.target.style.height = "auto";
          e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />

      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => onSoonClick("Keyboard mode")}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-white/45 transition-colors hover:text-white"
            aria-label="Keyboard mode"
          >
            <Keyboard size={16} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            onClick={() => onSoonClick("File upload")}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-white/45 transition-colors hover:text-white"
            aria-label="Upload file"
          >
            <Paperclip size={16} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            onClick={() => onSoonClick("Camera")}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-white/45 transition-colors hover:text-white"
            aria-label="Camera"
          >
            <Camera size={16} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            onClick={() => onSoonClick("Input settings")}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-white/45 transition-colors hover:text-white"
            aria-label="Settings"
          >
            <SlidersHorizontal size={16} strokeWidth={1.8} />
          </button>
        </div>

        <div className="flex items-center gap-2">
          <VoiceWaveform mode={waveformMode} analyserRef={analyserRef} boundaryRef={ttsBoundaryRef} className="hidden sm:block" />

          <MicButton
            supported={supported}
            listening={listening}
            disabled={(disabled && coreState !== "speaking") || processing}
            onToggle={handleMicToggle}
          />

          <button
            type="submit"
            disabled={disabled || !value.trim()}
            aria-label="Send message"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-primary to-accent text-black shadow-[0_0_16px_color-mix(in_srgb,var(--color-primary)_60%,transparent)] transition-transform hover:scale-105 disabled:cursor-not-allowed disabled:from-white/10 disabled:to-white/10 disabled:text-white/30 disabled:shadow-none"
          >
            <ArrowUp size={16} strokeWidth={2.2} />
          </button>
        </div>
      </div>
    </form>
  );
}
