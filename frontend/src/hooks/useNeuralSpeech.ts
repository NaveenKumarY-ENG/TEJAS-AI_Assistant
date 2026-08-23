import { useCallback, useEffect, useRef } from "react";
import type { TtsBoundarySignal } from "./useSpeechSynthesis";

export type { TtsBoundarySignal };

/**
 * Server-side neural voice (GPU-accelerated Kokoro, see agent/tts.py) —
 * same public shape as useSpeechSynthesis so callers can swap between them.
 * Sentences are POSTed to /api/tts one at a time (a sequential pump loop,
 * same as useSpeechSynthesis's recursive speakNext), played through a
 * single reused <audio> element, and queued in order despite each request
 * being an independent async round-trip.
 *
 * A per-sentence failure (network blip, backend not actually ready despite
 * /api/meta saying so, etc.) falls back to the browser's own
 * SpeechSynthesis for just that one sentence rather than dropping it —
 * same fail-soft philosophy as agent/transcription.py's openai->local STT
 * fallback. The pump loop keeps going either way, so one bad sentence never
 * stalls the rest of the reply.
 */
export function useNeuralSpeech(onQueueDrained?: () => void) {
  const supported = typeof window !== "undefined" && typeof fetch === "function" && typeof Audio !== "undefined";

  const queueRef = useRef<string[]>([]);
  const playingRef = useRef(false);
  // Bumped by stop() to invalidate any in-flight fetch/playback/fallback —
  // cheaper and more robust than trying to precisely cancel every awaited
  // step individually (fetch abort still happens too, via abortRef, but
  // this generation check is what actually stops a response that resolved
  // right as stop() was called from being played anyway).
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const rafRef = useRef<number | null>(null);
  const boundaryRef = useRef<TtsBoundarySignal | null>(null);
  const onQueueDrainedRef = useRef(onQueueDrained);
  // The settle functions for whichever playAudio() promise is currently
  // in flight, if any. stop() must call resolve() through this BEFORE
  // nulling audio.onended/onerror below — otherwise, when stop() is called
  // while audio is already playing (a common interrupt: sending a new
  // message, muting, switching sessions), the promise pump() is awaiting
  // never settles and that pump() call hangs forever instead of unwinding.
  const currentPlaybackRef = useRef<{ resolve: () => void; reject: (err: Error) => void } | null>(null);

  useEffect(() => {
    onQueueDrainedRef.current = onQueueDrained;
  }, [onQueueDrained]);

  useEffect(() => {
    if (!supported) return;
    const audio = new Audio();
    audioRef.current = audio;
    return () => {
      audio.pause();
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopPulse = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const startPulse = useCallback(() => {
    stopPulse();
    const tick = () => {
      const audio = audioRef.current;
      if (audio && !audio.paused) {
        boundaryRef.current = { ts: performance.now(), charIndex: 0 };
        rafRef.current = requestAnimationFrame(tick);
      } else {
        rafRef.current = null;
      }
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [stopPulse]);

  const playAudio = useCallback(
    (blob: Blob, generation: number): Promise<void> =>
      new Promise((resolve, reject) => {
        const audio = audioRef.current;
        if (!audio || generation !== generationRef.current) {
          resolve();
          return;
        }
        const url = URL.createObjectURL(blob);
        if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = url;

        const settle = (fn: () => void) => {
          currentPlaybackRef.current = null;
          fn();
        };
        currentPlaybackRef.current = {
          resolve: () => settle(resolve),
          reject: (err) => settle(() => reject(err)),
        };

        audio.onended = () => currentPlaybackRef.current?.resolve();
        audio.onerror = () => currentPlaybackRef.current?.reject(new Error("Audio playback failed"));
        audio.src = url;
        audio.play().then(startPulse).catch((err) => currentPlaybackRef.current?.reject(err));
      }),
    [startPulse]
  );

  // Rare-path fallback for a single sentence the backend couldn't voice —
  // a direct SpeechSynthesisUtterance, not the useSpeechSynthesis hook
  // (which owns its own independent queue/onQueueDrained), so this stays
  // inside the same sequential pump loop below with one await, no
  // cross-hook drain coordination to get wrong.
  const speakViaBrowserFallback = useCallback((text: string, generation: number): Promise<void> => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return Promise.resolve();
    return new Promise((resolve) => {
      if (generation !== generationRef.current) {
        resolve();
        return;
      }
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.onboundary = (event) => {
        boundaryRef.current = { ts: performance.now(), charIndex: event.charIndex };
      };
      utterance.onend = () => resolve();
      utterance.onerror = () => resolve();
      window.speechSynthesis.speak(utterance);
    });
  }, []);

  const pump = useCallback(async () => {
    const generation = generationRef.current;
    const next = queueRef.current.shift();

    if (!next) {
      playingRef.current = false;
      onQueueDrainedRef.current?.();
      return;
    }

    playingRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: next }),
        signal: controller.signal,
      });
      if (generation !== generationRef.current) return;
      if (!res.ok) throw new Error(`TTS request failed: ${res.status}`);
      const blob = await res.blob();
      if (generation !== generationRef.current) return;
      await playAudio(blob, generation);
    } catch (err) {
      if (generation !== generationRef.current) return;
      // Browsers block audio.play() with NotAllowedError until the page has
      // had a real user gesture — guaranteed to happen on the auto-spoken
      // greeting, which fires on a timer with no click/keypress yet. That's
      // not "TTS is broken," it's "not allowed to speak yet" — falling back
      // to the browser's own SpeechSynthesis here would still "work," but
      // with whatever voice the OS/browser defaults to (commonly female),
      // silently overriding the user's configured male neural voice for
      // that one sentence. Skipping it entirely (not speaking that sentence
      // at all) is the correct fail-soft behavior for THIS failure — every
      // real chat reply happens after the user has already clicked/typed,
      // so it never hits this path; only the unrequested auto-greeting can.
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        console.warn("Neural TTS blocked (no user interaction yet) — skipping this sentence rather than misvoicing it:", err);
      } else {
        console.error("Neural TTS failed, falling back to browser voice for this sentence:", err);
        await speakViaBrowserFallback(next, generation);
      }
    }

    if (generation === generationRef.current) pump();
  }, [playAudio, speakViaBrowserFallback]);

  const enqueue = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!supported || !trimmed) return;
      queueRef.current.push(trimmed);
      if (!playingRef.current) pump();
    },
    [supported, pump]
  );

  const stop = useCallback(() => {
    generationRef.current += 1;
    queueRef.current = [];
    playingRef.current = false;
    stopPulse();
    boundaryRef.current = null;

    abortRef.current?.abort();
    abortRef.current = null;

    // Settle any in-flight playAudio() promise before tearing down the
    // audio element's handlers below — see currentPlaybackRef's comment.
    // pump()'s generation check (already invalidated by the increment
    // above) makes this resolve a clean no-op resume, not a stray replay.
    currentPlaybackRef.current?.resolve();
    currentPlaybackRef.current = null;

    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
  }, [stopPulse]);

  return { supported, enqueue, stop, boundaryRef };
}
