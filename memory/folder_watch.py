"""Folder auto-watch: point the knowledge base at a folder on disk and it
stays in sync automatically — new files get ingested, changed files
re-ingested, deleted files removed, both on startup (a reconciliation scan,
catching anything that changed while the server was off) and live (via
watchdog filesystem events).

Reuses memory/knowledge.py's ingest_document/delete_document — a
folder-sourced document goes through the exact same
extract/chunk/embed pipeline (OCR included) as a manual upload, just
triggered by a file appearing on disk instead of a browser upload.
"""
import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from memory import knowledge, structured

logger = logging.getLogger("assistant.folder_watch")

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"}

# How long to wait after the last filesystem event for a given path before
# actually ingesting it — editors/OS copy operations often fire several
# events in quick succession (create, then multiple writes) for one logical
# save, and ingesting mid-write would embed a truncated file.
_DEBOUNCE_SECONDS = 1.5

_observers: dict[int, Observer] = {}  # folder_id -> running Observer
_debounce_timers: dict[str, threading.Timer] = {}
_debounce_lock = threading.Lock()


def _ingest_path(folder_id: int, filepath: Path) -> None:
    try:
        data = filepath.read_bytes()
    except OSError:
        return  # file vanished between the event firing and us reading it
    if not data:
        return

    existing = structured.get_watched_file(str(filepath))
    if existing and existing["document_id"] is not None:
        knowledge.delete_document(existing["document_id"])

    try:
        doc = knowledge.ingest_document(filepath.name, data, source_type="folder")
    except (knowledge.UnsupportedFileType, knowledge.OCRUnavailable, ValueError) as e:
        logger.warning("Skipping %s: %s", filepath, e)
        return
    structured.upsert_watched_file(folder_id, str(filepath), doc["id"], filepath.stat().st_mtime)


def _remove_path(filepath: Path) -> None:
    row = structured.delete_watched_file(str(filepath))
    if row and row["document_id"] is not None:
        knowledge.delete_document(row["document_id"])


def _scan_folder(folder_id: int, path: Path) -> None:
    """Reconcile a folder's real contents against what's tracked in SQLite —
    used for both the initial scan when a folder is first watched and the
    startup reconciliation for folders that already existed."""
    on_disk = {
        p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    }
    tracked = {Path(f["filepath"]): f for f in structured.list_watched_files(folder_id)}

    for filepath in on_disk:
        existing = tracked.get(filepath)
        if existing is None:
            _ingest_path(folder_id, filepath)
        elif existing["mtime"] != filepath.stat().st_mtime:
            _ingest_path(folder_id, filepath)

    for filepath in tracked:
        if filepath not in on_disk:
            _remove_path(filepath)


class _Handler(FileSystemEventHandler):
    def __init__(self, folder_id: int):
        self.folder_id = folder_id

    def _debounced(self, filepath: Path, action) -> None:
        key = str(filepath)
        with _debounce_lock:
            existing = _debounce_timers.get(key)
            if existing:
                existing.cancel()
            timer = threading.Timer(_DEBOUNCE_SECONDS, action)
            timer.daemon = True
            _debounce_timers[key] = timer
            timer.start()

    def _is_supported(self, path: str) -> bool:
        return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

    def on_created(self, event):
        if event.is_directory or not self._is_supported(event.src_path):
            return
        filepath = Path(event.src_path)
        self._debounced(filepath, lambda: _ingest_path(self.folder_id, filepath))

    def on_modified(self, event):
        if event.is_directory or not self._is_supported(event.src_path):
            return
        filepath = Path(event.src_path)
        self._debounced(filepath, lambda: _ingest_path(self.folder_id, filepath))

    def on_deleted(self, event):
        if event.is_directory or not self._is_supported(event.src_path):
            return
        filepath = Path(event.src_path)
        self._debounced(filepath, lambda: _remove_path(filepath))

    def on_moved(self, event):
        if not event.is_directory and self._is_supported(event.src_path):
            src = Path(event.src_path)
            self._debounced(src, lambda: _remove_path(src))
        if not event.is_directory and self._is_supported(event.dest_path):
            dest = Path(event.dest_path)
            self._debounced(dest, lambda: _ingest_path(self.folder_id, dest))


def _start_observer(folder_id: int, path: Path) -> None:
    observer = Observer()
    observer.daemon = True
    observer.schedule(_Handler(folder_id), str(path), recursive=True)
    observer.start()
    _observers[folder_id] = observer


def add_folder(path: str) -> dict:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"Not a folder on this machine: {path}")

    existing = next((f for f in structured.list_watched_folders() if f["path"] == str(resolved)), None)
    if existing:
        raise ValueError(f"Already watching {resolved}")

    folder_id = structured.add_watched_folder(str(resolved))
    threading.Thread(target=_scan_folder, args=(folder_id, resolved), daemon=True).start()
    _start_observer(folder_id, resolved)
    return {"id": folder_id, "path": str(resolved)}


def remove_folder(folder_id: int) -> bool:
    folder = structured.get_watched_folder(folder_id)
    observer = _observers.pop(folder_id, None)
    if observer:
        observer.stop()

    # Stopping the observer doesn't cancel timers already scheduled by
    # _Handler's debounce — without this, a file changed just before removal
    # could still trigger a stray re-ingest ~_DEBOUNCE_SECONDS later, after
    # the folder (and its documents) are supposed to be gone.
    if folder:
        with _debounce_lock:
            for key, timer in list(_debounce_timers.items()):
                if key.startswith(folder["path"]):
                    timer.cancel()
                    del _debounce_timers[key]

    for f in structured.list_watched_files(folder_id):
        if f["document_id"] is not None:
            knowledge.delete_document(f["document_id"])

    return structured.delete_watched_folder(folder_id)


def list_folders() -> list[dict]:
    return [
        {**f, "file_count": len(structured.list_watched_files(f["id"]))}
        for f in structured.list_watched_folders()
    ]


def start_all() -> None:
    """Reload every watched folder from SQLite, reconcile it against disk
    (catches anything that changed while the server was off), then attach a
    live watcher. Call once at server startup, in a background thread — see
    server.py's _start_folder_watchers, same pattern as tts.warm_up."""
    for folder in structured.list_watched_folders():
        path = Path(folder["path"])
        if not path.is_dir():
            logger.warning("Watched folder no longer exists, skipping: %s", path)
            continue
        _scan_folder(folder["id"], path)
        _start_observer(folder["id"], path)
