import { useEffect, useState } from "react";
import { useAssistantStore } from "../../store/assistantStore";
import { WidgetCard } from "./WidgetCard";

interface Reminder {
  id: number;
  text: string;
  due_at: string | null;
  done: number;
}

/** Real reminders — the same add_reminder/list_reminders tools the agent
 *  itself uses when you say "remind me to X". Refetches whenever a turn
 *  finishes, since that's the only point a reminder could have just been
 *  added or completed via chat. */
export function RemindersWidget() {
  const [reminders, setReminders] = useState<Reminder[] | null>(null);
  const [error, setError] = useState(false);
  const coreState = useAssistantStore((s) => s.coreState);

  useEffect(() => {
    if (coreState !== "idle") return;
    fetch("/api/reminders")
      .then((r) => r.json())
      .then((data) => setReminders(data.reminders ?? []))
      .catch(() => setError(true));
  }, [coreState]);

  return (
    <WidgetCard title="Reminders">
      {error && <p className="text-[12.5px] text-white/40">Unavailable</p>}
      {!error && !reminders && <p className="text-[12.5px] text-white/40">Loading…</p>}
      {reminders && reminders.length === 0 && <p className="text-[12.5px] text-white/40">No reminders yet.</p>}
      {reminders && reminders.length > 0 && (
        <ul className="space-y-2.5">
          {reminders.slice(0, 6).map((r) => (
            <li key={r.id} className="flex items-start gap-2.5 text-[13px] text-white/60">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-warning" />
              <span>
                {r.text}
                {r.due_at && <span className="ml-1.5 text-white/35">· {r.due_at}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </WidgetCard>
  );
}
