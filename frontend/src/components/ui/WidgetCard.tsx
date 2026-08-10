import type { ReactNode } from "react";

export function WidgetCard({
  title,
  tag,
  tagTone = "success",
  children,
}: {
  title: string;
  tag?: string;
  tagTone?: "success" | "accent" | "warning";
  children: ReactNode;
}) {
  const tagClasses =
    tagTone === "success"
      ? "bg-success/10 text-success"
      : tagTone === "warning"
        ? "bg-warning/10 text-warning"
        : "bg-accent/15 text-accent";

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 backdrop-blur-2xl">
      <div className="mb-2.5 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-white/45">{title}</span>
        {tag && <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${tagClasses}`}>{tag}</span>}
      </div>
      {children}
    </div>
  );
}
