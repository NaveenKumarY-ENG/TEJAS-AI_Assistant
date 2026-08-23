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

def add_reminder(text: str, due_at: str | None = None) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (text, due_at, created_at) VALUES (?, ?, ?)",
            (text, due_at, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


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
    filename: str, chunk_count: int, tags: list[str] | None = None, source_type: str = "manual"
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO documents (filename, chunk_count, tags, source_type, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (filename, chunk_count, json.dumps(tags or []), source_type, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def list_documents() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
        documents = [dict(r) for r in rows]
    for doc in documents:
        doc["tags"] = json.loads(doc["tags"])
    return documents


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