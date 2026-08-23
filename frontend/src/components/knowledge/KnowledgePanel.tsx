import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  ChevronDown,
  FileText,
  Folder,
  FolderPlus,
  Link as LinkIcon,
  Loader2,
  NotebookPen,
  Pencil,
  Plus,
  Search,
  Tag,
  Trash2,
  Upload,
  X,
} from "lucide-react";

interface DocumentItem {
  id: number;
  filename: string;
  chunk_count: number;
  created_at: string;
  tags: string[];
  source_type: "manual" | "folder";
}

interface SearchResult {
  filename: string;
  text: string;
}

interface WatchedFolder {
  id: number;
  path: string;
  file_count: number;
}

const ACCEPTED_EXTENSIONS = [".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"];

/** A URL-ingested document is stored with the URL itself as its "filename"
 *  (see memory/knowledge.py's ingest_url) — no separate source-type column,
 *  so this is just how the UI tells the two apart. Same trick covers notes
 *  incidentally (a note's title basically never starts with "http"). */
function isUrlSource(filename: string): boolean {
  return filename.startsWith("http://") || filename.startsWith("https://");
}

function hasAcceptedExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function parseTags(input: string): string[] {
  return input
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

const inputClass =
  "min-w-0 flex-1 rounded-xl border border-white/[0.08] bg-white/[0.02] px-3.5 py-2.5 text-[13px] text-white/90 placeholder:text-white/30 outline-none transition-colors focus:border-primary/40 disabled:opacity-50";

/**
 * Fullscreen knowledge-base management panel (sidebar → Knowledge) — upload
 * documents, paste URLs, jot notes, tag/organize/search what's indexed, and
 * delete what you don't want anymore. Structurally mirrors VoiceMode.tsx's
 * overlay (fixed inset-0, Escape to close, exit button) but with plain panel
 * content — no hologram/voice concerns here, so no AssistantCore. Fully
 * self-contained: fetches its own list on mount rather than needing global
 * store state, same as VoiceMode owns its own voice-specific state.
 */
export function KnowledgePanel({ onExit, ocrAvailable }: { onExit: () => void; ocrAvailable: boolean }) {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const [uploadTags, setUploadTags] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [urlInput, setUrlInput] = useState("");
  const [urlTags, setUrlTags] = useState("");
  const [ingestingUrl, setIngestingUrl] = useState(false);

  const [noteOpen, setNoteOpen] = useState(false);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteText, setNoteText] = useState("");
  const [noteTags, setNoteTags] = useState("");
  const [savingNote, setSavingNote] = useState(false);

  const [activeTagFilter, setActiveTagFilter] = useState<string | null>(null);
  const [editingTagsId, setEditingTagsId] = useState<number | null>(null);
  const [tagEditValue, setTagEditValue] = useState("");

  const [searchQuery, setSearchQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);

  const [folders, setFolders] = useState<WatchedFolder[]>([]);
  const [folderSectionOpen, setFolderSectionOpen] = useState(false);
  const [folderPathInput, setFolderPathInput] = useState("");
  const [addingFolder, setAddingFolder] = useState(false);

  useEffect(() => {
    fetch("/api/knowledge")
      .then((r) => r.json())
      .then((data) => setDocuments(data.documents ?? []))
      .catch(() => setError("Failed to load documents."))
      .finally(() => setLoading(false));
    fetch("/api/knowledge/folders")
      .then((r) => r.json())
      .then((data) => setFolders(data.folders ?? []))
      .catch(() => {});
  }, []);

  // Watched folders ingest in the background (server-side watchdog thread),
  // so a file dropped on disk needs some way to show up here without the
  // user closing and reopening the panel — a light poll rather than a
  // dedicated push channel for something this infrequent.
  useEffect(() => {
    if (folders.length === 0) return;
    const refresh = () => {
      fetch("/api/knowledge")
        .then((r) => r.json())
        .then((data) => setDocuments(data.documents ?? []))
        .catch(() => {});
      fetch("/api/knowledge/folders")
        .then((r) => r.json())
        .then((data) => setFolders(data.folders ?? []))
        .catch(() => {});
    };
    // Fire once right away too — otherwise a just-watched folder sits at a
    // stale "0 files" for up to a full interval before the first tick.
    refresh();
    const interval = window.setInterval(refresh, 5000);
    return () => window.clearInterval(interval);
  }, [folders.length]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onExit();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onExit]);

  const handleFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    if (!hasAcceptedExtension(file.name)) {
      setError(`Unsupported file type — only ${ACCEPTED_EXTENSIONS.join(", ")} are supported right now.`);
      return;
    }

    setError(null);
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("tags", uploadTags);
      const res = await fetch("/api/knowledge", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
      setDocuments((docs) => [data, ...docs]);
      setUploadTags("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleIngestUrl = async () => {
    const url = urlInput.trim();
    if (!url) return;

    setError(null);
    setIngestingUrl(true);
    try {
      const res = await fetch("/api/knowledge/url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, tags: parseTags(urlTags) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to ingest URL");
      setDocuments((docs) => [data, ...docs]);
      setUrlInput("");
      setUrlTags("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to ingest URL");
    } finally {
      setIngestingUrl(false);
    }
  };

  const handleAddNote = async () => {
    const title = noteTitle.trim();
    const text = noteText.trim();
    if (!title || !text) return;

    setError(null);
    setSavingNote(true);
    try {
      const res = await fetch("/api/knowledge/note", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, text, tags: parseTags(noteTags) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to save note");
      setDocuments((docs) => [data, ...docs]);
      setNoteTitle("");
      setNoteText("");
      setNoteTags("");
      setNoteOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save note");
    } finally {
      setSavingNote(false);
    }
  };

  const handleDelete = async (id: number) => {
    const previous = documents;
    setDocuments((docs) => docs.filter((d) => d.id !== id)); // optimistic — restored below on failure
    try {
      const res = await fetch(`/api/knowledge/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Delete failed");
    } catch {
      setError("Failed to delete document.");
      setDocuments(previous);
    }
  };

  const startEditingTags = (doc: DocumentItem) => {
    setEditingTagsId(doc.id);
    setTagEditValue(doc.tags.join(", "));
  };

  const saveTags = async (id: number) => {
    const tags = parseTags(tagEditValue);
    setEditingTagsId(null);
    const previous = documents;
    setDocuments((docs) => docs.map((d) => (d.id === id ? { ...d, tags } : d))); // optimistic
    try {
      const res = await fetch(`/api/knowledge/${id}/tags`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tags }),
      });
      if (!res.ok) throw new Error("Failed to update tags");
    } catch {
      setError("Failed to update tags.");
      setDocuments(previous);
    }
  };

  const handleSearch = async () => {
    const q = searchQuery.trim();
    if (!q) {
      setSearchResults(null);
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const res = await fetch(`/api/knowledge/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Search failed");
      setSearchResults(data.results ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  };

  const clearSearch = () => {
    setSearchQuery("");
    setSearchResults(null);
  };

  const handleWatchFolder = async () => {
    const path = folderPathInput.trim();
    if (!path) return;

    setError(null);
    setAddingFolder(true);
    try {
      const res = await fetch("/api/knowledge/folders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to watch folder");
      setFolders((f) => [{ ...data, file_count: 0 }, ...f]);
      setFolderPathInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to watch folder");
    } finally {
      setAddingFolder(false);
    }
  };

  const handleUnwatchFolder = async (folder: WatchedFolder) => {
    if (!window.confirm(`Stop watching "${folder.path}"? This deletes every document it produced.`)) return;
    const previousFolders = folders;
    setFolders((f) => f.filter((x) => x.id !== folder.id)); // optimistic — restored below on failure
    try {
      const res = await fetch(`/api/knowledge/folders/${folder.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to unwatch folder");
      // The folder's documents are gone server-side too — a DocumentItem
      // doesn't carry which folder it came from, so refresh the whole list
      // rather than guessing which entries to drop.
      const docsRes = await fetch("/api/knowledge");
      const docsData = await docsRes.json();
      setDocuments(docsData.documents ?? []);
    } catch {
      setError("Failed to unwatch folder.");
      setFolders(previousFolders);
    }
  };

  const allTags = [...new Set(documents.flatMap((d) => d.tags))].sort();
  const visibleDocuments = activeTagFilter ? documents.filter((d) => d.tags.includes(activeTagFilter)) : documents;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-[#050505]">
      <div
        className="pointer-events-none fixed inset-0 opacity-[0.04]"
        style={{ backgroundImage: "repeating-linear-gradient(0deg, #fff 0px, transparent 1px, transparent 3px)" }}
      />

      <div className="relative z-10 mx-auto flex min-h-full max-w-2xl flex-col px-8 py-10">
        <div className="flex items-center justify-between">
          <span className="mono flex items-center gap-1.5 rounded-full border border-primary/25 px-3 py-1 text-[11px] tracking-wider text-primary/70">
            <BookOpen size={12} strokeWidth={1.8} />
            KNOWLEDGE BASE
          </span>
          <button
            type="button"
            onClick={onExit}
            aria-label="Close knowledge base"
            className="grid h-9 w-9 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03] text-white/60 transition-colors hover:border-primary/40 hover:text-white"
          >
            <X size={16} strokeWidth={1.8} />
          </button>
        </div>

        <h1 className="mt-6 text-[22px] font-semibold text-white">Documents</h1>
        <p className="mt-0.5 text-[13px] text-white/45">
          Upload notes or documents so TEJAS can search and answer questions from them.
        </p>

        {/* Search — swaps the browse view below for real snippet results. */}
        <div className="mt-5 flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={14} strokeWidth={1.8} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-white/30" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSearch();
                if (e.key === "Escape" && searchResults !== null) clearSearch();
              }}
              placeholder="Search your knowledge base…"
              className={`${inputClass} pl-9`}
            />
          </div>
          {searchResults !== null ? (
            <button
              type="button"
              onClick={clearSearch}
              className="shrink-0 rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 text-[13px] text-white/70 transition-colors hover:border-primary/40 hover:text-white"
            >
              Clear
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSearch}
              disabled={searching || !searchQuery.trim()}
              className="flex shrink-0 items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 text-[13px] text-white/70 transition-colors hover:border-primary/40 hover:text-white disabled:opacity-40"
            >
              {searching ? <Loader2 size={14} strokeWidth={1.8} className="animate-spin" /> : <Search size={14} strokeWidth={1.8} />}
              Search
            </button>
          )}
        </div>

        {error && <p className="mt-3 text-[12.5px] text-warning">{error}</p>}

        {searchResults !== null ? (
          <div className="thin-scroll mt-6 min-h-0 flex-1 space-y-2 overflow-y-auto">
            {searchResults.length === 0 ? (
              <p className="mx-auto mt-8 max-w-sm rounded-2xl bg-black/30 px-5 py-3 text-center text-[13px] text-white/45 backdrop-blur-md">
                No matches for "{searchQuery}".
              </p>
            ) : (
              searchResults.map((r, i) => (
                <div key={i} className="rounded-xl border border-white/[0.07] bg-white/[0.015] px-4 py-3">
                  <div className="mb-1 flex items-center gap-1.5 text-[11.5px] text-primary/70">
                    {isUrlSource(r.filename) ? <LinkIcon size={11} strokeWidth={1.8} /> : <FileText size={11} strokeWidth={1.8} />}
                    <span className="truncate">{r.filename}</span>
                  </div>
                  <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-white/70">{r.text}</p>
                </div>
              ))
            )}
          </div>
        ) : (
          <>
            <div className="mt-2 flex flex-col gap-2 rounded-2xl border border-dashed border-white/[0.12] bg-white/[0.02] p-4">
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_EXTENSIONS.join(",")}
                className="hidden"
                onChange={(e) => handleFiles(e.target.files)}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="flex items-center justify-center gap-2.5 py-4 text-[13px] text-white/50 transition-colors hover:text-white/80 disabled:opacity-50"
              >
                {uploading ? (
                  <>
                    <Loader2 size={16} strokeWidth={1.8} className="animate-spin" />
                    Uploading…
                  </>
                ) : (
                  <>
                    <Upload size={16} strokeWidth={1.8} />
                    Click to upload a document ({ACCEPTED_EXTENSIONS.join(", ")})
                  </>
                )}
              </button>
              <div className="flex items-center gap-2 px-1">
                <Tag size={12} strokeWidth={1.8} className="shrink-0 text-white/25" />
                <input
                  type="text"
                  value={uploadTags}
                  onChange={(e) => setUploadTags(e.target.value)}
                  placeholder="tags (comma separated, optional)"
                  className="min-w-0 flex-1 bg-transparent text-[12px] text-white/70 placeholder:text-white/25 outline-none"
                />
              </div>
              {!ocrAvailable && (
                <p className="px-1 text-[11px] text-white/30">
                  OCR isn't installed on this server — image/scanned-PDF uploads will fail. See README.
                </p>
              )}
            </div>

            <div className="mt-3 flex items-center gap-2">
              <div className="h-px flex-1 bg-white/[0.08]" />
              <span className="text-[11px] text-white/30">or paste a URL</span>
              <div className="h-px flex-1 bg-white/[0.08]" />
            </div>

            <div className="mt-3 flex flex-col gap-2 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-3">
              <div className="flex items-center gap-2">
                <input
                  type="url"
                  value={urlInput}
                  onChange={(e) => setUrlInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleIngestUrl();
                  }}
                  placeholder="https://example.com/article"
                  disabled={ingestingUrl}
                  className={inputClass}
                />
                <button
                  type="button"
                  onClick={handleIngestUrl}
                  disabled={ingestingUrl || !urlInput.trim()}
                  className="flex shrink-0 items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 text-[13px] text-white/70 transition-colors hover:border-primary/40 hover:text-white disabled:opacity-40"
                >
                  {ingestingUrl ? <Loader2 size={14} strokeWidth={1.8} className="animate-spin" /> : <LinkIcon size={14} strokeWidth={1.8} />}
                  Add
                </button>
              </div>
              <div className="flex items-center gap-2 px-1">
                <Tag size={12} strokeWidth={1.8} className="shrink-0 text-white/25" />
                <input
                  type="text"
                  value={urlTags}
                  onChange={(e) => setUrlTags(e.target.value)}
                  placeholder="tags (comma separated, optional)"
                  className="min-w-0 flex-1 bg-transparent text-[12px] text-white/70 placeholder:text-white/25 outline-none"
                />
              </div>
            </div>

            {/* Add a note — collapsed by default, no file needed. */}
            <div className="mt-3 rounded-2xl border border-white/[0.08] bg-white/[0.02]">
              <button
                type="button"
                onClick={() => setNoteOpen((o) => !o)}
                className="flex w-full items-center justify-between px-4 py-3 text-[13px] text-white/60 transition-colors hover:text-white/90"
              >
                <span className="flex items-center gap-2">
                  <NotebookPen size={14} strokeWidth={1.8} />
                  Add a note
                </span>
                <ChevronDown size={14} strokeWidth={1.8} className={`transition-transform ${noteOpen ? "rotate-180" : ""}`} />
              </button>
              {noteOpen && (
                <div className="flex flex-col gap-2 border-t border-white/[0.08] p-4">
                  <input
                    type="text"
                    value={noteTitle}
                    onChange={(e) => setNoteTitle(e.target.value)}
                    placeholder="Title"
                    className={inputClass}
                  />
                  <textarea
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    placeholder="Write your note…"
                    rows={4}
                    className={`${inputClass} resize-none`}
                  />
                  <div className="flex items-center gap-2">
                    <Tag size={12} strokeWidth={1.8} className="shrink-0 text-white/25" />
                    <input
                      type="text"
                      value={noteTags}
                      onChange={(e) => setNoteTags(e.target.value)}
                      placeholder="tags (comma separated, optional)"
                      className="min-w-0 flex-1 bg-transparent text-[12px] text-white/70 placeholder:text-white/25 outline-none"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={handleAddNote}
                    disabled={savingNote || !noteTitle.trim() || !noteText.trim()}
                    className="flex items-center justify-center gap-2 self-end rounded-xl border border-primary/30 bg-primary/10 px-4 py-2 text-[12.5px] text-primary transition-colors hover:bg-primary/15 disabled:opacity-40"
                  >
                    {savingNote ? <Loader2 size={13} strokeWidth={1.8} className="animate-spin" /> : <Plus size={13} strokeWidth={1.8} />}
                    Save note
                  </button>
                </div>
              )}
            </div>

            {/* Watched folders — collapsed by default, same shape as "Add a note". */}
            <div className="mt-3 rounded-2xl border border-white/[0.08] bg-white/[0.02]">
              <button
                type="button"
                onClick={() => setFolderSectionOpen((o) => !o)}
                className="flex w-full items-center justify-between px-4 py-3 text-[13px] text-white/60 transition-colors hover:text-white/90"
              >
                <span className="flex items-center gap-2">
                  <FolderPlus size={14} strokeWidth={1.8} />
                  Watched folders {folders.length > 0 && `(${folders.length})`}
                </span>
                <ChevronDown size={14} strokeWidth={1.8} className={`transition-transform ${folderSectionOpen ? "rotate-180" : ""}`} />
              </button>
              {folderSectionOpen && (
                <div className="flex flex-col gap-3 border-t border-white/[0.08] p-4">
                  <p className="text-[11.5px] text-white/35">
                    Any file dropped into a watched folder is ingested automatically, and stays in sync as it
                    changes — enter a full folder path on this machine (e.g. C:\Users\you\Documents\notes).
                  </p>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={folderPathInput}
                      onChange={(e) => setFolderPathInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleWatchFolder();
                      }}
                      placeholder="C:\Users\you\Documents\notes"
                      disabled={addingFolder}
                      className={inputClass}
                    />
                    <button
                      type="button"
                      onClick={handleWatchFolder}
                      disabled={addingFolder || !folderPathInput.trim()}
                      className="flex shrink-0 items-center gap-2 rounded-xl border border-primary/30 bg-primary/10 px-4 py-2.5 text-[13px] text-primary transition-colors hover:bg-primary/15 disabled:opacity-40"
                    >
                      {addingFolder ? <Loader2 size={14} strokeWidth={1.8} className="animate-spin" /> : <FolderPlus size={14} strokeWidth={1.8} />}
                      Watch
                    </button>
                  </div>
                  {folders.length > 0 && (
                    <div className="flex flex-col gap-1.5">
                      {folders.map((folder) => (
                        <div
                          key={folder.id}
                          className="flex items-center gap-2 rounded-lg border border-white/[0.07] bg-white/[0.015] px-3 py-2"
                        >
                          <Folder size={13} strokeWidth={1.8} className="shrink-0 text-primary/60" />
                          <span className="min-w-0 flex-1 truncate text-[12px] text-white/70">{folder.path}</span>
                          <span className="shrink-0 text-[11px] text-white/35">
                            {folder.file_count} file{folder.file_count === 1 ? "" : "s"}
                          </span>
                          <button
                            type="button"
                            onClick={() => handleUnwatchFolder(folder)}
                            aria-label={`Stop watching ${folder.path}`}
                            className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-white/40 transition-colors hover:bg-warning/10 hover:text-warning"
                          >
                            <Trash2 size={13} strokeWidth={1.8} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {allTags.length > 0 && (
              <div className="mt-5 flex flex-wrap items-center gap-1.5">
                {allTags.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    onClick={() => setActiveTagFilter((t) => (t === tag ? null : tag))}
                    className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
                      activeTagFilter === tag
                        ? "border-primary/50 bg-primary/15 text-primary"
                        : "border-white/[0.1] bg-white/[0.02] text-white/50 hover:border-primary/30 hover:text-white/80"
                    }`}
                  >
                    {tag}
                  </button>
                ))}
              </div>
            )}

            <div className="thin-scroll mt-4 min-h-0 flex-1 space-y-2 overflow-y-auto">
              {loading ? (
                <p className="text-[13px] text-white/40">Loading…</p>
              ) : visibleDocuments.length === 0 ? (
                <p className="mx-auto mt-8 max-w-sm rounded-2xl bg-black/30 px-5 py-3 text-center text-[13px] text-white/45 backdrop-blur-md">
                  {documents.length === 0
                    ? "No documents yet — upload one above to get started."
                    : `No documents tagged "${activeTagFilter}".`}
                </p>
              ) : (
                visibleDocuments.map((doc) => (
                  <div key={doc.id} className="rounded-xl border border-white/[0.07] bg-white/[0.015] px-4 py-3">
                    <div className="flex items-center gap-3">
                      {isUrlSource(doc.filename) ? (
                        <LinkIcon size={16} strokeWidth={1.8} className="shrink-0 text-primary/70" />
                      ) : (
                        <FileText size={16} strokeWidth={1.8} className="shrink-0 text-primary/70" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13.5px] text-white/90">{doc.filename}</div>
                        <div className="text-[11.5px] text-white/40">
                          {doc.chunk_count} chunk{doc.chunk_count === 1 ? "" : "s"}
                        </div>
                      </div>
                      {doc.source_type === "folder" ? (
                        <span
                          title="Managed by a watched folder — remove the file or stop watching the folder to delete this"
                          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/25"
                        >
                          <Folder size={14} strokeWidth={1.8} />
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleDelete(doc.id)}
                          aria-label={`Delete ${doc.filename}`}
                          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/40 transition-colors hover:bg-warning/10 hover:text-warning"
                        >
                          <Trash2 size={14} strokeWidth={1.8} />
                        </button>
                      )}
                    </div>

                    <div className="mt-2 flex items-center gap-1.5 pl-[28px]">
                      {editingTagsId === doc.id ? (
                        <input
                          type="text"
                          autoFocus
                          value={tagEditValue}
                          onChange={(e) => setTagEditValue(e.target.value)}
                          onBlur={() => saveTags(doc.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") saveTags(doc.id);
                            if (e.key === "Escape") setEditingTagsId(null);
                          }}
                          placeholder="tags (comma separated)"
                          className="min-w-0 flex-1 rounded-lg border border-primary/30 bg-white/[0.03] px-2 py-1 text-[11px] text-white/80 outline-none"
                        />
                      ) : (
                        <>
                          {doc.tags.map((tag) => (
                            <span key={tag} className="rounded-full border border-white/[0.1] bg-white/[0.02] px-2 py-0.5 text-[10.5px] text-white/45">
                              {tag}
                            </span>
                          ))}
                          <button
                            type="button"
                            onClick={() => startEditingTags(doc)}
                            aria-label={`Edit tags for ${doc.filename}`}
                            className="grid h-5 w-5 shrink-0 place-items-center rounded text-white/25 transition-colors hover:text-white/60"
                          >
                            <Pencil size={10} strokeWidth={1.8} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
