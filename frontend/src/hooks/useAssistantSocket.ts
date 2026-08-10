import { useCallback, useEffect, useRef } from "react";
import { useAssistantStore } from "../store/assistantStore";
import { useSpeechSynthesis } from "./useSpeechSynthesis";
import { extractCompleteSentences } from "../utils/text";

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

  const { enqueue: speakText, stop: stopSpeakingRaw, supported: ttsSupported } = useSpeechSynthesis(() => {
    if (awaitingSpeechEndRef.current) {
      awaitingSpeechEndRef.current = false;
      finishStream();
    }
  });

  const stopSpeaking = useCallback(() => {
    awaitingSpeechEndRef.current = false;
    stopSpeakingRaw();
  }, [stopSpeakingRaw]);

  const connect = useCallback(
    (sessionOverride?: number | null) => {
      const targetSession = sessionOverride !== undefined ? sessionOverride : activeSessionIdRef.current;

      const proto = location.protocol === "https:" ? "wss" : "ws";
      const query = targetSession ? `?session_id=${targetSession}` : "";
      const socket = new WebSocket(`${proto}://${location.host}/ws${query}`);
      socketRef.current = socket;

      socket.onopen = () => setConnection("online");

      socket.onclose = () => {
        setConnection("reconnecting");
        setCoreState("idle");
        reconnectTimer.current = window.setTimeout(() => connect(), 2000);
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
            break;
        }
      };
    },
    [
      appendToStream,
      beginAssistantStream,
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
      stopSpeaking();
      socketRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchTo = useCallback(
    (sessionOverride: number | null) => {
      if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current);
      streamIdRef.current = null;
      stopSpeaking();
      speakRepliesRef.current = false;
      speechBufferRef.current = "";
      spokeAnythingRef.current = false;
      reset();
      socketRef.current?.close();
      connect(sessionOverride);
    },
    [connect, reset, stopSpeaking]
  );

  const startNewChat = useCallback(() => switchTo(null), [switchTo]);
  const openSession = useCallback((id: number) => switchTo(id), [switchTo]);

  const sendMessage = useCallback(
    (text: string, opts?: { speak?: boolean }) => {
      const trimmed = text.trim();
      if (!trimmed || socketRef.current?.readyState !== WebSocket.OPEN) return false;

      stopSpeaking(); // interrupt anything still playing from a previous turn
      speakRepliesRef.current = !!opts?.speak;
      speechBufferRef.current = "";
      spokeAnythingRef.current = false;

      addUserMessage(trimmed);
      socketRef.current.send(JSON.stringify({ text: trimmed }));
      return true;
    },
    [addUserMessage, stopSpeaking]
  );

  return { sendMessage, startNewChat, openSession, stopSpeaking };
}
