import { create } from "zustand";

export type CoreState = "idle" | "listening" | "processing" | "thinking" | "searching" | "speaking" | "error";
export type ConnectionState = "connecting" | "online" | "reconnecting" | "offline";

export interface ModelOption {
  id: string;
  provider: string;
  model: string;
  label: string;
}

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
  modelId: string;
  availableModels: ModelOption[];
  toolCount: number;
  timeline: TimelineEntry[];
  streamingId: string | null;
  // Whether TEJAS speaks replies aloud — applies to both typed and voice
  // input, defaults on so voice interactions feel like talking to an
  // assistant rather than a chat box. Persisted across the tab session only.
  voiceOutputEnabled: boolean;
  // Text a quick action wants placed in the chat input for the user to
  // finish and send themselves, rather than sent immediately as-is — e.g.
  // "Quick calculation" prefills "Calculate " instead of always asking the
  // same canned example. ChatInput consumes and clears this via an effect.
  pendingInput: string | null;

  setCoreState: (s: CoreState) => void;
  setConnection: (c: ConnectionState) => void;
  setSession: (id: number) => void;
  setMeta: (meta: { assistantName?: string; model?: string; toolCount?: number }) => void;
  setModels: (models: ModelOption[], activeId: string) => void;
  setActiveModel: (id: string, model: string) => void;
  hydrateHistory: (msgs: Array<{ role: string; content: string }>) => void;
  addUserMessage: (text: string) => void;
  beginAssistantStream: () => string;
  appendToStream: (id: string, piece: string) => void;
  endStream: () => void;
  pushTool: (name: string) => void;
  resolveTools: () => void;
  toggleVoiceOutput: () => void;
  setPendingInput: (text: string) => void;
  clearPendingInput: () => void;
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
  modelId: "",
  availableModels: [],
  toolCount: 0,
  timeline: [],
  streamingId: null,
  voiceOutputEnabled: true,
  pendingInput: null,

  setCoreState: (s) => set({ coreState: s }),
  setConnection: (c) => set({ connection: c }),
  setSession: (id) => set({ sessionId: id }),
  setMeta: (meta) =>
    set((state) => ({
      assistantName: meta.assistantName ?? state.assistantName,
      model: meta.model ?? state.model,
      toolCount: meta.toolCount ?? state.toolCount,
    })),
  setModels: (models, activeId) => set({ availableModels: models, modelId: activeId }),
  setActiveModel: (id, model) => set({ modelId: id, model }),

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

  // Deliberately does NOT set coreState — the caller (useAssistantSocket's
  // "tool" WS event handler) decides "thinking" vs "searching" based on
  // which tool ran, since a hardcoded value here would defeat that.
  pushTool: (name) =>
    set((state) => ({
      timeline: [...state.timeline, { id: nextId(), kind: "tool", name, done: false }],
    })),

  resolveTools: () =>
    set((state) => ({
      timeline: state.timeline.map((e) => (e.kind === "tool" && !e.done ? { ...e, done: true } : e)),
    })),

  toggleVoiceOutput: () => set((state) => ({ voiceOutputEnabled: !state.voiceOutputEnabled })),
  setPendingInput: (text) => set({ pendingInput: text }),
  clearPendingInput: () => set({ pendingInput: null }),

  reset: () => set({ timeline: [], streamingId: null, coreState: "idle", sessionId: null }),
}));
