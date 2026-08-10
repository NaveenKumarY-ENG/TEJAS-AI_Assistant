import { useEffect, useState } from "react";
import { useAssistantStore } from "../../store/assistantStore";
import { WidgetCard } from "./WidgetCard";

interface SessionRow {
  id: number;
  message_count: number;
}

export function RecentSessions({ onOpenSession }: { onOpenSession: (id: number) => void }) {
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [error, setError] = useState(false);
  const activeSessionId = useAssistantStore((s) => s.sessionId);
  const isIdle = useAssistantStore((s) => s.coreState === "idle");

  useEffect(() => {
    fetch("/api/sessions")
      .then((r) => r.json())
      .then((data) => setSessions(data.sessions ?? []))
      .catch(() => setError(true));
    // Refetch when a (new or different) session becomes active, and after
    // each turn finishes so message counts stay current.
  }, [activeSessionId, isIdle]);

  return (
    <WidgetCard title="Recent Sessions">
      {error && <p className="text-[12.5px] text-white/40">Unavailable</p>}
      {!error && !sessions && <p className="text-[12.5px] text-white/40">Loading…</p>}
      {sessions && sessions.length === 0 && <p className="text-[12.5px] text-white/40">No sessions yet.</p>}
      {sessions && sessions.length > 0 && (
        <ul className="space-y-1">
          {sessions.slice(0, 6).map((s) => (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onOpenSession(s.id)}
                disabled={s.id === activeSessionId}
                className={`flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-[12.3px] transition-colors ${
                  s.id === activeSessionId
                    ? "bg-primary/10 text-primary"
                    : "text-white/55 hover:bg-white/[0.05] hover:text-white/85"
                }`}
              >
                <span>Session #{s.id}</span>
                <span className="mono text-white/35">{s.message_count} msgs</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </WidgetCard>
  );
}
