"""
Structured memory: SQLite-backed storage for discrete facts, reminders,
conversation sessions, and message history - things you want to query
exactly, not semantically.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import config


def format_due(due_at: str | None) -> str:
    """Human-readable date/time — e.g. "Wednesday, September 05, 2026 at
    10:00 AM" — for both tools/memory_tool.py's chat replies AND
    reminder_listing()'s ambient per-turn context below. Centralized here
    (rather than left to the model to compute a weekday from a raw ISO
    date) after a confirmed-live bug: given only raw ISO dates, a 7B local
    model tried to work out weekday names itself and got them wrong,
    including contradicting itself across two consecutive calendar dates in
    the same reply. Falls back to the raw string for anything not a clean
    ISO datetime (e.g. a legacy free-text due_at from before this existed,
    like "tomorrow"), since that's still meaningful to show as-is.
    Windows-safe: strftime's %-d/%-e no-leading-zero directives aren't
    portable, so this just accepts the zero-padded day (e.g. "September 05")
    rather than reaching for a platform-specific workaround."""
    if not due_at:
        return ""
    try:
        dt = datetime.fromisoformat(due_at)
    except ValueError:
        return due_at
    return dt.strftime("%A, %B %d, %Y at %I:%M %p")


@contextmanager
def _connect():
    conn = sqlite3.connect(config.sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                due_at TEXT,
                done INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )"""
        )
        # Added for real Google Calendar reminders (integrations/google_calendar.py)
        # — links a reminder to the Calendar event carrying its actual popup/email
        # notification, so completing the reminder can also delete that event.
        # NULL for a reminder saved before Calendar was configured/available.
        reminder_columns = {row["name"] for row in conn.execute("PRAGMA table_info(reminders)")}
        if "calendar_event_id" not in reminder_columns:
            conn.execute("ALTER TABLE reminders ADD COLUMN calendar_event_id TEXT")
        # "none"/"daily"/"weekly"/"monthly" — mirrors the Calendar event's own
        # RRULE (see integrations/google_calendar.py's _build_rrule), kept
        # here too so the UI/API can show a reminder is recurring without a
        # round trip to Google.
        if "recurrence" not in reminder_columns:
            conn.execute("ALTER TABLE reminders ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                name TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )"""
        )
        # Speeds up loading a session's history as the message table grows.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        # Added after documents already shipped (Phase 3) — CREATE TABLE IF
        # NOT EXISTS above is a no-op against an existing table, so a real
        # migration step is needed for anyone with documents predating tags.
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
        if "tags" not in existing_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
        # Added in Phase 4 (folder auto-watch) — 'manual' (upload/URL/note) or
        # 'folder' (ingested by memory/folder_watch.py). Folder-sourced
        # documents can't be deleted directly via the UI/API (see server.py's
        # DELETE /api/knowledge/{id}) since the watcher only re-ingests on a
        # detected file *change*, not on "the document vanished from SQLite" —
        # deleting a folder doc's file, or unwatching the folder, is the way
        # to actually remove it.
        if "source_type" not in existing_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual'")
        # Added for structured document extraction (memory/extraction.py) —
        # NOT the same "structured" as this module's own name (that predates
        # this and refers to SQLite-backed facts/reminders/sessions); this is
        # per-document extracted key-value fields, e.g. an ID card's Name/DOB/
        # ID number. JSON-encoded, same convention as tags.
        if "structured_data" not in existing_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN structured_data TEXT NOT NULL DEFAULT '{}'")
        if "doc_type" not in existing_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN doc_type TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS watched_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS watched_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                document_id INTEGER,
                mtime REAL NOT NULL,
                FOREIGN KEY (folder_id) REFERENCES watched_folders(id),
                FOREIGN KEY (document_id) REFERENCES documents(id)
            )"""
        )


# ----------------------------------------------------------------------
# Facts
# ----------------------------------------------------------------------

def save_fact(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO facts (key, value, created_at) VALUES (?, ?, ?)",
            (key, value, datetime.utcnow().isoformat()),
        )


def get_facts(key: str | None = None) -> list[dict]:
    with _connect() as conn:
        if key:
            rows = conn.execute(
                "SELECT * FROM facts WHERE key = ? ORDER BY id DESC", (key,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM facts ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# Reminders
# ----------------------------------------------------------------------

def add_reminder(
    text: str, due_at: str | None = None, calendar_event_id: str | None = None, recurrence: str = "none"
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (text, due_at, calendar_event_id, recurrence, created_at) VALUES (?, ?, ?, ?, ?)",
            (text, due_at, calendar_event_id, recurrence, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_reminder(reminder_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return dict(row) if row else None


def update_reminder(reminder_id: int, text: str | None = None, due_at: str | None = None) -> bool:
    """Partial update — only overwrites fields actually given. Does not
    touch calendar_event_id/recurrence (a rescheduled reminder keeps the
    same Calendar event and recurrence, just moves)."""
    fields, values = [], []
    if text is not None:
        fields.append("text = ?")
        values.append(text)
    if due_at is not None:
        fields.append("due_at = ?")
        values.append(due_at)
    if not fields:
        return False
    values.append(reminder_id)
    with _connect() as conn:
        cur = conn.execute(f"UPDATE reminders SET {', '.join(fields)} WHERE id = ?", values)
        return cur.rowcount > 0


def reminder_listing() -> str:
    """A compact one-line-per-reminder summary (id, text, due date,
    recurrence) for agent/loop.py's per-turn context — same fix already
    applied to the knowledge base's document listing (memory/knowledge.py),
    for the identical reason confirmed live, repeatedly: a 7B local model
    asked to "list my reminders," or to update/delete "my X reminder" by
    description, will sometimes skip calling manage_reminders entirely and
    fabricate a plausible-looking answer instead — one live test invented a
    "Buy milk" reminder that never existed, and separately used a
    hallucinated id to silently update a real but completely unrelated
    reminder. Giving the model the real list unconditionally, every turn,
    means even a turn where it skips the tool call is grounded in real
    data, and gives it the correct ids to reference for update/delete
    instead of guessing from its own possibly-wrong memory of the
    conversation. Returns "" when there are no active reminders, so the
    caller can skip the section entirely.

    Dates are pre-formatted via format_due() (a real weekday name, computed
    from the actual date) rather than left as raw ISO — confirmed live as a
    second, related bug: given only raw ISO dates here, the model tried to
    work out weekday names itself when composing a reply and got them
    wrong, even contradicting itself across two consecutive calendar dates
    in the same answer. Handing it an already-correct weekday leaves
    nothing for it to (mis)compute."""
    reminders = list_reminders()
    if not reminders:
        return ""
    lines = []
    for r in reminders:
        line = f"- #{r['id']}: {r['text']}"
        if r["due_at"]:
            line += f" (due {format_due(r['due_at'])})"
        if r.get("recurrence", "none") != "none":
            line += f" [repeats {r['recurrence']}]"
        lines.append(line)
    return "\n".join(lines)


def delete_reminder(reminder_id: int) -> bool:
    """Real deletion — distinct from complete_reminder's soft done=1.
    "delete" means it shouldn't exist; "complete" means done, kept as a
    record."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        return cur.rowcount > 0


