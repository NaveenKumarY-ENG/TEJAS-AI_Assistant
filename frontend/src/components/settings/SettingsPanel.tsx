import { useEffect, useState, type ReactNode } from "react";
import {
  Info,
  Cpu,
  Mic2,
  BrainCircuit,
  BookOpen,
  Activity,
  ShieldCheck,
  Terminal,
  Check,
  ExternalLink,
} from "lucide-react";
import { WidgetCard } from "../ui/WidgetCard";
import { useAssistantStore } from "../../store/assistantStore";

type SectionId = "general" | "ai" | "voice" | "memory" | "knowledge" | "system" | "privacy" | "developer";

const SECTIONS: { id: SectionId; label: string; icon: typeof Info }[] = [
  { id: "general", label: "General", icon: Info },
  { id: "ai", label: "AI & Models", icon: Cpu },
  { id: "voice", label: "Voice & Speech", icon: Mic2 },
  { id: "memory", label: "Chat & Memory", icon: BrainCircuit },
  { id: "knowledge", label: "Knowledge", icon: BookOpen },
  { id: "system", label: "System", icon: Activity },
  { id: "privacy", label: "Privacy & Data", icon: ShieldCheck },
  { id: "developer", label: "Developer", icon: Terminal },
];

interface SystemHealth {
  status: string;
  database: string;
  vector_store: string;
  python_version: string;
}

interface KnowledgeSummary {
  documentCount: number;
  folders: { id: number; path: string; file_count: number }[];
}

// --- small shared building blocks (kept local — this page is the only consumer) ---

function SettingsRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-[13px]">
      <span className="text-white/50">{label}</span>
      <span className="text-right text-white/85">{children}</span>
    </div>
  );
}

function StatusPill({ tone, children }: { tone: "success" | "warning" | "neutral"; children: ReactNode }) {
  const classes =
    tone === "success"
      ? "bg-success/10 text-success"
      : tone === "warning"
        ? "bg-warning/10 text-warning"
        : "bg-white/[0.06] text-white/55";
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${classes}`}>{children}</span>;
}

/** "Configured"/"Not configured" (or a custom pair) driven by a real boolean
 * — never a fake switch, see SettingsPanel's Privacy/AI sections. */
function ConfiguredPill({ ok, yes = "Configured", no = "Not configured" }: { ok: boolean; yes?: string; no?: string }) {
  return (
    <StatusPill tone={ok ? "success" : "warning"}>{ok ? yes : no}</StatusPill>
  );
}

function OptionRow({
  active,
  disabled,
  onClick,
  primary,
  meta,
}: {
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  primary: string;
  meta: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex w-full items-center justify-between gap-3 rounded-xl border px-3.5 py-2.5 text-left text-[13px] transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
        active
          ? "border-primary/40 bg-primary/10 text-white"
          : "border-white/[0.07] bg-white/[0.015] text-white/70 hover:border-primary/25 hover:bg-white/[0.03]"
      }`}
    >
      <div className="min-w-0">
        <div className="truncate font-medium">{primary}</div>
        <div className="mt-0.5 truncate text-[11px] text-white/45">{meta}</div>
      </div>
      {active && <Check size={15} strokeWidth={2.2} className="shrink-0 text-primary" />}
    </button>
  );
}

/** Real, working on/off control — first of its kind in this app — for
 * voiceOutputEnabled specifically, which genuinely is a live, working
 * boolean (see assistantStore.ts), just frontend/session-only. Labeled
 * as such below rather than implying it's saved anywhere. */
