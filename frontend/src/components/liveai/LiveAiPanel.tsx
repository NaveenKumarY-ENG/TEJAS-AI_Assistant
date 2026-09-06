import { Globe2, ShoppingBag, Search as SearchIcon } from "lucide-react";
import { ConversationPanel } from "../ui/ConversationPanel";
import { ChatInput } from "../ui/ChatInput";
import { WidgetCard } from "../ui/WidgetCard";
import { HOLOGRAM_BACKDROP_STYLE } from "../../utils/hologramBackdrop";
import { useAssistantStore, type CoreState } from "../../store/assistantStore";
import type { TtsBoundarySignal } from "../../hooks/useSpeechSynthesis";

// Quick-start prompts for the suggested-action chips — plain text sent
// through the exact same sendMessage path Sidebar's own Quick Actions
// already use (see App.tsx's onQuickAction={sendMessage}), not new
// plumbing.
const SUGGESTED_ACTIONS: { label: string; prompt: string }[] = [
  { label: "Search the Web", prompt: "Search the web for " },
  { label: "Research", prompt: "Research " },
  { label: "Compare", prompt: "Compare " },
  { label: "Find Products", prompt: "Find me " },
  { label: "Shop", prompt: "Find this on Amazon and Flipkart: " },
];

/**
 * Live.AI — TEJAS's real-time web/shopping view. Deliberately NOT a second
 * chat/session system: there is exactly one WebSocket, session, and message
 * timeline in this app (confirmed while planning this — no router, no
 * per-page state store), so this reuses the SAME <ConversationPanel/> +
 * <ChatInput/> Home uses. A message or tool call made here shows up if the
 * user switches back to Home too — that's correct, it's one continuous
 * conversation with TEJAS, not a bug. What's different here is the page
 * chrome: a live-status strip (real backend capability checks from
 * /api/meta, not decorative), suggested research/shopping prompts, and a
 * sidebar card naming what Live.AI can actually do — including its real
 * limitations (Flipkart/other sites are research-only, no cart/checkout).
 */
export function LiveAiPanel({
  onSend,
  disabled,
  coreState,
  ttsBoundaryRef,
  stopSpeaking,
  onSoonClick,
  onVoiceError,
  browserAvailable,
  searchConfigured,
}: {
  onSend: (text: string, opts?: { speak?: boolean }) => void;
  disabled: boolean;
  coreState: CoreState;
  ttsBoundaryRef: React.RefObject<TtsBoundarySignal | null>;
  stopSpeaking: () => void;
  onSoonClick: (label: string) => void;
  onVoiceError: (message: string) => void;
  browserAvailable: boolean;
  searchConfigured: boolean;
}) {
  const assistantName = useAssistantStore((s) => s.assistantName);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)] gap-5 px-6 pb-6 lg:grid-cols-[1fr_300px]">
      <section
        className="relative flex min-h-0 flex-col overflow-hidden rounded-2xl border border-white/[0.06] bg-[#050208]"
        style={HOLOGRAM_BACKDROP_STYLE}
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-5 py-3">
          <span className="flex items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-2.5 py-1 text-[11px] font-semibold text-success">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-success" />
            LIVE
          </span>
          <span
            className={`rounded-full border px-2.5 py-1 text-[11px] ${
              searchConfigured ? "border-white/10 text-white/50" : "border-warning/30 text-warning"
            }`}
          >
            Web search: {searchConfigured ? "configured" : "not set up"}
          </span>
          <span
            className={`rounded-full border px-2.5 py-1 text-[11px] ${
              browserAvailable ? "border-white/10 text-white/50" : "border-warning/30 text-warning"
            }`}
          >
            Browser tools: {browserAvailable ? "available" : "not installed"}
          </span>
        </div>

        <div className="thin-scroll relative z-10 flex h-full min-h-0 flex-col overflow-y-auto">
          <p className="pt-6 pb-2 text-center text-[11px] text-white/30">
            {assistantName} can search, browse, and shop live for you. Verify important information.
          </p>

          <div className="flex flex-wrap gap-2 px-5 pb-3">
            {SUGGESTED_ACTIONS.map((action) => (
              <button
                key={action.label}
                type="button"
                disabled={disabled}
                onClick={() => onSend(action.prompt)}
                className="rounded-full border border-white/[0.08] bg-white/[0.02] px-3 py-1.5 text-[12px] text-white/65 transition-colors hover:border-primary/30 hover:bg-white/[0.05] hover:text-white/90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {action.label}
              </button>
            ))}
          </div>

          <div className="px-5 pb-3">
            <ChatInput
              disabled={disabled}
              coreState={coreState}
              ttsBoundaryRef={ttsBoundaryRef}
              stopSpeaking={stopSpeaking}
              onSend={onSend}
              onSoonClick={onSoonClick}
              onVoiceError={onVoiceError}
            />
          </div>
          <ConversationPanel />
        </div>
      </section>

      <aside className="thin-scroll hidden min-h-0 flex-col gap-3.5 overflow-y-auto pt-1 lg:flex">
        <WidgetCard title="Live Tools">
          <ul className="space-y-2.5 text-[12px] text-white/65">
            <li className="flex items-center gap-2">
              <SearchIcon size={14} className="shrink-0 text-primary/70" />
              <span>Web search &amp; page reading — {searchConfigured ? "ready" : "needs SEARCH_API_KEY"}</span>
            </li>
            <li className="flex items-center gap-2">
              <ShoppingBag size={14} className="shrink-0 text-primary/70" />
              <span>Amazon search + cart/checkout review — {browserAvailable ? "ready" : "needs Playwright"}</span>
            </li>
            <li className="flex items-center gap-2">
              <ShoppingBag size={14} className="shrink-0 text-primary/70" />
              <span>Flipkart search (research only, no cart) — {browserAvailable ? "ready" : "needs Playwright"}</span>
            </li>
            <li className="flex items-center gap-2">
              <Globe2 size={14} className="shrink-0 text-primary/70" />
              <span>Open any other website — {browserAvailable ? "ready" : "needs Playwright"}</span>
            </li>
          </ul>
        </WidgetCard>

        <WidgetCard title="Good to know">
          <p className="text-[12px] leading-relaxed text-white/55">
            Live.AI only automates carts and checkout on Amazon, and it always stops at the checkout
            review page — you place the final order yourself. Flipkart and other sites are
            research-only: {assistantName} can search and open them, but never fakes a purchase.
          </p>
        </WidgetCard>
      </aside>
    </div>
  );
}
