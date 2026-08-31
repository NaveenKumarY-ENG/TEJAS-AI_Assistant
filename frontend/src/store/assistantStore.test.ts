import { beforeEach, describe, expect, it } from "vitest";

import { useAssistantStore } from "./assistantStore";

// Zustand's create() returns one shared store instance — reset it to a
// known baseline before every test so tests can't leak state into each
// other via import-time module caching (the same store module is reused
// across the whole test file).
beforeEach(() => {
  useAssistantStore.setState({
    coreState: "idle",
    connection: "connecting",
    sessionId: null,
    timeline: [],
    streamingId: null,
    voiceOutputEnabled: true,
    pendingInput: null,
  });
});

describe("sending a message (critical path)", () => {
  it("adds a user message to the timeline and shows immediate activity", () => {
    useAssistantStore.getState().addUserMessage("Hello there");
    const { timeline, coreState } = useAssistantStore.getState();
    expect(timeline).toHaveLength(1);
    expect(timeline[0]).toMatchObject({ kind: "message", role: "user", content: "Hello there" });
    // Immediate feedback before any server response arrives — otherwise
    // nothing indicates activity until the first chunk/tool event, a
    // noticeable gap on a slow local model (see the store's own comment).
    expect(coreState).toBe("thinking");
  });

  it("streams an assistant reply incrementally via begin/append/end", () => {
    const store = useAssistantStore.getState();
    const id = store.beginAssistantStream();
    expect(useAssistantStore.getState().coreState).toBe("speaking");

    store.appendToStream(id, "Hello");
    store.appendToStream(id, ", world!");
    const entry = useAssistantStore.getState().timeline.find((e) => e.id === id);
    expect(entry).toMatchObject({ kind: "message", role: "assistant", content: "Hello, world!" });

    store.endStream();
    const state = useAssistantStore.getState();
    expect(state.streamingId).toBeNull();
    expect(state.coreState).toBe("idle");
  });

  it("keeps user and assistant messages in one ordered timeline", () => {
    const store = useAssistantStore.getState();
    store.addUserMessage("What's 2+2?");
    const id = store.beginAssistantStream();
    store.appendToStream(id, "4");
    store.endStream();

    const timeline = useAssistantStore.getState().timeline;
    expect(timeline.map((e) => (e.kind === "message" ? e.role : e.kind))).toEqual(["user", "assistant"]);
  });

  it("appendToStream only touches the matching streaming entry, not others", () => {
    const store = useAssistantStore.getState();
    const firstId = store.beginAssistantStream();
    store.appendToStream(firstId, "first reply");
    store.endStream();
    const secondId = store.beginAssistantStream();
    store.appendToStream(secondId, "second reply");

    const timeline = useAssistantStore.getState().timeline;
    const first = timeline.find((e) => e.id === firstId);
    const second = timeline.find((e) => e.id === secondId);
    expect(first).toMatchObject({ content: "first reply" });
    expect(second).toMatchObject({ content: "second reply" });
  });
});

describe("tool call UI state", () => {
  it("adds a pending tool entry via pushTool, then marks it done via resolveTools", () => {
    const store = useAssistantStore.getState();
    store.pushTool("get_weather");
    expect(useAssistantStore.getState().timeline[0]).toMatchObject({
      kind: "tool",
      name: "get_weather",
      done: false,
    });

    store.resolveTools();
    expect(useAssistantStore.getState().timeline[0]).toMatchObject({ kind: "tool", done: true });
  });

  it("resolveTools only marks NOT-yet-done tool entries, leaving already-resolved ones alone", () => {
    const store = useAssistantStore.getState();
    store.pushTool("get_weather");
    store.resolveTools();
    store.pushTool("search_knowledge");
    store.resolveTools();

    const tools = useAssistantStore.getState().timeline.filter((e) => e.kind === "tool");
    expect(tools).toHaveLength(2);
    expect(tools.every((t) => t.kind === "tool" && t.done)).toBe(true);
  });

  it("attaches sources to the most recent matching tool entry via setToolSources", () => {
    const store = useAssistantStore.getState();
    store.pushTool("search_knowledge");
    store.resolveTools();
    store.pushTool("search_knowledge"); // a second, later call to the same tool
    store.setToolSources("search_knowledge", ["report.pdf"]);

    const tools = useAssistantStore.getState().timeline.filter((e) => e.kind === "tool");
    expect(tools[0]).not.toHaveProperty("sources");
    expect(tools[1]).toMatchObject({ sources: ["report.pdf"] });
  });

  it("setToolSources is a no-op when no matching tool entry exists", () => {
    const before = useAssistantStore.getState().timeline;
    useAssistantStore.getState().setToolSources("nonexistent_tool", ["x"]);
    expect(useAssistantStore.getState().timeline).toBe(before); // unchanged reference — genuinely a no-op
  });
});

describe("session switch (critical path)", () => {
  it("hydrateHistory replaces the timeline with the loaded session's messages", () => {
    useAssistantStore.getState().addUserMessage("stale message from before switching");
    useAssistantStore.getState().hydrateHistory([
      { role: "user", content: "old question" },
      { role: "assistant", content: "old answer" },
    ]);

    const timeline = useAssistantStore.getState().timeline;
    expect(timeline).toHaveLength(2);
    expect(timeline.map((e) => (e.kind === "message" ? e.content : null))).toEqual(["old question", "old answer"]);
  });

  it("hydrateHistory normalizes any non-user role to assistant", () => {
    useAssistantStore.getState().hydrateHistory([{ role: "tool", content: "some tool output" }]);
    const entry = useAssistantStore.getState().timeline[0];
    expect(entry).toMatchObject({ role: "assistant" });
  });

  it("reset clears the timeline and streaming/session state for a fresh session", () => {
    const store = useAssistantStore.getState();
    store.setSession(42);
    store.addUserMessage("something");
    store.beginAssistantStream();

    store.reset();
    const state = useAssistantStore.getState();
    expect(state.timeline).toEqual([]);
    expect(state.streamingId).toBeNull();
    expect(state.sessionId).toBeNull();
    expect(state.coreState).toBe("idle");
  });

  it("setSession updates only the session id, leaving the timeline alone", () => {
    useAssistantStore.getState().addUserMessage("kept across the id change");
    useAssistantStore.getState().setSession(7);
    const state = useAssistantStore.getState();
    expect(state.sessionId).toBe(7);
    expect(state.timeline).toHaveLength(1);
  });
});

describe("voice output toggle", () => {
  it("toggleVoiceOutput flips the flag each call", () => {
    expect(useAssistantStore.getState().voiceOutputEnabled).toBe(true);
    useAssistantStore.getState().toggleVoiceOutput();
    expect(useAssistantStore.getState().voiceOutputEnabled).toBe(false);
    useAssistantStore.getState().toggleVoiceOutput();
    expect(useAssistantStore.getState().voiceOutputEnabled).toBe(true);
  });
});
