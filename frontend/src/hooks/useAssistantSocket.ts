import { useCallback, useEffect, useRef } from "react";
import { useAssistantStore } from "../store/assistantStore";
import { useSpeechSynthesis } from "./useSpeechSynthesis";
import { extractCompleteSentences } from "../utils/text";
import {
  ERROR_DISPLAY_MS,
  RECONNECT_BASE_MS,
  RECONNECT_FACTOR,
  RECONNECT_JITTER_MS,
  RECONNECT_MAX_MS,
} from "../constants/voice";

type ServerEvent =
  | { type: "ready"; session_id: number; history: Array<{ role: string; content: string }> }
  | { type: "tool"; name: string }
  | { type: "chunk"; text: string }
  | { type: "done" }
  | { type: "error"; message: string };

/**
 * Owns the WebSocket connection to the FastAPI /ws endpoint, translates its
 * events into assistant-store state transitions, and — when a message was
 * sent via voice — speaks the reply aloud as it streams in.
 *
 * Session lifecycle: the very first connect (page load) always starts a
 * brand new, empty session — closing and reopening the tab should not
 * silently resume yesterday's conversation. If the socket drops and
 * reconnects on its own (a network blip), it reconnects to the SAME session
 * so an in-progress chat isn't lost. startNewChat() and openSession(id) are
 * the only ways to deliberately switch sessions, mirroring a "New chat" /
 * chat-history picker UI.
 *
 * Voice replies: sendMessage(text, { speak: true }) marks the upcoming
 * reply for speech. Completed sentences are spoken progressively as they
 * arrive (not word-by-word, and not only after the full reply finishes).
 * coreState only returns to "idle" once BOTH the text stream AND the speech
 * queue have finished, so the hologram's speaking animation stays in sync
 * with actual audio instead of cutting out while TEJAS is still talking.
 */
