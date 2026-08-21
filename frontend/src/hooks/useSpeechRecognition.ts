import { useCallback, useEffect, useRef, useState } from "react";
import { useAssistantStore } from "../store/assistantStore";
import { attachMicAnalyser, readRmsLevel, type MicAnalyserHandle } from "../utils/audio";
import {
  ERROR_DISPLAY_MS,
  MIC_ANALYSER_ENABLED,
  SILENCE_RMS_THRESHOLD,
  SILENCE_TIMEOUT_MS,
  TRANSCRIPT_REVEAL_MS,
  VAD_AUTO_STOP_ENABLED,
} from "../constants/voice";

// Chrome's built-in SpeechRecognition ships audio to Google's speech service
// over the network; when that path is blocked or unreliable (firewall, VPN,
// regional endpoint issues) it fails with a generic "no-speech" error even
// though the mic itself works fine. Recording locally and transcribing via
// our own backend (agent/transcription.py, local Whisper by default) avoids
// that black box entirely and works offline.

const ERROR_MESSAGES: Record<string, string> = {
  NotFoundError: "No microphone was found on this device.",
  NotAllowedError: "Microphone access was denied — allow it in your browser's site settings to use voice input.",
  SecurityError: "Microphone access was denied — allow it in your browser's site settings to use voice input.",
  NotReadableError: "The microphone is already in use by another app.",
};

// First-ever transcription request can be much slower than normal: the
// backend lazily downloads the local Whisper model on first use. A too-short
// timeout would misreport that one-time delay as a broken service.
const TRANSCRIBE_TIMEOUT_MS = 90_000;

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type));
}

/**
 * Voice input: click to start recording. Stops either automatically after
 * a short silence (voice activity detection) or on a manual click — both
 * paths converge on the same MediaRecorder.stop(), so cleanup only lives in
 * one place (recorder.onstop). The recording is uploaded to /api/transcribe
 * on stop and the resulting text is handed to onFinalTranscript.
 */
