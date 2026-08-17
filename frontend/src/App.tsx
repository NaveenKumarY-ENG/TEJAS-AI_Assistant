import { useEffect } from "react";
import { Sidebar } from "./components/layout/Sidebar";
import { TopBar } from "./components/layout/TopBar";
import { AssistantCore } from "./components/core/AssistantCore";
import { QuickActions } from "./components/ui/QuickActions";
import { ConversationPanel } from "./components/ui/ConversationPanel";
import { ChatInput } from "./components/ui/ChatInput";
import { StatusPanel } from "./components/ui/StatusPanel";
import { WeatherWidget } from "./components/ui/WeatherWidget";
import { RecentSessions } from "./components/ui/RecentSessions";
import { RemindersWidget } from "./components/ui/RemindersWidget";
import { Toast } from "./components/ui/Toast";
import { IntroSequence } from "./components/IntroSequence";
import { useAssistantStore } from "./store/assistantStore";
import { useAssistantSocket } from "./hooks/useAssistantSocket";
import { useToast } from "./hooks/useToast";

function AmbientBackdrop() {
  return (
    <div className="pointer-events-none fixed inset-0 z-0" aria-hidden="true">
      <div
        className="absolute inset-0 opacity-[0.05]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(0,229,255,0.6) 1px, transparent 1px), linear-gradient(90deg, rgba(0,229,255,0.6) 1px, transparent 1px)",
          backgroundSize: "56px 56px",
        }}
      />
      <div className="absolute -left-40 -top-40 h-[520px] w-[520px] rounded-full bg-primary/10 blur-[120px]" />
      <div className="absolute -right-40 bottom-0 h-[480px] w-[480px] rounded-full bg-accent/10 blur-[130px]" />
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{ backgroundImage: "repeating-linear-gradient(0deg, #fff 0px, transparent 1px, transparent 3px)" }}
      />
    </div>
  );
}

function Dashboard() {
  const coreState = useAssistantStore((s) => s.coreState);
  const assistantName = useAssistantStore((s) => s.assistantName);
  const setMeta = useAssistantStore((s) => s.setMeta);
  const voiceOutputEnabled = useAssistantStore((s) => s.voiceOutputEnabled);
  const toggleVoiceOutput = useAssistantStore((s) => s.toggleVoiceOutput);
  const { message, show } = useToast();
  const { sendMessage, startNewChat, openSession, stopSpeaking, ttsBoundaryRef } = useAssistantSocket();

  const handleToggleVoiceOutput = () => {
    if (voiceOutputEnabled) stopSpeaking(); // muting mid-reply should cut audio immediately
    toggleVoiceOutput();
  };

  useEffect(() => {
    fetch("/api/meta")
      .then((r) => r.json())
      .then((m) =>
        setMeta({ assistantName: m.assistant_name, model: m.model, toolCount: m.tools?.length ?? 0 })
      )
      .catch(() => {});
  }, [setMeta]);

  const busy =
    coreState === "thinking" || coreState === "speaking" || coreState === "processing" || coreState === "searching";
  const onSoon = (label: string) => show(`${label} is coming soon`);
  // Voice errors are diagnostic (mic permission, backend unreachable, etc.)
  // and worth actually reading, so they stay up longer than a quick toast.
  const onVoiceError = (message: string) => show(message, 5000);

  return (
    <div className="relative flex h-full">
      <AmbientBackdrop />
      <div className="relative z-10 flex h-full w-full">
        <Sidebar onSoonClick={onSoon} onNewChat={startNewChat} />

        <main className="flex min-w-0 flex-1 flex-col">
          <TopBar onSoonClick={onSoon} voiceOutputEnabled={voiceOutputEnabled} onToggleVoiceOutput={handleToggleVoiceOutput} />

          <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)] gap-5 px-6 pb-6 lg:grid-cols-[1fr_300px]">
            <section className="relative min-h-0 overflow-hidden rounded-2xl border border-white/[0.06]">
              {/* Full-bleed hologram backdrop — the chat UI below floats on top of it. */}
              <AssistantCore coreState={coreState} />

              <div className="thin-scroll relative z-10 flex h-full min-h-0 flex-col overflow-y-auto">
                <div className="pt-5">
                  <QuickActions onPick={sendMessage} disabled={busy} />
                </div>
                <ConversationPanel />
                <div className="px-5 pb-1.5">
                  <ChatInput
                    disabled={busy}
                    coreState={coreState}
                    ttsBoundaryRef={ttsBoundaryRef}
                    stopSpeaking={stopSpeaking}
                    onSend={sendMessage}
                    onSoonClick={onSoon}
                    onVoiceError={onVoiceError}
                  />
                </div>
                <p className="pb-3 text-center text-[11px] text-white/30">
                  {assistantName} can make mistakes. Verify important information.
                </p>
              </div>
            </section>

            <aside className="thin-scroll hidden min-h-0 flex-col gap-3.5 overflow-y-auto pt-1 lg:flex">
              <StatusPanel />
              <WeatherWidget />
              <RecentSessions onOpenSession={openSession} onActiveSessionDeleted={startNewChat} />
              <RemindersWidget />
            </aside>
          </div>
        </main>
      </div>

      <Toast message={message} />
    </div>
  );
}

export default function App() {
  return (
    <IntroSequence>
      <Dashboard />
    </IntroSequence>
  );
}
