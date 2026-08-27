import { useEffect, useRef } from "react";
import Markdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import { Paperclip } from "lucide-react";
import { useAssistantStore } from "../../store/assistantStore";

// Only these schemes render as a real, clickable link. Assistant text can
// echo content from web_search results (page titles/snippets from
// arbitrary sites), and a smaller local model can reproduce a malicious
// markdown link verbatim — without this check, react-markdown would happily
// turn "[Click here](javascript:...)" into a real anchor a user could click
// and execute script in the app's origin. Anything else renders as plain
// text instead of a link, same as if react-markdown weren't here at all.
function isSafeHref(href: string | undefined): href is string {
  if (!href) return false;
  try {
    const url = new URL(href, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" || url.protocol === "mailto:";
  } catch {
    return false;
  }
}

// Maps Markdown elements to the panel's existing dark-glass style — kept
// minimal (no @tailwindcss/typography dependency) since replies only ever
// use a handful of these (bold, lists, links, inline code, paragraphs).
// Only assistant messages render through this; user messages stay plain
// text (see the `entry.role === "assistant"` check below) since a user's
// own typed text shouldn't be re-interpreted as Markdown syntax.
const markdownComponents = {
  p: ({ children }: { children?: React.ReactNode }) => <p className="mb-1.5 last:mb-0">{children}</p>,
  strong: ({ children }: { children?: React.ReactNode }) => <strong className="font-semibold text-white">{children}</strong>,
  em: ({ children }: { children?: React.ReactNode }) => <em className="italic">{children}</em>,
  ul: ({ children }: { children?: React.ReactNode }) => <ul className="mb-1.5 ml-4 list-disc space-y-0.5 last:mb-0">{children}</ul>,
  ol: ({ children }: { children?: React.ReactNode }) => <ol className="mb-1.5 ml-4 list-decimal space-y-0.5 last:mb-0">{children}</ol>,
  li: ({ children }: { children?: React.ReactNode }) => <li>{children}</li>,
  code: ({ children }: { children?: React.ReactNode }) => (
    <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-[13px]">{children}</code>
  ),
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) =>
    isSafeHref(href) ? (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2 hover:text-primary/80">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
};

export function ConversationPanel({ compactEmptyHint = false }: { compactEmptyHint?: boolean }) {
  const timeline = useAssistantStore((s) => s.timeline);
  const assistantName = useAssistantStore((s) => s.assistantName);
  const coreState = useAssistantStore((s) => s.coreState);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [timeline]);

  const lastEntry = timeline[timeline.length - 1];
  // Avoid showing generic "thinking" dots on top of a tool pill that's
  // already conveying "in progress" via its own pulsing indicator.
  const showThinkingDots = coreState === "thinking" && !(lastEntry?.kind === "tool" && !lastEntry.done);

  return (
    <div ref={scrollRef} className="thin-scroll flex min-h-[180px] flex-1 flex-col gap-3 overflow-y-auto px-6 py-4">
      {timeline.length === 0 && (
        // Pushed to the bottom of this flex-1 area (mt-auto) rather than
        // sitting near the top — the hologram is vertically centered in the
        // section behind this panel, so a top-anchored hint used to land
        // right over its center. Confirmed live: bottom placement clears it.
        // Voice Mode's box is much shorter than the Home screen's, with a
        // footer (hands-free/mic) right below this area — the full sentence
        // was tall enough to crowd right up against it. Confirmed live.
        <p className="mx-auto mt-auto mb-2 max-w-md rounded-2xl bg-black/30 px-5 py-3 text-center text-[14px] text-white/50 backdrop-blur-md">
          {compactEmptyHint
            ? "Ask Anything"
            : "Ask me anything — I can search, calculate, check the weather, and remember what matters."}
        </p>
      )}

      {timeline.map((entry) => {
        if (entry.kind === "tool") {
          return (
            <div key={entry.id} className="flex w-fit flex-col items-start gap-1">
              <div
                className={`inline-flex w-fit items-center gap-2 rounded-full border border-white/10 bg-black/40 px-3 py-1.5 font-mono text-[11.5px] backdrop-blur-md ${
                  entry.done ? "text-white/30" : "text-white/70"
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${entry.done ? "bg-white/30" : "bg-success shadow-[0_0_6px_rgba(0,255,200,0.7)]"}`}
                  style={entry.done ? undefined : { animation: "breathe 1.2s ease-in-out infinite" }}
                />
                {entry.name.replace(/_/g, " ")}
              </div>
              {/* Citations — which knowledge-base document(s) this reply is
                  actually grounded in, attached once search_knowledge's
                  result comes back (see useAssistantSocket.ts's "tool_result"
                  handler). Absent for every other tool. */}
              {entry.sources && entry.sources.length > 0 && (
                <div className="flex items-center gap-1.5 pl-3 text-[11px] text-white/35">
                  <Paperclip size={10} strokeWidth={1.8} className="shrink-0" />
                  <span className="truncate">{entry.sources.join(", ")}</span>
                </div>
              )}
            </div>
          );
        }

        return (
          <div key={entry.id} className={`flex animate-[enter_.3s_ease-out] ${entry.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[75%] rounded-2xl px-4 py-2.5 backdrop-blur-md ${
                entry.role === "user" ? "border border-accent/25 bg-accent/20" : "border border-primary/15 bg-black/45"
              }`}
            >
              <div className={`mb-1 text-[11px] font-semibold tracking-wide ${entry.role === "assistant" ? "text-primary" : "text-white/45"}`}>
                {entry.role === "assistant" ? assistantName : "You"}
              </div>
              <div className="text-[14.5px] leading-relaxed text-white/95">
                {entry.role === "assistant" ? (
                  <Markdown remarkPlugins={[remarkBreaks]} components={markdownComponents}>
                    {entry.content}
                  </Markdown>
                ) : (
                  <div className="whitespace-pre-wrap">{entry.content}</div>
                )}
              </div>
            </div>
          </div>
        );
      })}

      {showThinkingDots && (
        <div className="flex w-fit gap-1 rounded-full bg-black/40 px-3 py-2 backdrop-blur-md">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1.5 w-1.5 rounded-full bg-white/50"
              style={{ animation: "hop 1.2s ease-in-out infinite", animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
