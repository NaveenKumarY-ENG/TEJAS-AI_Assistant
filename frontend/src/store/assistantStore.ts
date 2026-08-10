import { create } from "zustand";

export type CoreState = "idle" | "listening" | "thinking" | "speaking";
export type ConnectionState = "connecting" | "online" | "reconnecting" | "offline";

// A single ordered timeline holds both chat messages and tool-call events,
// interleaved in the order they actually happened — a tool call from turn 1
// stays visible in history rather than being wiped once that turn's reply
// finishes streaming.
export type TimelineEntry =
  | { id: string; kind: "message"; role: "user" | "assistant"; content: string }
  | { id: string; kind: "tool"; name: string; done: boolean };

interface AssistantStore {
  coreState: CoreState;
  connection: ConnectionState;
  sessionId: number | null;
  assistantName: string;
  model: string;
  toolCount: number;
  timeline: TimelineEntry[];
  streamingId: string | null;
  // Whether TEJAS speaks replies aloud — applies to both typed and voice
  // input, defaults on so voice interactions feel like talking to an
  // assistant rather than a chat box. Persisted across the tab session only.
  voiceOutputEnabled: boolean;

  setCoreState: (s: CoreState) => void;
  setConnection: (c: ConnectionState) => void;
  setSession: (id: number) => void;
  setMeta: (meta: { assistantName?: string; model?: string; toolCount?: number }) => void;
  hydrateHistory: (msgs: Array<{ role: string; content: string }>) => void;
  addUserMessage: (text: string) => void;
  beginAssistantStream: () => string;
  appendToStream: (id: string, piece: string) => void;
  endStream: () => void;
  pushTool: (name: string) => void;
  resolveTools: () => void;
  toggleVoiceOutput: () => void;
  reset: () => void;
}

let uid = 0;
const nextId = () => `m${Date.now()}_${uid++}`;

export const useAssistantStore = create<AssistantStore>((set) => ({
  coreState: "idle",
  connection: "connecting",
  sessionId: null,
  assistantName: "Tejas",
  model: "",
  toolCount: 0,
  timeline: [],
  streamingId: null,
  voiceOutputEnabled: true,

  setCoreState: (s) => set({ coreState: s }),
  setConnection: (c) => set({ connection: c }),
  setSession: (id) => set({ sessionId: id }),
  setMeta: (meta) =>
    set((state) => ({
      assistantName: meta.assistantName ?? state.assistantName,
      model: meta.model ?? state.model,
      toolCount: meta.toolCount ?? state.toolCount,
    })),

  hydrateHistory: (msgs) =>
    set({
      timeline: msgs.map((m) => ({
        id: nextId(),
        kind: "message",
        role: m.role === "user" ? "user" : "assistant",
        content: m.content,
      })),
    }),

  addUserMessage: (text) =>
    set((state) => ({
      timeline: [...state.timeline, { id: nextId(), kind: "message", role: "user", content: text }],
      // Immediate feedback — otherwise nothing indicates activity until the
      // first tool/chunk event arrives from the server, which can be a
      // noticeable gap on a slow local model.
      coreState: "thinking",
    })),

  beginAssistantStream: () => {
    const id = nextId();
    set((state) => ({
      timeline: [...state.timeline, { id, kind: "message", role: "assistant", content: "" }],
      streamingId: id,
      coreState: "speaking",
    }));
    return id;
  },

  appendToStream: (id, piece) =>
    set((state) => ({
      timeline: state.timeline.map((e) => (e.id === id && e.kind === "message" ? { ...e, content: e.content + piece } : e)),
    })),

  endStream: () => set({ streamingId: null, coreState: "idle" }),

  pushTool: (name) =>
    set((state) => ({
      timeline: [...state.timeline, { id: nextId(), kind: "tool", name, done: false }],
      coreState: "thinking",
    })),

  resolveTools: () =>
    set((state) => ({
      timeline: state.timeline.map((e) => (e.kind === "tool" && !e.done ? { ...e, done: true } : e)),
    })),

  toggleVoiceOutput: () => set((state) => ({ voiceOutputEnabled: !state.voiceOutputEnabled })),

  reset: () => set({ timeline: [], streamingId: null, coreState: "idle", sessionId: null }),
}));
