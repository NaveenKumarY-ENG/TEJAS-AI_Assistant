import { useEffect, useRef, useState } from "react";
import {
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Database,
  Eye,
  File,
  FileText,
  Folder,
  FolderPlus,
  Image as ImageIcon,
  Layers,
  Link as LinkIcon,
  Loader2,
  NotebookPen,
  Pencil,
  Plus,
  RotateCw,
  Search,
  Sparkles,
  Square,
  SquarePen,
  Tag,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { WidgetCard } from "../ui/WidgetCard";
import { DocumentPreview } from "./DocumentPreview";
import { formatRelativeTime } from "../../utils/relativeTime";

interface DocumentItem {
  id: number;
  filename: string;
  chunk_count: number;
  created_at: string;
  tags: string[];
  source_type: "manual" | "folder";
  doc_type: string;
  structured_data: Record<string, string>;
  // False for anything without stored original bytes (notes, URLs, or a
  // document uploaded before this existed) — gates the preview panel's
  // "Download" button. Absent on the response from a fresh ingest.
  has_file?: boolean;
  // Only ever present on the response from a fresh ingest (upload/URL/note),
  // never on a document loaded from the list — the existing document this
  // one's content is byte-for-byte identical to, if any.
  duplicate_of?: { id: number; filename: string } | null;
}

interface SearchResult {
  filename: string;
  text: string;
  page: number | null;
}

interface WatchedFolder {
  id: number;
  path: string;
  file_count: number;
  include_pattern?: string;
  exclude_pattern?: string;
}

type Tab = "documents" | "notes" | "links" | "folders";

const ACCEPTED_EXTENSIONS = [".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"];

/** A URL-ingested document is stored with the URL itself as its "filename"
 *  (see memory/knowledge.py's ingest_url) — no separate source-type column,
 *  so this is just how the UI tells the two apart. */
function isUrlSource(filename: string): boolean {
  return filename.startsWith("http://") || filename.startsWith("https://");
}

function hasAcceptedExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/** A note is stored under its plain title as the "filename" (see
 *  memory/knowledge.py's ingest_note) — no dedicated source-type either, so
 *  "not a URL and has no recognized file extension" is the same kind of
 *  filename-shape inference isUrlSource already relies on. */
function isNoteSource(doc: DocumentItem): boolean {
  return !isUrlSource(doc.filename) && !hasAcceptedExtension(doc.filename);
}

function parseTags(input: string): string[] {
  return input
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

function documentKind(doc: DocumentItem): "folder" | "link" | "note" | "file" {
  if (doc.source_type === "folder") return "folder";
  if (isUrlSource(doc.filename)) return "link";
  if (isNoteSource(doc)) return "note";
  return "file";
}

function fileIconMeta(doc: DocumentItem): { icon: typeof FileText; iconClass: string; bgClass: string } {
  const kind = documentKind(doc);
  if (kind === "link") return { icon: LinkIcon, iconClass: "text-secondary", bgClass: "bg-secondary/10" };
  if (kind === "note") return { icon: NotebookPen, iconClass: "text-primary", bgClass: "bg-primary/10" };
  if (kind === "folder") return { icon: Folder, iconClass: "text-white/40", bgClass: "bg-white/[0.04]" };
  const lower = doc.filename.toLowerCase();
  if (lower.endsWith(".pdf")) return { icon: FileText, iconClass: "text-red-400", bgClass: "bg-red-500/10" };
  if (lower.endsWith(".docx")) return { icon: FileText, iconClass: "text-blue-400", bgClass: "bg-blue-500/10" };
  if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg"))
    return { icon: ImageIcon, iconClass: "text-emerald-400", bgClass: "bg-emerald-500/10" };
  return { icon: File, iconClass: "text-white/50", bgClass: "bg-white/[0.05]" };
}

function activityLabel(doc: DocumentItem): string {
  const kind = documentKind(doc);
  if (kind === "link") return "Link added";
  if (kind === "note") return "Note added";
  if (kind === "folder") return "Auto-ingested from a watched folder";
  return "Document uploaded";
}

const TABS: { id: Tab; label: string }[] = [
  { id: "documents", label: "Documents" },
  { id: "notes", label: "Notes" },
  { id: "links", label: "Web Links" },
  { id: "folders", label: "Watched Folders" },
];

const inputClass =
  "min-w-0 flex-1 rounded-xl border border-white/[0.08] bg-white/[0.02] px-3.5 py-2.5 text-[13px] text-white/90 placeholder:text-white/30 outline-none transition-colors focus:border-primary/40 disabled:opacity-50";

const pillButtonClass =
  "flex shrink-0 items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 text-[13px] text-white/70 transition-colors hover:border-primary/40 hover:text-white disabled:opacity-40";

/**
 * Knowledge Base page (sidebar → Knowledge Base) — upload documents, paste
 * URLs, jot notes, watch folders, tag/organize/search what's indexed, and
 * delete what you don't want anymore. Rendered by App.tsx inside the normal
 * sidebar+topbar shell (not an overlay), the same layout shape as the chat
 * view: main content column + a right-hand info panel built from the same
 * WidgetCard used by the chat view's StatusPanel/WeatherWidget/etc.
 *
 * "Documents" / "Notes" / "Web Links" are all the same underlying indexed-
 * document list from the backend (memory/knowledge.py stores them
 * identically) — the tabs are a client-side filter over one real dataset,
 * not three separate features. "Watched Folders" is a genuinely separate
 * resource with its own endpoints.
 */
export function KnowledgePanel({ ocrAvailable }: { ocrAvailable: boolean }) {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [duplicateWarning, setDuplicateWarning] = useState<{ filename: string; existingFilename: string } | null>(
    null,
  );
  const [reprocessingId, setReprocessingId] = useState<number | null>(null);

  const [activeTab, setActiveTab] = useState<Tab>("documents");
  const [addMenuOpen, setAddMenuOpen] = useState(false);

  const [uploading, setUploading] = useState(false);
  const [uploadTags, setUploadTags] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [urlInput, setUrlInput] = useState("");
  const [urlTags, setUrlTags] = useState("");
  const [ingestingUrl, setIngestingUrl] = useState(false);
  const urlInputRef = useRef<HTMLInputElement>(null);

  const [noteTitle, setNoteTitle] = useState("");
  const [noteText, setNoteText] = useState("");
  const [noteTags, setNoteTags] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  const noteTitleRef = useRef<HTMLInputElement>(null);

  const [activeTagFilter, setActiveTagFilter] = useState<string | null>(null);
  const [expandedDocId, setExpandedDocId] = useState<number | null>(null);
  const [editingTagsId, setEditingTagsId] = useState<number | null>(null);
  const [tagEditValue, setTagEditValue] = useState("");

  const [searchQuery, setSearchQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);

  const [folders, setFolders] = useState<WatchedFolder[]>([]);
  const [folderPathInput, setFolderPathInput] = useState("");
  const [folderIncludePattern, setFolderIncludePattern] = useState("");
  const [folderExcludePattern, setFolderExcludePattern] = useState("");
  const [addingFolder, setAddingFolder] = useState(false);

  const [previewDocId, setPreviewDocId] = useState<number | null>(null);

  const [editingNoteId, setEditingNoteId] = useState<number | null>(null);
  const [editNoteTitle, setEditNoteTitle] = useState("");
  const [editNoteText, setEditNoteText] = useState("");
  const [editNoteTags, setEditNoteTags] = useState("");
  const [savingNoteEdit, setSavingNoteEdit] = useState(false);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkTagInput, setBulkTagInput] = useState("");
  const [bulkActing, setBulkActing] = useState(false);

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
  // user leaving and returning to this page — a light poll rather than a
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
    if (!addMenuOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAddMenuOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [addMenuOpen]);

  // A selection made in one tab (a document's underlying id) has no
  // visible row to correspond to after switching tabs — clear it rather
  // than leave an invisible, stale selection a bulk action could act on.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [activeTab]);

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
      if (data.duplicate_of) {
        setDuplicateWarning({ filename: data.filename, existingFilename: data.duplicate_of.filename });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(false);
    if (uploading) return;
    handleFiles(e.dataTransfer.files);
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
      if (data.duplicate_of) {
        setDuplicateWarning({ filename: data.filename, existingFilename: data.duplicate_of.filename });
      }
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
      if (data.duplicate_of) {
        setDuplicateWarning({ filename: data.filename, existingFilename: data.duplicate_of.filename });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save note");
    } finally {
      setSavingNote(false);
    }
  };

  const startEditingNote = (doc: DocumentItem) => {
    setEditingNoteId(doc.id);
    setEditNoteTitle(doc.filename);
    setEditNoteText(""); // filled in just below — the list never carries a note's real body
    setEditNoteTags(doc.tags.join(", "));
    // The document list never carries raw_text (see structured.list_documents'
    // docstring) — fetch the note's real body just-in-time when editing starts.
    fetch(`/api/knowledge/${doc.id}`)
      .then((r) => r.json())
      .then((data) => setEditNoteText(data.raw_text ?? ""))
      .catch(() => setError("Failed to load the note's content."));
  };

  const cancelEditingNote = () => {
    setEditingNoteId(null);
    setEditNoteTitle("");
    setEditNoteText("");
    setEditNoteTags("");
  };

  const handleSaveNoteEdit = async () => {
    if (editingNoteId === null) return;
    const title = editNoteTitle.trim();
    const text = editNoteText.trim();
    if (!title || !text) return;

    setError(null);
    setSavingNoteEdit(true);
    try {
      const res = await fetch(`/api/knowledge/${editingNoteId}/note`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, text, tags: parseTags(editNoteTags) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to update note");
      setDocuments((docs) =>
        docs.map((d) =>
          d.id === editingNoteId
            ? { ...d, filename: data.filename, chunk_count: data.chunk_count, tags: data.tags, doc_type: data.doc_type, structured_data: data.structured_data }
            : d,
        ),
      );
      cancelEditingNote();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update note");
    } finally {
      setSavingNoteEdit(false);
    }
  };

  const handleDelete = async (doc: DocumentItem) => {
    if (!window.confirm(`Delete "${doc.filename}"? This can't be undone.`)) return;
    const previous = documents;
    setDocuments((docs) => docs.filter((d) => d.id !== doc.id)); // optimistic — restored below on failure
    try {
      const res = await fetch(`/api/knowledge/${doc.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Delete failed");
    } catch {
      setError("Failed to delete document.");
      setDocuments(previous);
    }
  };

  const handleReprocess = async (doc: DocumentItem) => {
    setError(null);
    setReprocessingId(doc.id);
    try {
      const res = await fetch(`/api/knowledge/${doc.id}/reprocess`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Reprocess failed");
      setDocuments((docs) =>
        docs.map((d) => (d.id === doc.id ? { ...d, doc_type: data.doc_type, structured_data: data.structured_data } : d)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reprocess failed");
    } finally {
      setReprocessingId(null);
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
        body: JSON.stringify({
          path,
          include_pattern: folderIncludePattern.trim(),
          exclude_pattern: folderExcludePattern.trim(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to watch folder");
      setFolders((f) => [{ ...data, file_count: 0 }, ...f]);
      setFolderPathInput("");
      setFolderIncludePattern("");
      setFolderExcludePattern("");
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

  const toggleSelected = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const clearSelection = () => setSelectedIds(new Set());

  const handleBulkDelete = async () => {
    const targets = documents.filter((d) => selectedIds.has(d.id) && d.source_type !== "folder");
    if (targets.length === 0) return;
    if (!window.confirm(`Delete ${targets.length} selected document${targets.length === 1 ? "" : "s"}? This can't be undone.`))
      return;

    setError(null);
    setBulkActing(true);
    try {
      const results = await Promise.allSettled(
        targets.map((d) => fetch(`/api/knowledge/${d.id}`, { method: "DELETE" })),
      );
      const deletedIds = new Set(
        targets.filter((_, i) => results[i].status === "fulfilled" && (results[i] as PromiseFulfilledResult<Response>).value.ok).map((d) => d.id),
      );
      setDocuments((docs) => docs.filter((d) => !deletedIds.has(d.id)));
      if (deletedIds.size < targets.length) setError("Some documents failed to delete — try again.");
    } finally {
      setBulkActing(false);
      clearSelection();
    }
  };

  const handleBulkAddTag = async () => {
    const tag = bulkTagInput.trim();
    if (!tag) return;
    const targets = documents.filter((d) => selectedIds.has(d.id));
    if (targets.length === 0) return;

    setError(null);
    setBulkActing(true);
    try {
      await Promise.allSettled(
        targets.map((d) => {
          const nextTags = d.tags.includes(tag) ? d.tags : [...d.tags, tag];
          return fetch(`/api/knowledge/${d.id}/tags`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tags: nextTags }),
          }).then((res) => (res.ok ? { id: d.id, tags: nextTags } : null));
        }),
      ).then((results) => {
        setDocuments((docs) =>
          docs.map((d) => {
            const applied = results.find(
              (r) => r.status === "fulfilled" && r.value && r.value.id === d.id,
            ) as PromiseFulfilledResult<{ id: number; tags: string[] } | null> | undefined;
            return applied?.value ? { ...d, tags: applied.value.tags } : d;
          }),
        );
      });
      setBulkTagInput("");
    } finally {
      setBulkActing(false);
    }
  };

  const goToTab = (tab: Tab) => {
    setActiveTab(tab);
    setAddMenuOpen(false);
    setSearchResults(null);
    if (tab === "documents") window.setTimeout(() => fileInputRef.current?.click(), 0);
    else if (tab === "links") window.setTimeout(() => urlInputRef.current?.focus(), 0);
    else if (tab === "notes") window.setTimeout(() => noteTitleRef.current?.focus(), 0);
  };

  const allTags = [...new Set(documents.flatMap((d) => d.tags))].sort();

  const tabDocuments =
    activeTab === "documents"
      ? documents.filter((d) => !isUrlSource(d.filename) && !isNoteSource(d))
      : activeTab === "notes"
        ? documents.filter((d) => isNoteSource(d))
        : activeTab === "links"
          ? documents.filter((d) => isUrlSource(d.filename))
          : [];
  const visibleDocuments = activeTagFilter ? tabDocuments.filter((d) => d.tags.includes(activeTagFilter)) : tabDocuments;

  const totalChunks = documents.reduce((sum, d) => sum + d.chunk_count, 0);
  const lastUpdated = documents.length > 0 ? formatRelativeTime(documents[0].created_at) : null;
  const recentActivity = documents.slice(0, 5);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)] gap-5 px-6 pb-6 lg:grid-cols-[1fr_300px]">
      <section className="thin-scroll min-h-0 overflow-y-auto rounded-2xl border border-white/[0.06] bg-[#050208] p-6">
        {/* Hero */}
        <div className="relative overflow-hidden rounded-2xl border border-primary/20 bg-gradient-to-br from-primary/15 via-secondary/10 to-transparent p-6">
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-primary/20 blur-[80px]"
            aria-hidden="true"
          />
          <span className="mono relative inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-[10.5px] tracking-wider text-primary">
            <Sparkles size={11} strokeWidth={1.8} />
            YOUR KNOWLEDGE, AMPLIFIED
          </span>
          <h2 className="relative mt-3 max-w-lg text-[21px] font-semibold leading-snug text-white">
            Turn your documents into intelligence
          </h2>
          <p className="relative mt-1.5 max-w-lg text-[13px] leading-relaxed text-white/50">
            Upload notes, PDFs, docs, or links{ocrAvailable ? " — including scanned images" : ""}. TEJAS reads them
            and answers questions using what's inside.
          </p>

          {/* Real, existing capabilities — not per-document data, so safe as static copy. */}
          <div className="relative mt-5 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            {[
              { icon: FileText, label: "Multiple Formats", detail: ACCEPTED_EXTENSIONS.join(" ") },
              { icon: Search, label: "Semantic Search", detail: "Find answers, not just keywords" },
              { icon: Sparkles, label: "AI-Powered Insights", detail: "Extracted fields, auto-tagged type" },
              { icon: Database, label: "Local & Private", detail: "Stored on this machine" },
            ].map((f) => (
              <div key={f.label} className="rounded-xl border border-white/[0.08] bg-black/20 p-2.5">
                <f.icon size={14} strokeWidth={1.8} className="text-primary/80" />
                <p className="mt-1.5 text-[11.5px] font-medium text-white/80">{f.label}</p>
                <p className="truncate text-[10.5px] text-white/35" title={f.detail}>
                  {f.detail}
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* Search + Add New */}
        <div className="mt-5 flex items-center gap-2">
          <div className="relative flex-1">
            <button
              type="button"
              onClick={handleSearch}
              disabled={searching}
              aria-label="Search"
              className="absolute left-3.5 top-1/2 -translate-y-1/2 text-white/30 transition-colors hover:text-primary disabled:hover:text-white/30"
            >
              {searching ? <Loader2 size={14} strokeWidth={1.8} className="animate-spin" /> : <Search size={14} strokeWidth={1.8} />}
            </button>
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
          {searchResults !== null && (
            <button type="button" onClick={clearSearch} className={pillButtonClass}>
              Clear
            </button>
          )}
          <div className="relative z-20 shrink-0">
            <button
              type="button"
              onClick={() => setAddMenuOpen((o) => !o)}
              className="relative z-20 flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-primary to-secondary px-4 py-2.5 text-[13px] font-medium text-white shadow-[0_0_20px_-6px_color-mix(in_srgb,var(--color-primary)_60%,transparent)] transition-opacity hover:opacity-90"
            >
              <Plus size={14} strokeWidth={2} />
              Add New
              <ChevronDown size={13} strokeWidth={1.8} className={`transition-transform ${addMenuOpen ? "rotate-180" : ""}`} />
            </button>
            {addMenuOpen && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setAddMenuOpen(false)} />
                <div className="absolute right-0 z-20 mt-2 w-48 overflow-hidden rounded-xl border border-white/[0.1] bg-[#0a0510] shadow-[0_12px_32px_-8px_rgba(0,0,0,0.6)]">
                  <button
                    type="button"
                    onClick={() => goToTab("documents")}
                    className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-[12.5px] text-white/75 transition-colors hover:bg-primary/10 hover:text-white"
                  >
                    <Upload size={14} strokeWidth={1.8} className="text-primary/70" />
                    Upload Document
                  </button>
                  <button
                    type="button"
                    onClick={() => goToTab("notes")}
                    className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-[12.5px] text-white/75 transition-colors hover:bg-primary/10 hover:text-white"
                  >
                    <NotebookPen size={14} strokeWidth={1.8} className="text-primary/70" />
                    Add a Note
                  </button>
                  <button
                    type="button"
                    onClick={() => goToTab("links")}
                    className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-[12.5px] text-white/75 transition-colors hover:bg-primary/10 hover:text-white"
                  >
                    <LinkIcon size={14} strokeWidth={1.8} className="text-primary/70" />
                    Add a Link
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        {error && (
          <p className="mt-3 flex items-center gap-1.5 text-[12.5px] text-warning">
            <X size={12} strokeWidth={2} />
            {error}
          </p>
        )}

        {/* Non-blocking — duplicate detection never stops the upload, it
            just tells the user what happened. See memory/knowledge.py's
            _index_text docstring for why this deliberately isn't a hard
            rejection. */}
        {duplicateWarning && (
          <div className="mt-3 flex items-center justify-between gap-2 rounded-xl border border-warning/25 bg-warning/[0.06] px-3.5 py-2.5 text-[12.5px] text-warning/90">
            <span>
              "{duplicateWarning.filename}" looks identical to an existing document, "
              {duplicateWarning.existingFilename}" — both were kept.
            </span>
            <button
              type="button"
              onClick={() => setDuplicateWarning(null)}
              aria-label="Dismiss duplicate warning"
              className="shrink-0 text-warning/60 transition-colors hover:text-warning"
            >
              <X size={13} strokeWidth={2} />
            </button>
          </div>
        )}

        {searchResults !== null ? (
          <div className="mt-6 space-y-2">
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
                    {r.page != null && <span className="shrink-0 text-white/30">· page {r.page}</span>}
                  </div>
                  <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-white/70">{r.text}</p>
                </div>
              ))
            )}
          </div>
        ) : (
          <>
            {/* Tabs */}
            <div className="mt-6 flex items-center gap-1.5 border-b border-white/[0.08]">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={`relative px-3.5 py-2.5 text-[13px] transition-colors ${
                    activeTab === tab.id ? "text-white" : "text-white/45 hover:text-white/75"
                  }`}
                >
                  {tab.label}
                  {tab.id === "folders" && folders.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-white/10 px-1.5 py-0.5 text-[10px] text-white/50">
                      {folders.length}
                    </span>
                  )}
                  {activeTab === tab.id && (
                    <span className="absolute inset-x-0 -bottom-px h-[2px] rounded-full bg-gradient-to-r from-primary to-secondary" />
                  )}
                </button>
              ))}
            </div>

            {activeTab === "documents" && (
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragActive(true);
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={handleDrop}
                className={`mt-4 flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed p-6 text-center transition-colors ${
                  dragActive ? "border-primary/60 bg-primary/[0.06]" : "border-white/[0.12] bg-white/[0.02]"
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPTED_EXTENSIONS.join(",")}
                  className="hidden"
                  onChange={(e) => handleFiles(e.target.files)}
                />
                <span className="grid h-11 w-11 place-items-center rounded-full border border-primary/25 bg-primary/10 text-primary">
                  {uploading ? <Loader2 size={18} strokeWidth={1.8} className="animate-spin" /> : <Upload size={18} strokeWidth={1.8} />}
                </span>
                <p className="text-[13px] text-white/70">
                  {uploading ? (
                    "Uploading…"
                  ) : (
                    <>
                      Drag and drop files here, or{" "}
                      <button type="button" onClick={() => fileInputRef.current?.click()} className="text-primary hover:underline">
                        click to upload
                      </button>
                    </>
                  )}
                </p>
                <p className="text-[11.5px] text-white/30">Supports: {ACCEPTED_EXTENSIONS.join(", ")}</p>
                <div className="flex items-center gap-2.5">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading}
                    className="rounded-lg bg-gradient-to-r from-primary to-secondary px-3.5 py-1.5 text-[12px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    Choose Files
                  </button>
                  <button
                    type="button"
                    onClick={() => goToTab("links")}
                    className="rounded-lg border border-white/[0.1] bg-white/[0.02] px-3.5 py-1.5 text-[12px] text-white/60 transition-colors hover:border-primary/30 hover:text-white"
                  >
                    Add from URL
                  </button>
                </div>
                <div className="flex w-full max-w-xs items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.02] px-2.5 py-1.5">
                  <Tag size={12} strokeWidth={1.8} className="shrink-0 text-white/25" />
                  <input
                    type="text"
                    value={uploadTags}
                    onChange={(e) => setUploadTags(e.target.value)}
                    list="kb-tag-suggestions"
                    placeholder="tags (comma separated, optional)"
                    className="min-w-0 flex-1 bg-transparent text-[12px] text-white/70 placeholder:text-white/25 outline-none"
                  />
                </div>
                {!ocrAvailable && (
                  <p className="text-[11px] text-white/30">
                    OCR isn't installed on this server — image/scanned-PDF uploads will fail. See README.
                  </p>
                )}
              </div>
            )}

            {activeTab === "notes" && (
              <div className="mt-4 flex flex-col gap-2 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4">
                <input
                  ref={noteTitleRef}
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
                    list="kb-tag-suggestions"
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

            {activeTab === "links" && (
              <div className="mt-4 flex flex-col gap-2 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4">
                <div className="flex items-center gap-2">
                  <input
                    ref={urlInputRef}
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
                    className={pillButtonClass}
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
                    list="kb-tag-suggestions"
                    placeholder="tags (comma separated, optional)"
                    className="min-w-0 flex-1 bg-transparent text-[12px] text-white/70 placeholder:text-white/25 outline-none"
                  />
                </div>
              </div>
            )}

            {activeTab === "folders" && (
              <div className="mt-4 flex flex-col gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4">
                <p className="text-[11.5px] text-white/35">
                  Any file dropped into a watched folder is ingested automatically, and stays in sync as it changes —
                  enter a full folder path on this machine (e.g. C:\Users\you\Documents\notes).
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
                {/* Optional per-folder filtering — without this, watching a
                    broad folder recursively pulls in every file matching a
                    supported extension with no way to narrow it down. */}
                <details className="text-[11.5px] text-white/40">
                  <summary className="cursor-pointer select-none hover:text-white/60">Filter which files get watched (optional)</summary>
                  <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                    <input
                      type="text"
                      value={folderIncludePattern}
                      onChange={(e) => setFolderIncludePattern(e.target.value)}
                      placeholder="Only include, e.g. *.pdf,*.docx"
                      disabled={addingFolder}
                      className={inputClass}
                    />
                    <input
                      type="text"
                      value={folderExcludePattern}
                      onChange={(e) => setFolderExcludePattern(e.target.value)}
                      placeholder="Exclude, e.g. archive/*,*.tmp"
                      disabled={addingFolder}
                      className={inputClass}
                    />
                  </div>
                </details>
                {folders.length === 0 ? (
                  <p className="mx-auto mt-4 max-w-sm rounded-2xl bg-black/30 px-5 py-3 text-center text-[13px] text-white/45">
                    No watched folders yet.
                  </p>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {folders.map((folder) => (
                      <div
                        key={folder.id}
                        className="flex items-center gap-2 rounded-lg border border-white/[0.07] bg-white/[0.015] px-3 py-2"
                      >
                        <Folder size={13} strokeWidth={1.8} className="shrink-0 text-primary/60" />
                        <div className="min-w-0 flex-1">
                          <span className="block truncate text-[12px] text-white/70">{folder.path}</span>
                          {(folder.include_pattern || folder.exclude_pattern) && (
                            <span className="mono block truncate text-[10px] text-white/30">
                              {folder.include_pattern && `include: ${folder.include_pattern}`}
                              {folder.include_pattern && folder.exclude_pattern && " · "}
                              {folder.exclude_pattern && `exclude: ${folder.exclude_pattern}`}
                            </span>
                          )}
                        </div>
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

            {activeTab !== "folders" && (
              <>
                {allTags.length > 0 && (
                  <div className="mt-4 flex flex-wrap items-center gap-1.5">
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

                <div className="mt-4 flex items-center justify-between">
                  <h3 className="text-[13px] font-medium text-white/60">
                    {TABS.find((t) => t.id === activeTab)?.label}
                    {!loading && <span className="ml-1.5 text-white/30">({visibleDocuments.length})</span>}
                  </h3>
                  {visibleDocuments.length > 0 && (
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedIds((prev) =>
                          prev.size === visibleDocuments.length ? new Set() : new Set(visibleDocuments.map((d) => d.id)),
                        )
                      }
                      className="text-[11.5px] text-white/35 transition-colors hover:text-white/70"
                    >
                      {selectedIds.size === visibleDocuments.length ? "Deselect all" : "Select all"}
                    </button>
                  )}
                </div>

                {selectedIds.size > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-primary/25 bg-primary/[0.05] px-3.5 py-2.5">
                    <span className="text-[12px] font-medium text-primary">
                      {selectedIds.size} selected
                    </span>
                    <input
                      type="text"
                      list="kb-tag-suggestions"
                      value={bulkTagInput}
                      onChange={(e) => setBulkTagInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleBulkAddTag();
                      }}
                      placeholder="Add tag to selected…"
                      disabled={bulkActing}
                      className="min-w-0 flex-1 rounded-lg border border-white/[0.1] bg-white/[0.03] px-2.5 py-1.5 text-[12px] text-white/85 placeholder:text-white/30 outline-none focus:border-primary/40"
                    />
                    <button
                      type="button"
                      onClick={handleBulkAddTag}
                      disabled={bulkActing || !bulkTagInput.trim()}
                      className="rounded-lg border border-primary/30 bg-primary/10 px-3 py-1.5 text-[11.5px] text-primary transition-colors hover:bg-primary/15 disabled:opacity-40"
                    >
                      Tag
                    </button>
                    <button
                      type="button"
                      onClick={handleBulkDelete}
                      disabled={bulkActing}
                      className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-1.5 text-[11.5px] text-warning transition-colors hover:bg-warning/15 disabled:opacity-40"
                    >
                      Delete
                    </button>
                    <button
                      type="button"
                      onClick={clearSelection}
                      disabled={bulkActing}
                      className="text-[11.5px] text-white/40 transition-colors hover:text-white/70"
                    >
                      Clear
                    </button>
                  </div>
                )}

                <div className="mt-2 space-y-2">
                  {loading ? (
                    <div className="space-y-2">
                      {[0, 1, 2].map((i) => (
                        <div key={i} className="h-[58px] animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.015]" />
                      ))}
                    </div>
                  ) : visibleDocuments.length === 0 ? (
                    <div className="mx-auto mt-6 flex max-w-sm flex-col items-center gap-3 rounded-2xl bg-black/30 px-5 py-6 text-center backdrop-blur-md">
                      <p className="text-[13px] text-white/45">
                        {documents.length === 0 || tabDocuments.length === 0
                          ? activeTab === "notes"
                            ? "No notes yet — add one above."
                            : activeTab === "links"
                              ? "No links yet — add one above."
                              : "No documents yet — upload one above to get started."
                          : `No results tagged "${activeTagFilter}".`}
                      </p>
                    </div>
                  ) : (
                    visibleDocuments.map((doc) => {
                      const hasStructuredData = Object.keys(doc.structured_data).length > 0;
                      const meta = fileIconMeta(doc);
                      const kind = documentKind(doc);

                      if (editingNoteId === doc.id) {
                        return (
                          <div key={doc.id} className="flex flex-col gap-2 rounded-xl border border-primary/30 bg-primary/[0.04] px-4 py-3">
                            <input
                              type="text"
                              autoFocus
                              value={editNoteTitle}
                              onChange={(e) => setEditNoteTitle(e.target.value)}
                              placeholder="Title"
                              className={inputClass}
                            />
                            <textarea
                              value={editNoteText}
                              onChange={(e) => setEditNoteText(e.target.value)}
                              placeholder="Note text…"
                              rows={4}
                              className={`${inputClass} resize-none`}
                            />
                            <input
                              type="text"
                              list="kb-tag-suggestions"
                              value={editNoteTags}
                              onChange={(e) => setEditNoteTags(e.target.value)}
                              placeholder="tags (comma separated, optional)"
                              className={inputClass}
                            />
                            <div className="flex items-center justify-end gap-2">
                              <button
                                type="button"
                                onClick={cancelEditingNote}
                                disabled={savingNoteEdit}
                                className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-[12px] text-white/60 transition-colors hover:text-white/90"
                              >
                                Cancel
                              </button>
                              <button
                                type="button"
                                onClick={handleSaveNoteEdit}
                                disabled={savingNoteEdit || !editNoteTitle.trim() || !editNoteText.trim()}
                                className="flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/10 px-3 py-1.5 text-[12px] text-primary transition-colors hover:bg-primary/15 disabled:opacity-40"
                              >
                                {savingNoteEdit && <Loader2 size={12} strokeWidth={1.8} className="animate-spin" />}
                                Save
                              </button>
                            </div>
                          </div>
                        );
                      }

                      return (
                        <div key={doc.id} className="rounded-xl border border-white/[0.07] bg-white/[0.015] px-4 py-3 transition-colors hover:border-white/[0.14]">
                          <div className="flex items-center gap-3">
                            <button
                              type="button"
                              onClick={() => toggleSelected(doc.id)}
                              aria-label={selectedIds.has(doc.id) ? `Deselect ${doc.filename}` : `Select ${doc.filename}`}
                              className="shrink-0 text-white/25 transition-colors hover:text-white/60"
                            >
                              {selectedIds.has(doc.id) ? (
                                <CheckSquare size={16} strokeWidth={1.8} className="text-primary" />
                              ) : (
                                <Square size={16} strokeWidth={1.8} />
                              )}
                            </button>
                            {hasStructuredData ? (
                              <button
                                type="button"
                                onClick={() => setExpandedDocId((id) => (id === doc.id ? null : doc.id))}
                                aria-label={`${expandedDocId === doc.id ? "Collapse" : "Expand"} extracted fields for ${doc.filename}`}
                                className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${meta.bgClass} ${meta.iconClass} transition-colors`}
                              >
                                {expandedDocId === doc.id ? (
                                  <ChevronDown size={15} strokeWidth={1.8} />
                                ) : (
                                  <ChevronRight size={15} strokeWidth={1.8} />
                                )}
                              </button>
                            ) : (
                              <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${meta.bgClass} ${meta.iconClass}`}>
                                <meta.icon size={15} strokeWidth={1.8} />
                              </span>
                            )}
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="truncate text-[13.5px] text-white/90">{doc.filename}</span>
                                {doc.doc_type && (
                                  <span className="shrink-0 rounded-full border border-primary/25 bg-primary/10 px-2 py-0.5 text-[10px] text-primary/80">
                                    {doc.doc_type}
                                  </span>
                                )}
                              </div>
                              <div className="flex items-center gap-1.5 text-[11.5px] text-white/40">
                                <Layers size={10} strokeWidth={1.8} />
                                {doc.chunk_count} chunk{doc.chunk_count === 1 ? "" : "s"}
                                <span className="text-white/20">•</span>
                                {formatRelativeTime(doc.created_at) || "just now"}
                              </div>
                            </div>
                            <span className="hidden shrink-0 items-center gap-1 text-[11px] text-success sm:flex">
                              <span className="h-1.5 w-1.5 rounded-full bg-success shadow-[0_0_6px_rgba(0,255,200,0.8)]" />
                              Ready
                            </span>
                            <button
                              type="button"
                              onClick={() => setPreviewDocId(doc.id)}
                              aria-label={`Preview ${doc.filename}`}
                              title="Read the full extracted content, or summarize it"
                              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/40 transition-colors hover:bg-primary/10 hover:text-primary"
                            >
                              <Eye size={14} strokeWidth={1.8} />
                            </button>
                            {kind === "note" && (
                              <button
                                type="button"
                                onClick={() => startEditingNote(doc)}
                                aria-label={`Edit ${doc.filename}`}
                                title="Edit this note"
                                className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/40 transition-colors hover:bg-primary/10 hover:text-primary"
                              >
                                <SquarePen size={14} strokeWidth={1.8} />
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => handleReprocess(doc)}
                              disabled={reprocessingId === doc.id}
                              aria-label={`Re-extract fields for ${doc.filename}`}
                              title="Re-run field extraction from this document's stored text"
                              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/40 transition-colors hover:bg-primary/10 hover:text-primary disabled:opacity-50"
                            >
                              {reprocessingId === doc.id ? (
                                <Loader2 size={14} strokeWidth={1.8} className="animate-spin" />
                              ) : (
                                <RotateCw size={14} strokeWidth={1.8} />
                              )}
                            </button>
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
                                onClick={() => handleDelete(doc)}
                                aria-label={`Delete ${doc.filename}`}
                                className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/40 transition-colors hover:bg-warning/10 hover:text-warning"
                              >
                                <Trash2 size={14} strokeWidth={1.8} />
                              </button>
                            )}
                          </div>

                          <div className="mt-2 flex items-center gap-1.5 pl-11">
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
                                list="kb-tag-suggestions"
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

                          {hasStructuredData && expandedDocId === doc.id && (
                            <div className="mt-3 overflow-hidden rounded-lg border border-white/[0.08]">
                              <table className="w-full text-[12px]">
                                <tbody>
                                  {Object.entries(doc.structured_data).map(([field, value]) => (
                                    <tr key={field} className="border-b border-white/[0.06] last:border-b-0">
                                      <td className="whitespace-nowrap bg-white/[0.02] px-3 py-1.5 align-top text-white/45">{field}</td>
                                      <td className="px-3 py-1.5 text-white/85">{String(value)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              </>
            )}
          </>
        )}
      </section>

      <aside className="thin-scroll hidden min-h-0 flex-col gap-3.5 overflow-y-auto pt-1 lg:flex">
        <WidgetCard title="Knowledge Stats">
          <dl className="space-y-2">
            <div className="flex items-center justify-between">
              <dt className="flex items-center gap-1.5 text-[12px] text-white/50">
                <FileText size={12} strokeWidth={1.8} />
                Total Documents
              </dt>
              <dd className="mono text-[13px] text-white/85">{documents.length}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="flex items-center gap-1.5 text-[12px] text-white/50">
                <Layers size={12} strokeWidth={1.8} />
                Total Chunks
              </dt>
              <dd className="mono text-[13px] text-white/85">{totalChunks}</dd>
            </div>
            {folders.length > 0 && (
              <div className="flex items-center justify-between">
                <dt className="flex items-center gap-1.5 text-[12px] text-white/50">
                  <Folder size={12} strokeWidth={1.8} />
                  Watched Folders
                </dt>
                <dd className="mono text-[13px] text-white/85">{folders.length}</dd>
              </div>
            )}
            {lastUpdated && (
              <div className="flex items-center justify-between">
                <dt className="flex items-center gap-1.5 text-[12px] text-white/50">
                  <Database size={12} strokeWidth={1.8} />
                  Last Updated
                </dt>
                <dd className="text-[12px] text-white/70">{lastUpdated}</dd>
              </div>
            )}
          </dl>
        </WidgetCard>

        <WidgetCard title="Supported Formats">
          <div className="flex flex-wrap gap-1.5">
            {ACCEPTED_EXTENSIONS.map((ext) => (
              <span key={ext} className="mono rounded-lg border border-white/[0.08] bg-white/[0.02] px-2 py-1 text-[11px] text-white/60">
                {ext}
              </span>
            ))}
          </div>
        </WidgetCard>

        <WidgetCard title="Recent Activity">
          {recentActivity.length === 0 ? (
            <p className="text-[12.5px] text-white/40">Nothing yet.</p>
          ) : (
            <ul className="space-y-2.5">
              {recentActivity.map((doc) => {
                const meta = fileIconMeta(doc);
                return (
                  <li key={doc.id} className="flex items-start gap-2.5">
                    <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md ${meta.bgClass} ${meta.iconClass}`}>
                      <meta.icon size={12} strokeWidth={1.8} />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-[12px] text-white/75">{doc.filename}</p>
                      <p className="text-[11px] text-white/35">
                        {activityLabel(doc)} · {formatRelativeTime(doc.created_at) || "just now"}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </WidgetCard>
      </aside>

      {/* Shared by every tag input in this page (upload/note/url/edit) —
          native <datalist> autocomplete against tags that already exist,
          so "AI" / "ai" / "A.I." don't silently fragment into separate
          filter pills from typos alone. */}
      <datalist id="kb-tag-suggestions">
        {allTags.map((tag) => (
          <option key={tag} value={tag} />
        ))}
      </datalist>

      {previewDocId !== null && (
        <DocumentPreview documentId={previewDocId} onClose={() => setPreviewDocId(null)} />
      )}
    </div>
  );
}