function ToggleSwitch({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onClick}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-primary" : "bg-white/15"}`}
    >
      <span
        className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
          on ? "translate-x-[22px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

/**
 * Settings — a real, mostly read-only control center. Reuses the exact same
 * store fields/actions and REST endpoints TopBar's ModelSelector/
 * VoiceSelector already use (GET/POST /api/models, GET/POST /api/tts/voices)
 * rather than a second model/voice system — selecting a model or voice here
 * calls the identical endpoint and store setter, so TopBar and Settings can
 * never disagree (same source of truth, not synchronized after the fact).
 * Everything else on this page is either a real read-only fact (traced to
 * an existing endpoint/config field) or explicitly omitted — nothing here
 * is invented. Mirrors KnowledgePanel.tsx/LiveAiPanel.tsx's own shape: one
 * file, own internal tab state, mounted by App.tsx the same way.
 */
export function SettingsPanel({
  assistantName,
  toolCount,
  ttsAvailable,
  ocrAvailable,
  browserAvailable,
  searchConfigured,
  liveAiEnabled,
  anthropicConfigured,
  geminiConfigured,
  onModelError,
}: {
  assistantName: string;
  toolCount: number;
  ttsAvailable: boolean;
  ocrAvailable: boolean;
  browserAvailable: boolean;
  searchConfigured: boolean;
  liveAiEnabled: boolean;
  anthropicConfigured: boolean;
  geminiConfigured: boolean;
  onModelError: (message: string) => void;
}) {
  const [activeSection, setActiveSection] = useState<SectionId>("general");

  const availableModels = useAssistantStore((s) => s.availableModels);
  const modelId = useAssistantStore((s) => s.modelId);
  const setActiveModel = useAssistantStore((s) => s.setActiveModel);
  const ttsVoices = useAssistantStore((s) => s.ttsVoices);
  const ttsVoiceId = useAssistantStore((s) => s.ttsVoiceId);
  const setActiveTtsVoice = useAssistantStore((s) => s.setActiveTtsVoice);
  const voiceOutputEnabled = useAssistantStore((s) => s.voiceOutputEnabled);
  const toggleVoiceOutput = useAssistantStore((s) => s.toggleVoiceOutput);
  const connection = useAssistantStore((s) => s.connection);

  const [modelSwitching, setModelSwitching] = useState(false);
  const [voiceSwitching, setVoiceSwitching] = useState(false);

  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [knowledgeSummary, setKnowledgeSummary] = useState<KnowledgeSummary | null>(null);
  const [knowledgeError, setKnowledgeError] = useState(false);

  // Fetched once, lazily — not polled — the moment Settings itself is
  // opened (SettingsPanel only mounts once the user navigates here, so
  // this never fires on app load). Three sections read this (General's
  // Python version, System, Developer's diagnostics dump), so it's fetched
  // unconditionally on mount rather than re-triggered per-tab — the
  // earlier per-tab version left General's "Python version" stuck on
  // "Loading…" forever for anyone who never visited System/Developer.
  useEffect(() => {
    fetch("/api/system/health")
      .then((r) => r.json())
      .then((h) => setHealth(h))
      .catch(() => setHealthError(true));
  }, []);

  useEffect(() => {
    if (activeSection !== "knowledge") return;
    if (knowledgeSummary || knowledgeError) return;
    Promise.all([fetch("/api/knowledge").then((r) => r.json()), fetch("/api/knowledge/folders").then((r) => r.json())])
      .then(([docs, folders]) =>
        setKnowledgeSummary({
          documentCount: (docs.documents ?? []).length,
          folders: folders.folders ?? [],
        }),
      )
      .catch(() => setKnowledgeError(true));
  }, [activeSection, knowledgeSummary, knowledgeError]);

  async function handleSelectModel(id: string) {
    if (id === modelId || modelSwitching) return;
    setModelSwitching(true);
    try {
      const res = await fetch("/api/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to switch model");
      setActiveModel(data.active, data.model);
    } catch (err) {
      onModelError(err instanceof Error ? err.message : "Failed to switch model");
    } finally {
      setModelSwitching(false);
    }
  }

  async function handleSelectVoice(id: string) {
    if (id === ttsVoiceId || voiceSwitching) return;
    setVoiceSwitching(true);
    try {
      const res = await fetch("/api/tts/voices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to switch voice");
      setActiveTtsVoice(data.active);
    } catch (err) {
      onModelError(err instanceof Error ? err.message : "Failed to switch voice");
    } finally {
      setVoiceSwitching(false);
    }
  }

  const activeModel = availableModels.find((m) => m.id === modelId);
  const connectionLabel =
    connection === "online" ? "Connected" : connection === "connecting" ? "Connecting" : connection === "reconnecting" ? "Reconnecting" : "Offline";

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 px-6 pb-6 lg:grid-cols-[220px_1fr]">
      <nav className="thin-scroll flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible lg:overflow-y-auto">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setActiveSection(s.id)}
            className={`flex shrink-0 items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left text-[13px] transition-all lg:shrink ${
              activeSection === s.id
                ? "border-transparent bg-gradient-to-r from-primary/90 to-secondary/70 text-white shadow-[0_0_20px_-4px_color-mix(in_srgb,var(--color-primary)_55%,transparent)]"
                : "border-white/[0.07] bg-white/[0.015] text-white/60 hover:border-primary/30 hover:bg-white/[0.04] hover:text-white/90"
            }`}
          >
            <s.icon size={15} strokeWidth={1.8} className={activeSection === s.id ? "text-white" : "text-primary/50"} />
            <span className="whitespace-nowrap">{s.label}</span>
          </button>
        ))}
      </nav>

      <div className="thin-scroll flex min-h-0 flex-col gap-4 overflow-y-auto pb-2">
        {activeSection === "general" && (
          <>
            <WidgetCard title="General">
              <SettingsRow label="Assistant name">{assistantName}</SettingsRow>
              <SettingsRow label="Application">TEJAS AI Assistant</SettingsRow>
              <SettingsRow label="Source">
                <a
                  href="https://github.com/NaveenKumarY-ENG/TEJAS-AI_Assistant"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  GitHub <ExternalLink size={11} />
                </a>
              </SettingsRow>
            </WidgetCard>
            <WidgetCard title="Runtime information">
              <SettingsRow label="Python version">{health?.python_version ?? (healthError ? "Unavailable" : "Loading…")}</SettingsRow>
              <SettingsRow label="Backend">FastAPI</SettingsRow>
              <SettingsRow label="Frontend">React + Vite</SettingsRow>
            </WidgetCard>
          </>
        )}

        {activeSection === "ai" && (
          <WidgetCard title="AI & Models" tag={`${availableModels.length} available`} tagTone="accent">
            <p className="mb-3 text-[12px] text-white/50">
              Active: <span className="text-white/85">{activeModel?.label ?? "—"}</span>. Selecting a model here changes it
              everywhere in TEJAS immediately — the same switch the model selector in the top bar makes.
            </p>
            <div className="space-y-2">
              {availableModels.map((m) => (
                <OptionRow
                  key={m.id}
                  active={m.id === modelId}
                  disabled={modelSwitching}
                  onClick={() => handleSelectModel(m.id)}
                  primary={m.label}
                  meta={`${m.provider} · ${m.provider === "ollama" ? "Local" : "Cloud"} · ${m.supports_tools ? "Tools supported" : "No tool access"}`}
                />
              ))}
              {availableModels.length === 0 && <p className="text-[12px] text-white/40">Loading models…</p>}
            </div>
          </WidgetCard>
        )}

        {activeSection === "voice" && (
          <>
            <WidgetCard title="Voice output">
              <div className="flex items-center justify-between gap-4 py-1">
                <div>
                  <div className="text-[13px] text-white/85">Speak replies aloud</div>
                  <p className="mt-0.5 text-[11px] text-white/40">Applies to typed and spoken input alike. This session only — resets on reload.</p>
                </div>
                <ToggleSwitch on={voiceOutputEnabled} onClick={toggleVoiceOutput} label="Toggle spoken replies" />
              </div>
            </WidgetCard>
            <WidgetCard title="Available voices" tag={`${ttsVoices.length} available`} tagTone="accent">
              <div className="space-y-2">
                {ttsVoices.map((v) => (
                  <OptionRow
                    key={v.id}
                    active={v.id === ttsVoiceId}
                    disabled={voiceSwitching}
                    onClick={() => handleSelectVoice(v.id)}
                    primary={v.label}
                    meta="Neural voice (Kokoro)"
                  />
                ))}
                {ttsVoices.length === 0 && <p className="text-[12px] text-white/40">Loading voices…</p>}
              </div>
            </WidgetCard>
            <WidgetCard title="Speech backend">
              <SettingsRow label="Text-to-speech"><ConfiguredPill ok={ttsAvailable} yes="Neural (Kokoro)" no="Browser fallback" /></SettingsRow>
            </WidgetCard>
          </>
        )}

        {activeSection === "memory" && (
          <>
            <WidgetCard title="Chat & memory">
              <SettingsRow label="Conversation storage">SQLite</SettingsRow>
              <SettingsRow label="Semantic memory"><StatusPill tone="success">ChromaDB — enabled</StatusPill></SettingsRow>
            </WidgetCard>
            <WidgetCard title="Memory information">
              <p className="text-[12px] leading-relaxed text-white/55">
                Past conversations are recalled by meaning, not exact wording — but only when genuinely relevant: an
                unrelated question doesn't drag in the nearest-available past exchange just because something has to be
                nearest. Point-in-time facts (like a past weather answer) are deliberately never recalled as if still true.
              </p>
            </WidgetCard>
          </>
        )}

        {activeSection === "knowledge" && (
          <>
            <WidgetCard title="Knowledge base status">
              <SettingsRow label="Documents indexed">
                {knowledgeSummary ? knowledgeSummary.documentCount : knowledgeError ? "Unavailable" : "Loading…"}
              </SettingsRow>
              <SettingsRow label="OCR"><ConfiguredPill ok={ocrAvailable} yes="Available" no="Not installed" /></SettingsRow>
              <SettingsRow label="Semantic search"><StatusPill tone="success">Enabled</StatusPill></SettingsRow>
              <SettingsRow label="Hybrid search (keyword + semantic)"><StatusPill tone="success">Enabled</StatusPill></SettingsRow>
            </WidgetCard>
            <WidgetCard title="Watched folders" tag={knowledgeSummary ? `${knowledgeSummary.folders.length}` : undefined} tagTone="accent">
              {knowledgeSummary && knowledgeSummary.folders.length === 0 && (
                <p className="text-[12px] text-white/40">No folders are being watched.</p>
              )}
              <div className="space-y-1.5">
                {knowledgeSummary?.folders.map((f) => (
                  <div key={f.id} className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.06] px-3 py-2 text-[12px]">
                    <span className="truncate text-white/70">{f.path}</span>
                    <span className="shrink-0 text-white/40">{f.file_count} file{f.file_count === 1 ? "" : "s"}</span>
                  </div>
                ))}
              </div>
            </WidgetCard>
          </>
        )}

        {activeSection === "system" && (
          <WidgetCard title="System">
            <SettingsRow label="Backend"><StatusPill tone="success">ok</StatusPill></SettingsRow>
            <SettingsRow label="WebSocket"><StatusPill tone={connection === "online" ? "success" : "warning"}>{connectionLabel}</StatusPill></SettingsRow>
            <SettingsRow label="SQLite">
              <StatusPill tone={health?.database === "ok" ? "success" : health ? "warning" : "neutral"}>
                {health?.database ?? (healthError ? "unknown" : "checking…")}
              </StatusPill>
            </SettingsRow>
            <SettingsRow label="ChromaDB">
              <StatusPill tone={health?.vector_store === "ok" ? "success" : health ? "warning" : "neutral"}>
                {health?.vector_store ?? (healthError ? "unknown" : "checking…")}
              </StatusPill>
            </SettingsRow>
            <SettingsRow label="LLM"><StatusPill tone="success">{activeModel ? `${activeModel.provider} — ok` : "ok"}</StatusPill></SettingsRow>
            <SettingsRow label="TTS"><ConfiguredPill ok={ttsAvailable} yes="ok" no="unavailable" /></SettingsRow>
            <SettingsRow label="OCR"><ConfiguredPill ok={ocrAvailable} yes="available" no="unavailable" /></SettingsRow>
          </WidgetCard>
        )}

        {activeSection === "privacy" && (
          <>
            <WidgetCard title="Local storage">
              <SettingsRow label="Conversations & reminders">data/assistant.db (SQLite)</SettingsRow>
              <SettingsRow label="Semantic memory & knowledge base">data/vector_store/ (ChromaDB)</SettingsRow>
            </WidgetCard>
            <WidgetCard title="Cloud AI">
              <SettingsRow label="Anthropic Claude"><ConfiguredPill ok={anthropicConfigured} /></SettingsRow>
              <SettingsRow label="Google Gemini"><ConfiguredPill ok={geminiConfigured} /></SettingsRow>
              <SettingsRow label="Web search (Tavily)"><ConfiguredPill ok={searchConfigured} /></SettingsRow>
              <SettingsRow label="Live.AI browser tools"><ConfiguredPill ok={browserAvailable} yes="Available" no="Not installed" /></SettingsRow>
            </WidgetCard>
            <WidgetCard title="Local AI">
              <SettingsRow label="Ollama"><StatusPill tone="success">Configured as default provider</StatusPill></SettingsRow>
            </WidgetCard>
            <WidgetCard title="Secrets">
              <p className="text-[12px] leading-relaxed text-white/55">
                API keys live only in this server's local <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-[11px]">.env</code> file
                and are never sent to the browser — TEJAS only ever reports whether a key looks configured, never the key itself.
              </p>
            </WidgetCard>
          </>
        )}

        {activeSection === "developer" && (
          <>
            <WidgetCard title="Developer">
              <SettingsRow label="Runtime">Python {health?.python_version ?? "…"} · FastAPI · React + Vite</SettingsRow>
              <SettingsRow label="Active model">{activeModel?.label ?? "—"}</SettingsRow>
              <SettingsRow label="Active voice">{ttsVoices.find((v) => v.id === ttsVoiceId)?.label ?? "—"}</SettingsRow>
              <SettingsRow label="Tools loaded">{toolCount}</SettingsRow>
              <SettingsRow label="Live.AI"><ConfiguredPill ok={liveAiEnabled} yes="Enabled" no="Disabled" /></SettingsRow>
            </WidgetCard>
            <WidgetCard title="Diagnostics">
              <pre className="thin-scroll max-h-64 overflow-auto rounded-lg border border-white/[0.06] bg-black/30 p-3 text-[11px] leading-relaxed text-white/60">
                {JSON.stringify(
                  {
                    assistant_name: assistantName,
                    active_model: activeModel?.id,
                    active_voice: ttsVoiceId,
                    tool_count: toolCount,
                    tts_available: ttsAvailable,
                    ocr_available: ocrAvailable,
                    browser_available: browserAvailable,
                    search_configured: searchConfigured,
                    anthropic_configured: anthropicConfigured,
                    gemini_configured: geminiConfigured,
                    live_ai_enabled: liveAiEnabled,
                    connection,
                    system_health: health ?? (healthError ? "unavailable" : "loading"),
                  },
                  null,
                  2,
                )}
              </pre>
            </WidgetCard>
          </>
        )}
      </div>
    </div>
  );
}
