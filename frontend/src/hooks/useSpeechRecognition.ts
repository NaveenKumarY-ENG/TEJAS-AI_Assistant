import { useCallback, useEffect, useRef, useState } from "react";
import { useAssistantStore } from "../store/assistantStore";

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
 * Push-to-talk voice input: click to start recording, click again to stop.
 * The recording is uploaded to /api/transcribe on stop and the resulting
 * text is handed to onFinalTranscript — same contract the old Web Speech
 * API version had, so callers don't need to change.
 */
export function useSpeechRecognition(onFinalTranscript: (text: string) => void, onError?: (message: string) => void) {
  const supported =
    typeof window !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";
  const [listening, setListening] = useState(false);
  const [processing, setProcessing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const setCoreState = useAssistantStore((s) => s.setCoreState);

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

  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
      releaseStream();
    };
  }, [releaseStream]);

  const start = useCallback(async () => {
    if (!supported) {
      onError?.("Voice input isn't available — this page needs to be served over localhost or https.");
      return;
    }
    if (listening || processing) return;

    console.info("[voice] Requesting microphone access...");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.error("[voice] Microphone access failed:", err);
      const name = err instanceof DOMException ? err.name : "";
      onError?.(ERROR_MESSAGES[name] ?? "Couldn't access the microphone.");
      return;
    }

    let recorder: MediaRecorder;
    try {
      const mimeType = pickMimeType();
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch (err) {
      console.error("[voice] MediaRecorder setup failed:", err);
      stream.getTracks().forEach((track) => track.stop());
      onError?.("Couldn't start recording audio in this browser.");
      return;
    }

    streamRef.current = stream;
    chunksRef.current = [];

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };

    recorder.onerror = (event) => {
      console.error("[voice] MediaRecorder error:", event);
      onError?.("Recording failed unexpectedly.");
    };

    recorder.onstop = async () => {
      console.info("[voice] Recording stopped, %d chunk(s) captured", chunksRef.current.length);
      releaseStream();
      setListening(false);
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
      chunksRef.current = [];

      if (blob.size < 1000) {
        console.warn("[voice] Recording too short (%d bytes) — skipping transcription", blob.size);
        setCoreState("idle");
        return;
      }

      setProcessing(true);
      setCoreState("thinking");
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), TRANSCRIBE_TIMEOUT_MS);
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
          onFinalTranscript(text);
        } else {
          onError?.("Didn't catch that — no speech detected.");
        }
      } catch (err) {
        console.error("[voice] Transcription request failed:", err);
        if (err instanceof DOMException && err.name === "AbortError") {
          onError?.("Transcription timed out — try again.");
        } else {
          onError?.("Couldn't reach the transcription service — is the backend server running?");
        }
      } finally {
        window.clearTimeout(timeout);
        setProcessing(false);
        setCoreState("idle");
      }
    };

    mediaRecorderRef.current = recorder;
    recorder.start();
    console.info("[voice] Recording started (mimeType=%s)", recorder.mimeType);
    setListening(true);
    setCoreState("listening");
  }, [supported, listening, processing, onFinalTranscript, onError, setCoreState, releaseStream]);

  const stop = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
  }, []);

  return { supported, listening, processing, interimText: "", start, stop };
}
