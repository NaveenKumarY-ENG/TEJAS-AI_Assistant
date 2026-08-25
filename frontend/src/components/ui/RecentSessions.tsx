import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { useAssistantStore } from "../../store/assistantStore";
import { WidgetCard } from "./WidgetCard";

interface SessionRow {
  id: number;
  message_count: number;
  title: string | null;
}

const MAX_TITLE_LENGTH = 34;

function sessionLabel(session: SessionRow): string {
  if (!session.title) return "New session";
  const trimmed = session.title.trim().replace(/\s+/g, " ");
  return trimmed.length > MAX_TITLE_LENGTH ? `${trimmed.slice(0, MAX_TITLE_LENGTH)}…` : trimmed;
}

interface RecentSessionsProps {
  onOpenSession: (id: number) => void;
  // Called when the session the user just deleted was the one currently
  // open — the WebSocket is still connected to a session_id that no longer
  // exists in the DB, so the caller needs to force a fresh one.
  onActiveSessionDeleted: () => void;
}

export function RecentSessions({ onOpenSession, onActiveSessionDeleted }: RecentSessionsProps) {
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

  const handleDelete = async (session: SessionRow, e: React.MouseEvent) => {
    e.stopPropagation();
    const id = session.id;
    if (!window.confirm(`Delete "${sessionLabel(session)}"? This can't be undone.`)) return;

    try {
      const res = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(`Delete failed (${res.status})`);
      setSessions((prev) => prev?.filter((s) => s.id !== id) ?? prev);
      if (id === activeSessionId) onActiveSessionDeleted();
    } catch (err) {
      console.error("Failed to delete session:", err);
    }
  };

  return (
    <WidgetCard title="Recent Sessions">
      {error && <p className="text-[12.5px] text-white/40">Unavailable</p>}
      {!error && !sessions && <p className="text-[12.5px] text-white/40">Loading…</p>}
      {sessions && sessions.length === 0 && <p className="text-[12.5px] text-white/40">No sessions yet.</p>}
      {sessions && sessions.length > 0 && (
        <ul className="space-y-1.5">
          {sessions.slice(0, 6).map((s) => (
            <li key={s.id} className="group flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => onOpenSession(s.id)}
                disabled={s.id === activeSessionId}
                title={s.title ?? undefined}
                className={`flex min-w-0 flex-1 items-center justify-between rounded-xl border px-2.5 py-1.5 text-[12.3px] transition-all ${
                  s.id === activeSessionId
                    ? "border-primary/50 bg-primary/10 text-primary shadow-[0_0_14px_-6px_color-mix(in_srgb,var(--color-primary)_60%,transparent)]"
                    : "border-white/[0.07] bg-white/[0.015] text-white/55 hover:border-primary/25 hover:bg-white/[0.04] hover:text-white/85"
                }`}
              >
                <span className={`truncate ${s.title ? "" : "italic text-white/35"}`}>{sessionLabel(s)}</span>
                <span className="mono ml-2 shrink-0 text-white/35">{s.message_count} msgs</span>
              </button>
              <button
                type="button"
                onClick={(e) => handleDelete(s, e)}
                aria-label={`Delete "${sessionLabel(s)}"`}
                className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-transparent text-white/25 opacity-0 transition-all hover:border-red-500/30 hover:bg-red-500/10 hover:text-red-400 focus-visible:opacity-100 group-hover:opacity-100"
              >
                <Trash2 size={13} strokeWidth={1.8} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </WidgetCard>
  );
}