export function useSpeechRecognition(
  onFinalTranscript: (text: string) => void,
  onError?: (message: string) => void,
  options?: { vadAutoStop?: boolean }
) {
  const vadAutoStop = options?.vadAutoStop ?? VAD_AUTO_STOP_ENABLED;
  const supported =
    typeof window !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";
  const [listening, setListening] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [revealText, setRevealText] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const setCoreState = useAssistantStore((s) => s.setCoreState);

  // Voice activity detection — real mic-level analysis to auto-stop after
  // a period of silence, instead of requiring a second click.
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioHandleRef = useRef<MicAnalyserHandle | null>(null);
  const vadFrameRef = useRef<number | null>(null);
  const hasDetectedSpeechRef = useRef(false);
  const lastLoudTsRef = useRef(0);

  const errorTimeoutRef = useRef<number | null>(null);
  const revealTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    if (!supported) {
      console.warn(
        "[voice] Not supported in this context — navigator.mediaDevices/MediaRecorder unavailable. " +
          "This usually means the page isn't served over localhost or https (a 'secure context' is required)."
      );
    }
  }, [supported]);

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const teardownAudio = useCallback(() => {
    if (vadFrameRef.current !== null) {
      cancelAnimationFrame(vadFrameRef.current);
      vadFrameRef.current = null;
    }
    audioHandleRef.current?.dispose();
    audioHandleRef.current = null;
    analyserRef.current = null;
  }, []);

  const enterError = useCallback(
    (message: string) => {
      onError?.(message); // existing toast surfacing — kept, additive
      setCoreState("error");
      if (errorTimeoutRef.current) window.clearTimeout(errorTimeoutRef.current);
      errorTimeoutRef.current = window.setTimeout(() => setCoreState("idle"), ERROR_DISPLAY_MS);
    },
    [onError, setCoreState]
  );

  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
      releaseStream();
      teardownAudio();
      if (errorTimeoutRef.current) window.clearTimeout(errorTimeoutRef.current);
      if (revealTimeoutRef.current) window.clearTimeout(revealTimeoutRef.current);
    };
  }, [releaseStream, teardownAudio]);

  const start = useCallback(async () => {
    if (!supported) {
      enterError("Voice input isn't available — this page needs to be served over localhost or https.");
      return;
    }
    if (listening || processing) return;

    if (errorTimeoutRef.current) {
      window.clearTimeout(errorTimeoutRef.current);
      errorTimeoutRef.current = null;
    }

    console.info("[voice] Requesting microphone access...");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.error("[voice] Microphone access failed:", err);
      const name = err instanceof DOMException ? err.name : "";
      enterError(ERROR_MESSAGES[name] ?? "Couldn't access the microphone.");
      return;
    }

    let recorder: MediaRecorder;
    try {
      const mimeType = pickMimeType();
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch (err) {
      console.error("[voice] MediaRecorder setup failed:", err);
      stream.getTracks().forEach((track) => track.stop());
      enterError("Couldn't start recording audio in this browser.");
      return;
    }

    streamRef.current = stream;
    chunksRef.current = [];

    // Best-effort — if this fails (rare browser edge case) or is disabled
    // via MIC_ANALYSER_ENABLED, voice activity detection and the mic
    // waveform just don't activate; recording itself still works via the
    // manual click-to-stop path below.
    if (MIC_ANALYSER_ENABLED) {
      try {
        audioHandleRef.current = attachMicAnalyser(stream);
        analyserRef.current = audioHandleRef.current.analyser;
      } catch (err) {
        console.warn("[voice] Mic analyser setup failed — VAD/waveform disabled for this recording:", err);
      }
    }

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };

    recorder.onerror = (event) => {
      console.error("[voice] MediaRecorder error:", event);
      enterError("Recording failed unexpectedly.");
    };

    recorder.onstop = async () => {
      console.info("[voice] Recording stopped, %d chunk(s) captured", chunksRef.current.length);
      releaseStream();
      teardownAudio();
      setListening(false);
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
      chunksRef.current = [];

      if (blob.size < 1000) {
        console.warn("[voice] Recording too short (%d bytes) — skipping transcription", blob.size);
        setCoreState("idle");
        return;
      }

      setProcessing(true);
      setCoreState("processing"); // STT upload/transcription — distinct from LLM "thinking"
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), TRANSCRIBE_TIMEOUT_MS);
      let hadError = false;
      try {
        console.info("[voice] Uploading %d bytes for transcription...", blob.size);
        const form = new FormData();
        form.append("audio", blob, "speech.webm");
        const res = await fetch("/api/transcribe", { method: "POST", body: form, signal: controller.signal });
        if (!res.ok) {
          const detail = await res.text().catch(() => "");
          throw new Error(`Transcription failed (${res.status}): ${detail}`);
        }
        const data = await res.json();
        const text = (data.text ?? "").trim();
        console.info("[voice] Transcription result:", JSON.stringify(text));
        if (text) {
          // Fire immediately rather than delaying for the reveal pill —
          // the reveal is a HUD flourish, not a gate on responsiveness.
          setRevealText(text);
          onFinalTranscript(text);
          if (revealTimeoutRef.current) window.clearTimeout(revealTimeoutRef.current);
          revealTimeoutRef.current = window.setTimeout(() => setRevealText(null), TRANSCRIPT_REVEAL_MS);
        } else {
          hadError = true;
          enterError("Didn't catch that — no speech detected.");
        }
      } catch (err) {
        hadError = true;
        console.error("[voice] Transcription request failed:", err);
        if (err instanceof DOMException && err.name === "AbortError") {
          enterError("Transcription timed out — try again.");
        } else {
          enterError("Couldn't reach the transcription service — is the backend server running?");
        }
      } finally {
        window.clearTimeout(timeout);
        setProcessing(false);
        // Only clear OUR OWN "processing" state — onFinalTranscript (above)
        // synchronously sends the message and advances coreState to
        // "thinking", and forcing "idle" unconditionally here would stomp
        // that if it ever renders in between (currently masked by React's
        // automatic batching coalescing both updates into one commit, but
        // fragile — e.g. the first real await added to that send path would
        // expose it as the mic/HUD flashing "idle" mid-turn).
        if (!hadError && useAssistantStore.getState().coreState === "processing") setCoreState("idle");
      }
    };

    mediaRecorderRef.current = recorder;
    recorder.start();
    console.info("[voice] Recording started (mimeType=%s)", recorder.mimeType);
    setListening(true);
    setCoreState("listening");

    // Voice activity detection: auto-stop after SILENCE_TIMEOUT_MS of quiet
    // that follows detected speech (so it never fires on the silence before
    // the user starts talking, only after). Gated off by default — see
    // VAD_AUTO_STOP_ENABLED in constants/voice.ts.
    if (vadAutoStop && analyserRef.current) {
      hasDetectedSpeechRef.current = false;
      lastLoudTsRef.current = performance.now();
      const buffer = new Uint8Array(analyserRef.current.fftSize);
      const watchSilence = () => {
        const analyser = analyserRef.current;
        if (!analyser) return; // torn down already
        const level = readRmsLevel(analyser, buffer);
        const now = performance.now();
        if (level > SILENCE_RMS_THRESHOLD) {
          hasDetectedSpeechRef.current = true;
          lastLoudTsRef.current = now;
        }
        if (hasDetectedSpeechRef.current && now - lastLoudTsRef.current > SILENCE_TIMEOUT_MS) {
          if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
          return;
        }
        vadFrameRef.current = requestAnimationFrame(watchSilence);
      };
      vadFrameRef.current = requestAnimationFrame(watchSilence);
    }
  }, [supported, listening, processing, onFinalTranscript, enterError, setCoreState, releaseStream, teardownAudio, vadAutoStop]);

  const stop = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
  }, []);

  // Unlike stop() — which finalizes the recording through the normal
  // transcribe-and-send pipeline (recorder.onstop) — this discards whatever
  // was captured so far without transcribing or sending it. Needed for
  // "leave voice mode mid-sentence" style exits, where stop() would
  // otherwise still upload and send a half-spoken utterance after the user
  // has already navigated away.
  const cancel = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state === "recording") {
      recorder.onstop = null; // detach before stopping so the transcribe/send pipeline never runs
      recorder.stop();
    }
    releaseStream();
    teardownAudio();
    setListening(false);
    setProcessing(false);
    // Only clear coreState if it's currently a state THIS hook owns — a
    // caller (VoiceMode's exit handler) may call cancel() while the
    // assistant is "thinking"/"searching"/"speaking" from a turn that's
    // still legitimately in flight in the background, which must be left
    // alone rather than stomped to "idle" just because voice input is
    // being torn down.
    const owned = useAssistantStore.getState().coreState;
    if (owned === "listening" || owned === "processing") setCoreState("idle");
  }, [releaseStream, teardownAudio, setCoreState]);

  return { supported, listening, processing, interimText: "", start, stop, cancel, analyserRef, revealText };
}
