import {
  Home,
  SquarePen,
  Mic,
  BookOpen,
  FolderKanban,
  Zap,
  BarChart3,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useState } from "react";
import { useAssistantStore } from "../../store/assistantStore";

const NAV_ITEMS = [
  { icon: Home, label: "Home", active: true },
  { icon: SquarePen, label: "Chats", action: "newChat" as const },
  { icon: Mic, label: "Voice", soon: true },
  { icon: BookOpen, label: "Knowledge", soon: true },
  { icon: FolderKanban, label: "Projects", soon: true },
  { icon: Zap, label: "Automation", soon: true },
  { icon: BarChart3, label: "Analytics", soon: true },
  { icon: Settings, label: "Settings", soon: true },
];

export function Sidebar({
  onSoonClick,
  onNewChat,
}: {
  onSoonClick: (label: string) => void;
  onNewChat: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const connection = useAssistantStore((s) => s.connection);

  return (
    <aside
      className={`relative flex flex-col border-r border-white/[0.06] bg-white/[0.015] backdrop-blur-2xl transition-[width] duration-300 ${
        collapsed ? "w-[72px]" : "w-[220px]"
      }`}
    >
      <div className="flex items-center gap-2.5 px-4 py-5">
        <div className="relative h-7 w-7 shrink-0">
          <div className="absolute inset-0 rounded-full bg-primary/30 blur-md" />
          <div className="absolute inset-[3px] rounded-full border border-primary/70" />
          <div className="absolute inset-[8px] rounded-full bg-primary shadow-[0_0_10px_2px_rgba(0,229,255,0.7)]" />
        </div>
        {!collapsed && (
          <span className="text-sm font-semibold tracking-[0.14em] text-white/90">NEXUS</span>
        )}
      </div>

      <nav className="flex-1 space-y-1 px-2.5">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.label}
            type="button"
            onClick={() => {
              if (item.action === "newChat") onNewChat();
              else if (item.soon) onSoonClick(item.label);
            }}
            className={`group relative flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-[13px] transition-colors ${
              item.active
                ? "bg-gradient-to-r from-primary/15 to-transparent text-white"
                : "text-white/55 hover:bg-white/[0.05] hover:text-white/90"
            }`}
          >
            {item.active && (
              <span className="absolute left-0 top-2 bottom-2 w-[2.5px] rounded-full bg-primary shadow-[0_0_8px_rgba(0,229,255,0.8)]" />
            )}
            <item.icon size={16} strokeWidth={1.8} className="shrink-0" />
            {!collapsed && <span className="truncate">{item.label}</span>}
            {!collapsed && item.soon && (
              <span className="ml-auto rounded-full border border-white/10 px-1.5 py-0.5 text-[9px] font-medium text-white/35">
                Soon
              </span>
            )}
          </button>
        ))}
      </nav>

      <div className="border-t border-white/[0.06] p-3">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="flex w-full items-center justify-center gap-2 rounded-lg py-2 text-white/40 transition-colors hover:bg-white/[0.05] hover:text-white/80"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
        {!collapsed && (
          <div className="mt-2 flex items-center justify-center gap-1.5 text-[11px] text-white/35">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                connection === "online" ? "bg-success shadow-[0_0_6px_rgba(0,255,200,0.8)]" : "bg-white/25"
              }`}
            />
            {connection === "online"
              ? "Connected"
              : connection === "connecting"
                ? "Connecting"
                : connection === "reconnecting"
                  ? "Reconnecting"
                  : "Offline"}
          </div>
        )}
      </div>
    </aside>
  );
}
