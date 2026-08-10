import { useAssistantStore } from "../../store/assistantStore";
import { WidgetCard } from "./WidgetCard";

export function StatusPanel() {
  const model = useAssistantStore((s) => s.model);
  const toolCount = useAssistantStore((s) => s.toolCount);
  const sessionId = useAssistantStore((s) => s.sessionId);
  const connection = useAssistantStore((s) => s.connection);

  const tag = connection === "online" ? "Online" : connection === "connecting" ? "Connecting" : connection === "reconnecting" ? "Reconnecting" : "Offline";
  const tone = connection === "online" ? "success" : connection === "offline" ? "warning" : "accent";

  return (
    <WidgetCard title="System Status" tag={tag} tagTone={tone}>
      <dl className="space-y-2 text-[12.5px]">
        <Row label="Model" value={model || "—"} />
        <Row label="Tools loaded" value={toolCount || "—"} />
        <Row label="Session" value={sessionId ? `#${sessionId}` : "—"} />
      </dl>
    </WidgetCard>
  );
}

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-white/50">{label}</dt>
      <dd className="mono text-white/85">{value}</dd>
    </div>
  );
}
