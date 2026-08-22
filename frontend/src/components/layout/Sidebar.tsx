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
import { QuickActions } from "../ui/QuickActions";
import { ProfileCard } from "../ui/ProfileCard";

const NAV_ITEMS = [
  { icon: Home, label: "Home" },
  { icon: SquarePen, label: "Chats", action: "newChat" as const },
  { icon: Mic, label: "Voice", action: "voice" as const },
  { icon: BookOpen, label: "Knowledge", soon: true },
  { icon: FolderKanban, label: "Projects", soon: true },
  { icon: Zap, label: "Automation", soon: true },
  { icon: BarChart3, label: "Analytics", soon: true },
  { icon: Settings, label: "Settings", soon: true },
];

export function Sidebar({
  onSoonClick,
  onNewChat,
  onEnterVoice,
  voiceModeActive,
  onQuickAction,
  quickActionsDisabled,
}: {
  onSoonClick: (label: string) => void;
  onNewChat: () => void;
  onEnterVoice: () => void;
  voiceModeActive: boolean;
  onQuickAction: (q: string) => void;
  quickActionsDisabled: boolean;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const connection = useAssistantStore((s) => s.connection);

  return (
    <aside
      className={`relative flex flex-col border-r border-white/[0.06] bg-white/[0.015] backdrop-blur-2xl transition-[width] duration-300 ${
        collapsed ? "w-[76px]" : "w-[232px]"
      }`}
    >
      <div className="flex items-center gap-3 px-4 py-5">
        <ProfileCard />
        {!collapsed && (
          <span className="text-sm font-semibold tracking-[0.2em] text-white/90">NKY.AI</span>
        )}
      </div>

      <div className="mx-4 border-t border-white/[0.08]" />

      <div className="thin-scroll min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3.5">
        <nav className="space-y-2">
          {NAV_ITEMS.map((item) => {
            const isActive =
              item.label === "Home" ? !voiceModeActive : item.label === "Voice" ? voiceModeActive : false;
            return (
            <button
              key={item.label}
              type="button"
              onClick={() => {
                if (item.action === "newChat") onNewChat();
                else if (item.action === "voice") onEnterVoice();
                else if (item.soon) onSoonClick(item.label);
              }}
              className={`group flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-[13px] transition-all ${
                isActive
                  ? "border-primary/50 bg-primary/10 text-white shadow-[0_0_18px_-6px_rgba(0,229,255,0.6)]"
                  : "border-white/[0.07] bg-white/[0.015] text-white/60 hover:border-primary/30 hover:bg-white/[0.04] hover:text-white/90"
              }`}
            >
              <item.icon
                size={16}
                strokeWidth={1.8}
                className={`shrink-0 ${isActive ? "text-primary" : "text-primary/45 group-hover:text-primary/80"}`}
              />
              {!collapsed && <span className="truncate">{item.label}</span>}
              {!collapsed && item.soon && (
                <span className="ml-auto rounded-full border border-primary/25 px-1.5 py-0.5 text-[9px] font-medium text-primary/55">
                  Soon
                </span>
              )}
            </button>
            );
          })}
        </nav>

        <div className="border-t border-white/[0.08] pt-4">
          {!collapsed && (
            <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wider text-white/45">
              Quick Actions
            </div>
          )}
          <QuickActions onPick={onQuickAction} disabled={quickActionsDisabled} collapsed={collapsed} />
        </div>
      </div>

      <div className="border-t border-white/[0.08] p-3">
        <div className="flex items-center gap-2.5 rounded-xl border border-white/[0.08] bg-white/[0.02] px-2.5 py-2">
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="grid h-6 w-6 shrink-0 place-items-center rounded-lg text-white/40 transition-colors hover:bg-white/[0.06] hover:text-white/80"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />}
          </button>
          {!collapsed && (
            <>
              <div className="h-4 w-px bg-white/10" />
              <div className="flex items-center gap-1.5 text-[11px] text-white/45">
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
            </>
          )}
        </div>
      </div>
    </aside>
  );
}
