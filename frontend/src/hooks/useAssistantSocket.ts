import { useCallback, useEffect, useRef } from "react";
import { useAssistantStore } from "../store/assistantStore";
import { useSpeechSynthesis } from "./useSpeechSynthesis";
import { useNeuralSpeech } from "./useNeuralSpeech";
import { extractCompleteSentences, humanizeForSpeech, stripMarkdownForSpeech } from "../utils/text";
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
  | { type: "tool_result"; name: string; result: string }
  | { type: "chunk"; text: string }
  | { type: "done" }
  | { type: "error"; message: string };

// Matches tools/knowledge_tool.py's SearchKnowledgeTool.run() result format
// ("From 'filename': text\n\nFrom 'filename2': text2") — pulled out here
// rather than sending structured data over the wire, since the tool's
// return value is already a plain string handed straight to the LLM.
const KNOWLEDGE_SOURCE_RE = /From '([^']+)':/g;

function extractKnowledgeSources(result: string): string[] {
  return [...new Set([...result.matchAll(KNOWLEDGE_SOURCE_RE)].map((m) => m[1]))];
}

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
 *
 * Voice engine: `ttsAvailable` (from /api/meta's tts_available, resolved
 * asynchronously by the caller) picks between the neural (GPU, backend)
 * and browser (SpeechSynthesis) engines. Both are instantiated unconditionally
 * (cheap while idle, and required by the Rules of Hooks), but which one
 * actually speaks is decided per-call via ttsAvailableRef rather than by
 * swapping which functions `connect`'s WebSocket closures capture — those
 * closures are only rebuilt on reconnect, so a value that can flip mid-session
 * (the /api/meta fetch resolving after the socket's first connect) has to be
 * read live through a ref, not captured at closure-creation time, or a
 * session that connects before the fetch resolves would be stuck on the
 * wrong engine for its entire lifetime.
 */
export function useAssistantSocket(ttsAvailable: boolean) {
  const socketRef = useRef<WebSocket | null>(null);
  const streamIdRef = useRef<string | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  // Exponential backoff attempt counter — resets to 0 on a successful open
  // or a deliberate session switch, so an unrelated later network blip
  // doesn't inherit a long delay from an earlier outage.
  const reconnectAttemptRef = useRef(0);
  // Set right before a deliberate close() with no replacement socket coming
  // (component unmount) — that socket's onclose still fires asynchronously
  // afterward (a close is a handshake round-trip, not instant) and needs to
  // know not to schedule a reconnect nothing will ever use. switchTo's case
  // (close + immediately open a replacement) is instead handled directly in
  // onclose by comparing against socketRef.current — see its comment.
  // Missing this distinction entirely was a real bug, confirmed live:
  // every "New Chat"/session switch flashed "Reconnecting" about a second
  // later and silently leaked an orphaned extra socket + backend session,
  // reproducing deterministically in dev under StrictMode's mount/unmount/
  // remount too.
  const intentionalCloseRef = useRef(false);
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

  const ttsAvailableRef = useRef(ttsAvailable);
  useEffect(() => {
    ttsAvailableRef.current = ttsAvailable;
  }, [ttsAvailable]);

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
    setToolSources,
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

  const onSpeechQueueDrained = useCallback(() => {
    if (awaitingSpeechEndRef.current) {
      awaitingSpeechEndRef.current = false;
      finishStream();
    }
  }, [finishStream]);

  const browserTts = useSpeechSynthesis(onSpeechQueueDrained);
  const neuralTts = useNeuralSpeech(onSpeechQueueDrained);

  // Stable regardless of which engine is active — see the ttsAvailableRef
  // note above for why the routing decision has to happen inside these
  // function bodies (read live) rather than by picking which hook's
  // `.enqueue`/`.stop` this const points to. Depending on the inner
  // functions directly (not the whole browserTts/neuralTts objects, which
  // are fresh literals every render) is what keeps these two identity-stable
  // across renders.
  const speakText = useCallback(
    (text: string) => {
      // Rewrites an all-caps assistant name (e.g. "TEJAS") to Title Case
      // before it reaches either engine — see humanizeForSpeech's own
      // comment for why (both TTS engines otherwise spell it out as an
      // acronym). Read live via getState() rather than a subscribed value
      // so this stays correct even if ASSISTANT_NAME is ever changed
      // without needing speakText's identity to depend on it.
      const spoken = humanizeForSpeech(stripMarkdownForSpeech(text), useAssistantStore.getState().assistantName);
      (ttsAvailableRef.current ? neuralTts.enqueue : browserTts.enqueue)(spoken);
    },
    // Depending on the .enqueue functions themselves (stable across renders)
    // rather than the browserTts/neuralTts objects (fresh literals every
    // render) is deliberate, not a missing-deps oversight — see the comment
    // above. react-hooks' exhaustive-deps lint doesn't reason well about
    // member-expression deps here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [neuralTts.enqueue, browserTts.enqueue]
  );
  const stopSpeakingRaw = useCallback(() => {
    // Stop both unconditionally (cheap, idempotent) rather than only the
    // "active" one — guards the edge case where ttsAvailable flips between
    // when a sentence was spoken and when stop() is called.
    browserTts.stop();
    neuralTts.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browserTts.stop, neuralTts.stop]);
  // Neither engine's "supported" reflects backend reachability (that's
  // ttsAvailable's job) — both are just "can this browser do TTS at all,"
  // which barely changes at runtime, so an OR here is stable enough to use
  // directly (no ref indirection needed).
  const ttsSupported = browserTts.supported || neuralTts.supported;
  // Selected fresh every render (not inside a WebSocket closure), so this
  // one DOES correctly re-point once ttsAvailable resolves — consumers
  // (ChatInput/VoiceMode) just read .current on whatever ref this is this render.
  const ttsBoundaryRef = ttsAvailable ? neuralTts.boundaryRef : browserTts.boundaryRef;

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

      intentionalCloseRef.current = false;

      socket.onopen = () => {
        setConnection("online");
        reconnectAttemptRef.current = 0;
      };

      socket.onclose = () => {
        // Two ways this close is stale, not a real disconnect to react to:
        // (1) intentionalCloseRef — set right before a deliberate close()
        // with no replacement socket coming (unmount). (2) socketRef.current
        // !== socket — a *replacement* socket (switchTo) already took over
        // socketRef synchronously, before this handshake-driven close event
        // even had a chance to fire; reacting to it would schedule a
        // redundant reconnect for a socket nothing is using anymore.
        if (intentionalCloseRef.current || socketRef.current !== socket) return;
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

          case "tool_result": {
            // Only ever sent for search_knowledge (see server.py's WS
            // handler) — attaches citation filenames to the pill pushed
            // just above, so a reply that used the knowledge base shows
            // which document(s) it actually came from.
            const sources = extractKnowledgeSources(msg.result);
            if (sources.length) setToolSources(msg.name, sources);
            break;
          }

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
      setToolSources,
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
      intentionalCloseRef.current = true;
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
      intentionalCloseRef.current = true;
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