def list_reminders(include_done: bool = False) -> list[dict]:
    with _connect() as conn:
        query = (
            "SELECT * FROM reminders"
            if include_done
            else "SELECT * FROM reminders WHERE done = 0"
        )
        rows = conn.execute(query + " ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def complete_reminder(reminder_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,))
        return cur.rowcount > 0


# ----------------------------------------------------------------------
# Sessions and message history
# ----------------------------------------------------------------------

def create_session() -> int:
    """Start a new conversation session, return its ID."""
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (started_at, last_active_at) VALUES (?, ?)", (now, now)
        )
        return cur.lastrowid


def get_latest_session() -> int | None:
    """Return the most recent session's ID, or None if there are no sessions yet."""
    with _connect() as conn:
        row = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        return row["id"] if row else None


def list_sessions(limit: int = 10) -> list[dict]:
    """List recent sessions with their message counts and a title (the
    first user message, so a session picker can show something recognizable
    instead of just a raw sequential ID) - useful for a session picker."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.started_at, s.last_active_at,
                      COUNT(m.id) AS message_count,
                      (SELECT content FROM messages
                       WHERE session_id = s.id AND role = 'user'
                       ORDER BY id ASC LIMIT 1) AS title
               FROM sessions s
               LEFT JOIN messages m ON m.session_id = s.id
               GROUP BY s.id
               ORDER BY s.id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_message(session_id: int, role: str, content: str, name: str | None = None) -> None:
    """Persist a single message and bump the session's last-active timestamp."""
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, name, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, name, now),
        )
        conn.execute(
            "UPDATE sessions SET last_active_at = ? WHERE id = ?", (now, session_id)
        )


