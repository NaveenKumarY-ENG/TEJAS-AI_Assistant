const ACTIONS = [
  { label: "Check the weather", q: "What's the weather in Bengaluru?" },
  { label: "Search the web", q: "Search the web for the latest AI model releases" },
  { label: "System check", q: "How much disk space do I have?" },
  { label: "Set a reminder", q: "Remind me to review the pull request tomorrow" },
];

export function QuickActions({ onPick, disabled }: { onPick: (q: string) => void; disabled: boolean }) {
  return (
    <div className="flex flex-wrap justify-center gap-2">
      {ACTIONS.map((a) => (
        <button
          key={a.label}
          type="button"
          disabled={disabled}
          onClick={() => onPick(a.q)}
          className="rounded-full border border-white/10 bg-white/[0.03] px-3.5 py-1.5 text-[12.5px] text-white/55 transition-colors hover:border-primary/40 hover:bg-primary/10 hover:text-white disabled:opacity-40"
        >
          {a.label}
        </button>
      ))}
    </div>
  );
}
