import { useCallback, useRef } from "react";

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
    window.speechSynthesis.cancel();
  }, [supported]);

  return { supported, enqueue, stop };
}