export function useAssistantSocket() {
  const socketRef = useRef<WebSocket | null>(null);
  const streamIdRef = useRef<string | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  // Exponential backoff attempt counter — resets to 0 on a successful open
  // or a deliberate session switch, so an unrelated later network blip
  // doesn't inherit a long delay from an earlier outage.
  const reconnectAttemptRef = useRef(0);
  // Auto-clears coreState:"error" back to "idle" after ERROR_DISPLAY_MS —
  // tracked so a stale timer can't stomp a legitimate later state if the
  // user starts a new turn while the error is still showing.
  const errorClearTimer = useRef<number | null>(null);
  // The session currently open in this tab. Undefined means "not established
  // yet" (first connect); null means "force a brand new session".
  const activeSessionIdRef = useRef<number | null | undefined>(undefined);

  const speakRepliesRef = useRef(false);
  const speechBufferRef = useRef("");
  const spokeAnythingRef = useRef(false);
  const awaitingSpeechEndRef = useRef(false);

  const {
    setConnection,
    setSession,
    hydrateHistory,
    addUserMessage,
    beginAssistantStream,
    appendToStream,
    endStream,
    pushTool,
    resolveTools,
    setCoreState,
    reset,
  } = useAssistantStore.getState();

  const finishStream = useCallback(() => {
    streamIdRef.current = null;
    endStream();
  }, [endStream]);

  const clearPendingErrorState = useCallback(() => {
    if (errorClearTimer.current) {
      window.clearTimeout(errorClearTimer.current);
      errorClearTimer.current = null;
    }
  }, []);

  const {
    enqueue: speakText,
    stop: stopSpeakingRaw,
    supported: ttsSupported,
    boundaryRef: ttsBoundaryRef,
  } = useSpeechSynthesis(() => {
    if (awaitingSpeechEndRef.current) {
      awaitingSpeechEndRef.current = false;
      finishStream();
    }
  });

  const stopSpeaking = useCallback(() => {
    // Cancelling the CURRENT utterance isn't enough on its own: the backend
    // keeps streaming the rest of this turn's reply regardless of whether
    // we muted, and speakRepliesRef gated whether each new "chunk" event
    // gets spoken. Without resetting it here, every sentence that finishes
    // streaming AFTER this call still gets enqueued and spoken — the mute
    // silences what's playing right now but not what arrives next.
    const wasAwaitingSpeechEnd = awaitingSpeechEndRef.current;
    awaitingSpeechEndRef.current = false;
    speakRepliesRef.current = false;
    speechBufferRef.current = "";
    spokeAnythingRef.current = false;
    stopSpeakingRaw();
    // If we were deferring finishStream() until the speech queue drained
    // naturally (see the "done" case below), a manual stop() never fires
    // that drain callback — finish it now so coreState doesn't stay stuck
    // on "speaking" forever.
    if (wasAwaitingSpeechEnd) finishStream();
  }, [stopSpeakingRaw, finishStream]);

  const connect = useCallback(
    (sessionOverride?: number | null) => {
      const targetSession = sessionOverride !== undefined ? sessionOverride : activeSessionIdRef.current;

      const proto = location.protocol === "https:" ? "wss" : "ws";
      const query = targetSession ? `?session_id=${targetSession}` : "";
      const socket = new WebSocket(`${proto}://${location.host}/ws${query}`);
      socketRef.current = socket;

      socket.onopen = () => {
        setConnection("online");
        reconnectAttemptRef.current = 0;
      };

      socket.onclose = () => {
        setConnection("reconnecting");
        setCoreState("idle");
        const attempt = reconnectAttemptRef.current;
        const backoff = Math.min(RECONNECT_BASE_MS * RECONNECT_FACTOR ** attempt, RECONNECT_MAX_MS);
        reconnectAttemptRef.current = attempt + 1;
        reconnectTimer.current = window.setTimeout(() => connect(), backoff + Math.random() * RECONNECT_JITTER_MS);
      };

      socket.onerror = () => setConnection("offline");

      socket.onmessage = (event) => {
        const msg: ServerEvent = JSON.parse(event.data);

        switch (msg.type) {
          case "ready":
            activeSessionIdRef.current = msg.session_id;
            setSession(msg.session_id);
            if (msg.history?.length) hydrateHistory(msg.history);
            break;

          case "tool":
            resolveTools();
            pushTool(msg.name);
            // web_search is the one tool worth a distinct HUD state; the
            // exact string is coupled to tools/web_search.py's `name`.
            setCoreState(msg.name === "web_search" ? "searching" : "thinking");
            break;

          case "chunk":
            resolveTools();
            if (!streamIdRef.current) {
              streamIdRef.current = beginAssistantStream();
            }
            appendToStream(streamIdRef.current, msg.text);

            if (speakRepliesRef.current && ttsSupported) {
              speechBufferRef.current += msg.text;
              const { complete, remainder } = extractCompleteSentences(speechBufferRef.current);
              complete.forEach((sentence) => {
                speakText(sentence);
                spokeAnythingRef.current = true;
              });
              speechBufferRef.current = remainder;
            }
            break;

          case "done": {
            resolveTools();

            if (speakRepliesRef.current && ttsSupported) {
              const leftover = speechBufferRef.current.trim();
              speechBufferRef.current = "";
              if (leftover) {
                speakText(leftover);
                spokeAnythingRef.current = true;
              }
            }

            const shouldWaitForSpeech = speakRepliesRef.current && ttsSupported && spokeAnythingRef.current;
            speakRepliesRef.current = false;
            spokeAnythingRef.current = false;

            if (shouldWaitForSpeech) {
              awaitingSpeechEndRef.current = true; // finishStream() runs once the speech queue drains
            } else {
              finishStream();
            }
            break;
          }

          case "error":
            resolveTools();
            stopSpeaking();
            speakRepliesRef.current = false;
            speechBufferRef.current = "";
            spokeAnythingRef.current = false;
            if (!streamIdRef.current) streamIdRef.current = beginAssistantStream();
            appendToStream(streamIdRef.current, `\n\n${msg.message}`);
            finishStream();
            // Additive to the inline chat text above — also flips the HUD
            // to a distinct red "error" state, auto-clearing back to idle.
            clearPendingErrorState();
            setCoreState("error");
            errorClearTimer.current = window.setTimeout(() => setCoreState("idle"), ERROR_DISPLAY_MS);
            break;
        }
      };
    },
    [
      appendToStream,
      beginAssistantStream,
      clearPendingErrorState,
      finishStream,
      hydrateHistory,
      pushTool,
      resolveTools,
      setConnection,
      setCoreState,
      setSession,
      speakText,
      stopSpeaking,
      ttsSupported,
    ]
  );

  useEffect(() => {
    connect(null); // fresh session on page load, never auto-resume
    return () => {
      if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current);
      clearPendingErrorState();
      stopSpeaking();
      socketRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchTo = useCallback(
    (sessionOverride: number | null) => {
      if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current);
      reconnectAttemptRef.current = 0;
      clearPendingErrorState();
      streamIdRef.current = null;
      stopSpeaking();
      speakRepliesRef.current = false;
      speechBufferRef.current = "";
      spokeAnythingRef.current = false;
      reset();
      socketRef.current?.close();
      connect(sessionOverride);
    },
    [clearPendingErrorState, connect, reset, stopSpeaking]
  );

  const startNewChat = useCallback(() => switchTo(null), [switchTo]);
  const openSession = useCallback((id: number) => switchTo(id), [switchTo]);

  // Speaks arbitrary text outside the chat-reply pipeline above — no
  // coreState changes, no streaming/interrupt bookkeeping, and no mute
  // check of its own (that's the caller's job, e.g. App.tsx's one-time
  // greeting on load checks voiceOutputEnabled before calling this).
  const speak = useCallback(
    (text: string) => {
      if (ttsSupported) speakText(text);
    },
    [speakText, ttsSupported]
  );

  const sendMessage = useCallback(
    (text: string, opts?: { speak?: boolean }) => {
      const trimmed = text.trim();
      if (!trimmed || socketRef.current?.readyState !== WebSocket.OPEN) return false;

      clearPendingErrorState(); // a new turn supersedes any lingering error display
      stopSpeaking(); // interrupt anything still playing from a previous turn
      speakRepliesRef.current = !!opts?.speak;
      speechBufferRef.current = "";
      spokeAnythingRef.current = false;

      addUserMessage(trimmed);
      socketRef.current.send(JSON.stringify({ text: trimmed }));
      return true;
    },
    [addUserMessage, clearPendingErrorState, stopSpeaking]
  );

  return { sendMessage, startNewChat, openSession, stopSpeaking, ttsBoundaryRef, speak };
}