def load_messages(session_id: int, limit: int = 20) -> list[dict]:
    """
    Load the most recent messages from a session, oldest-first,
    in the shape the agent loop expects.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, name FROM messages "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()

    messages = []
    for r in reversed(rows):  # back to chronological order
        msg = {"role": r["role"], "content": r["content"]}
        if r["name"]:
            msg["name"] = r["name"]
        messages.append(msg)
    return messages


def delete_session(session_id: int) -> bool:
    """Delete a session and all its messages."""
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


# ----------------------------------------------------------------------
# Knowledge base documents
# ----------------------------------------------------------------------
# This table is the source of truth for a document's ID — that same integer
# ID is stamped into each of the document's chunks in the knowledge_base
# Chroma collection (see memory/knowledge.py), so deleting a document never
# needs to track individual chunk UUIDs anywhere.

def add_document(
    filename: str,
    chunk_count: int,
    tags: list[str] | None = None,
    source_type: str = "manual",
    structured_data: dict | None = None,
    doc_type: str = "",
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO documents (filename, chunk_count, tags, source_type, structured_data, doc_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                filename,
                chunk_count,
                json.dumps(tags or []),
                source_type,
                json.dumps(structured_data or {}),
                doc_type,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def _decode_document(doc: dict) -> dict:
    doc["tags"] = json.loads(doc["tags"])
    doc["structured_data"] = json.loads(doc["structured_data"])
    return doc


def list_documents() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
        documents = [dict(r) for r in rows]
    return [_decode_document(d) for d in documents]


def get_document(document_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return _decode_document(dict(row)) if row else None


def update_document_structured_data(document_id: int, structured_data: dict, doc_type: str) -> bool:
    """Overwrite a document's extracted fields in place — used to re-run
    memory/extraction.py against an already-ingested document (e.g. after
    improving the extraction prompt) without needing to re-upload the
    original file."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE documents SET structured_data = ?, doc_type = ? WHERE id = ?",
            (json.dumps(structured_data), doc_type, document_id),
        )
        return cur.rowcount > 0


def update_document_tags(document_id: int, tags: list[str]) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE documents SET tags = ? WHERE id = ?", (json.dumps(tags), document_id)
        )
        return cur.rowcount > 0


def delete_document(document_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return cur.rowcount > 0


# ----------------------------------------------------------------------
# Watched folders (Phase 4 — memory/folder_watch.py)
# ----------------------------------------------------------------------
# watched_files is the reconciliation ledger: it maps an absolute filepath to
# the document it produced and the mtime it was last ingested at, so a scan
# can tell "new file" (not in the table) from "changed file" (mtime differs)
# from "deleted file" (in the table, no longer on disk) without re-reading
# every file's content just to check.

def add_watched_folder(path: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO watched_folders (path, created_at) VALUES (?, ?)",
            (path, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def list_watched_folders() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM watched_folders ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_watched_folder(folder_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM watched_folders WHERE id = ?", (folder_id,)).fetchone()
        return dict(row) if row else None


def delete_watched_folder(folder_id: int) -> bool:
    with _connect() as conn:
        conn.execute("DELETE FROM watched_files WHERE folder_id = ?", (folder_id,))
        cur = conn.execute("DELETE FROM watched_folders WHERE id = ?", (folder_id,))
        return cur.rowcount > 0


def get_watched_file(filepath: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM watched_files WHERE filepath = ?", (filepath,)).fetchone()
        return dict(row) if row else None


def upsert_watched_file(folder_id: int, filepath: str, document_id: int, mtime: float) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO watched_files (folder_id, filepath, document_id, mtime)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(filepath) DO UPDATE SET document_id = excluded.document_id, mtime = excluded.mtime""",
            (folder_id, filepath, document_id, mtime),
        )


def delete_watched_file(filepath: str) -> dict | None:
    """Remove a file's tracking row and return it (so the caller can pull
    document_id to delete the associated document), or None if it wasn't tracked."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM watched_files WHERE filepath = ?", (filepath,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM watched_files WHERE filepath = ?", (filepath,))
        return dict(row)


def list_watched_files(folder_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM watched_files WHERE folder_id = ? ORDER BY filepath", (folder_id,)
        ).fetchall()
        return [dict(r) for r in rows]


init_db()