import { useCallback, useRef } from "react";

/** Fired on each word/sentence boundary the browser reports while speaking.
 *  Browser TTS exposes no real audio buffer, so this timing signal is the
 *  closest approximation available for driving a "TTS mode" waveform —
 *  it's a cadence approximation, not actual audio amplitude data. */
export interface TtsBoundarySignal {
  ts: number;
  charIndex: number;
}

/**
 * Thin wrapper over the browser's native SpeechSynthesis API for spoken
 * replies. Text is fed in incrementally (one sentence at a time as it
 * completes during streaming) and queued so playback is continuous rather
 * than restarting per call.
 */
export function useSpeechSynthesis(onQueueDrained?: () => void) {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const queueRef = useRef<string[]>([]);
  const speakingRef = useRef(false);
  // Chrome has a long-standing bug where an utterance with nothing else
  // referencing it can get garbage-collected mid-speech, silently killing
  // playback. Keeping a live reference here works around it.
  const currentUtteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const boundaryRef = useRef<TtsBoundarySignal | null>(null);

  const speakNext = useCallback(() => {
    if (!supported) return;
    const next = queueRef.current.shift();

    if (!next) {
      const wasSpeaking = speakingRef.current;
      speakingRef.current = false;
      currentUtteranceRef.current = null;
      if (wasSpeaking) onQueueDrained?.();
      return;
    }

    const utterance = new SpeechSynthesisUtterance(next);
    utterance.rate = 1.02;
    utterance.pitch = 1;
    utterance.onend = speakNext;
    utterance.onerror = (event) => {
      console.error("Speech synthesis error:", event.error);
      speakNext();
    };
    utterance.onboundary = (event) => {
      boundaryRef.current = { ts: performance.now(), charIndex: event.charIndex };
    };

    currentUtteranceRef.current = utterance;
    speakingRef.current = true;
    window.speechSynthesis.speak(utterance);
  }, [supported, onQueueDrained]);

  const enqueue = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!supported || !trimmed) return;
      queueRef.current.push(trimmed);
      if (!speakingRef.current) speakNext();
    },
    [supported, speakNext]
  );

  const stop = useCallback(() => {
    if (!supported) return;
    queueRef.current = [];
    speakingRef.current = false;
    currentUtteranceRef.current = null;
    boundaryRef.current = null; // don't let a stale pulse linger past an interrupt
    window.speechSynthesis.cancel();
  }, [supported]);

  return { supported, enqueue, stop, boundaryRef };
}
