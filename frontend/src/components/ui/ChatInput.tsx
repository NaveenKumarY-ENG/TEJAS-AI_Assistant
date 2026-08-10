import { useRef, useState } from "react";
import { ArrowUp, Keyboard, Paperclip, Camera, SlidersHorizontal } from "lucide-react";
import { MicButton } from "../voice/MicButton";
import { useSpeechRecognition } from "../../hooks/useSpeechRecognition";
import { useAssistantStore } from "../../store/assistantStore";

interface ChatInputProps {
  disabled: boolean;
  onSend: (text: string, opts?: { speak?: boolean }) => void;
  onSoonClick: (label: string) => void;
  onVoiceError: (message: string) => void;
}

export function ChatInput({ disabled, onSend, onSoonClick, onVoiceError }: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Global mute toggle (TopBar's speaker icon) — TEJAS speaks every reply,
  // typed or voice, unless the user has muted it.
  const voiceOutputEnabled = useAssistantStore((s) => s.voiceOutputEnabled);

  const submitText = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed, { speak: voiceOutputEnabled });
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  // Voice replies auto-submit as soon as transcription completes — the
  // spoken query then shows up as the sent message in the conversation
  // itself, same as a real voice assistant (no extra step of reviewing text
  // in a box first). Push-to-talk: click the mic to start recording, click
  // again to stop and transcribe.
  const { supported, listening, processing, start, stop } = useSpeechRecognition(submitText, onVoiceError);

  const submit = () => submitText(value);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="relative mx-auto flex w-full max-w-2xl items-end gap-2 rounded-3xl border border-white/10 bg-white/[0.04] p-2 pl-4 shadow-[0_0_30px_-10px_rgba(0,229,255,0.25)] backdrop-blur-2xl transition-shadow focus-within:border-primary/50 focus-within:shadow-[0_0_40px_-8px_rgba(0,229,255,0.4)]"
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

      <button
        type="button"
        onClick={() => onSoonClick("Keyboard mode")}
        className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-white/45 transition-colors hover:text-white"
        aria-label="Keyboard mode"
      >
        <Keyboard size={16} strokeWidth={1.8} />
      </button>

      <textarea
        ref={textareaRef}
        rows={1}
        value={value}
        placeholder={listening || processing ? "" : "Ask me anything..."}
        className="max-h-40 flex-1 resize-none bg-transparent py-2 text-[15px] text-white placeholder:text-white/35 focus:outline-none"
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

      <MicButton
        supported={supported}
        listening={listening}
        disabled={disabled || processing}
        onToggle={() => (listening ? stop() : start())}
      />

      <button
        type="submit"
        disabled={disabled || !value.trim()}
        aria-label="Send message"
        className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-primary to-accent text-black shadow-[0_0_16px_rgba(0,229,255,0.6)] transition-transform hover:scale-105 disabled:cursor-not-allowed disabled:from-white/10 disabled:to-white/10 disabled:text-white/30 disabled:shadow-none"
      >
        <ArrowUp size={16} strokeWidth={2.2} />
      </button>
    </form>
  );
}
