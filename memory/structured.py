"""
Structured memory: SQLite-backed storage for discrete facts, reminders,
conversation sessions, and message history - things you want to query
exactly, not semantically.
"""
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


init_db()