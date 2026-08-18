import { Cloud, Search, Cpu, BellPlus, ListChecks, BrainCircuit, Globe, Calculator, type LucideIcon } from "lucide-react";

const ACTIONS: { icon: LucideIcon; label: string; q: string }[] = [
  { icon: Cloud, label: "Check the weather", q: "What's the weather in Bengaluru?" },
  { icon: Search, label: "Search the web", q: "Search the web for the latest AI model releases" },
  { icon: Cpu, label: "System check", q: "How much disk space do I have?" },
  { icon: BellPlus, label: "Set a reminder", q: "Remind me to review the pull request tomorrow" },
  { icon: ListChecks, label: "List reminders", q: "What reminders do I have?" },
  { icon: BrainCircuit, label: "Remember something", q: "Remember that I prefer concise, direct answers" },
  { icon: Globe, label: "World time", q: "What time is it in Tokyo right now?" },
  { icon: Calculator, label: "Quick calculation", q: "Calculate an 18% tip on $92.50" },
];

/** One-click canned prompts, sent immediately as if typed — lives in the
 *  sidebar (see Sidebar.tsx) so it's always available without competing for
 *  space with the conversation itself. */
export function QuickActions({
  onPick,
  disabled,
  collapsed = false,
}: {
  onPick: (q: string) => void;
  disabled: boolean;
  collapsed?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      {ACTIONS.map((a) => (
        <button
          key={a.label}
          type="button"
          disabled={disabled}
          onClick={() => onPick(a.q)}
          title={a.label}
          className="group flex w-full items-center gap-3 rounded-xl border border-white/[0.07] bg-white/[0.015] px-3 py-2.5 text-[13px] text-white/60 transition-all hover:border-primary/30 hover:bg-white/[0.04] hover:text-white/90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <a.icon size={16} strokeWidth={1.8} className="shrink-0 text-primary/45 group-hover:text-primary/80" />
          {!collapsed && <span className="truncate">{a.label}</span>}
        </button>
      ))}
    </div>
  );
}
