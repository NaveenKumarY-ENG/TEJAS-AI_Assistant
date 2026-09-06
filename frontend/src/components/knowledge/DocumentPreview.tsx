import { useEffect, useState } from "react";
import { Download, Loader2, Sparkles, X } from "lucide-react";

interface DocumentDetail {
  id: number;
  filename: string;
  doc_type: string;
  raw_text: string;
  has_file?: boolean;
}

/**
 * Read-the-actual-content panel for one document — fetches its full
 * extracted text (GET /api/knowledge/{id}, deliberately separate from the
 * bulk list which excludes raw_text; see structured.list_documents'
 * docstring) on open. Previously the only way to see what TEJAS actually
 * extracted from a document was to ask it in chat and hope; this shows the
 * real stored text directly. Also offers on-demand summarization and, for
 * a real file upload, downloading the original bytes back
 * (memory/knowledge.py's get_original_file — never available for notes/
 * URLs, which have no "original file" separate from their own text).
 */
export function DocumentPreview({ documentId, onClose }: { documentId: number; onClose: () => void }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [summary, setSummary] = useState<string | null>(null);
  const [summarizing, setSummarizing] = useState(false);
  const [summarizeError, setSummarizeError] = useState<string | null>(null);

  useEffect(() => {
    setDoc(null);
    setSummary(null);
    setSummarizeError(null);
    setError(null);
    setLoading(true);
    fetch(`/api/knowledge/${documentId}`)
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load document");
        return r.json();
      })
      .then((data) => setDoc(data))
      .catch(() => setError("Failed to load this document's content."))
      .finally(() => setLoading(false));
  }, [documentId]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const handleSummarize = async () => {
    setSummarizing(true);
    setSummarizeError(null);
    try {
      const res = await fetch(`/api/knowledge/${documentId}/summarize`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Summarization failed");
      setSummary(data.summary);
    } catch (err) {
      setSummarizeError(err instanceof Error ? err.message : "Summarization failed");
    } finally {
      setSummarizing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/[0.1] bg-[#0a0510] shadow-[0_24px_64px_-12px_rgba(0,0,0,0.7)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-white/[0.08] px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-[15px] font-semibold text-white">{doc?.filename ?? "Loading…"}</h2>
            {doc?.doc_type && <p className="mt-0.5 text-[11.5px] text-white/40">{doc.doc_type}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {doc?.has_file && (
              <a
                href={`/api/knowledge/${documentId}/file`}
                className="flex items-center gap-1.5 rounded-lg border border-white/[0.1] bg-white/[0.03] px-3 py-1.5 text-[12px] text-white/70 transition-colors hover:border-primary/40 hover:text-white"
              >
                <Download size={13} strokeWidth={1.8} />
                Download
              </a>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close preview"
              className="grid h-8 w-8 place-items-center rounded-lg border border-white/[0.08] bg-white/[0.03] text-white/60 transition-colors hover:border-primary/40 hover:text-white"
            >
              <X size={15} strokeWidth={1.8} />
            </button>
          </div>
        </div>

        <div className="thin-scroll min-h-0 flex-1 overflow-y-auto p-5">
          {loading ? (
            <div className="space-y-2">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-4 animate-pulse rounded bg-white/[0.05]" />
              ))}
            </div>
          ) : error ? (
            <p className="text-[13px] text-warning">{error}</p>
          ) : (
            <>
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-[11px] font-semibold uppercase tracking-wider text-white/40">Summary</h3>
                {!summary && (
                  <button
                    type="button"
                    onClick={handleSummarize}
                    disabled={summarizing}
                    className="flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/10 px-2.5 py-1 text-[11.5px] text-primary transition-colors hover:bg-primary/15 disabled:opacity-50"
                  >
                    {summarizing ? (
                      <Loader2 size={12} strokeWidth={1.8} className="animate-spin" />
                    ) : (
                      <Sparkles size={12} strokeWidth={1.8} />
                    )}
                    {summarizing ? "Summarizing…" : "Summarize"}
                  </button>
                )}
              </div>
              {summarizeError && <p className="mt-1.5 text-[12px] text-warning">{summarizeError}</p>}
              {summary && (
                <p className="mt-2 rounded-xl border border-primary/20 bg-primary/[0.05] p-3 text-[13px] leading-relaxed text-white/80">
                  {summary}
                </p>
              )}

              <h3 className="mt-5 text-[11px] font-semibold uppercase tracking-wider text-white/40">Full Content</h3>
              <p className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-white/70">
                {doc?.raw_text || "No stored content."}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
