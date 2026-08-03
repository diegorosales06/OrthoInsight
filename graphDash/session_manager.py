import os
import sqlite3
from datetime import datetime

from graphDash.paths import db_path, ensure_dirs

DEFAULT_KEEP = 50


def _connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time   TEXT,
                file_path  TEXT NOT NULL,
                manual_file_path TEXT
            )
        """)
        # Add manual_file_path column if it doesn't exist (migration for existing DBs)
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN manual_file_path TEXT")
        except Exception:
            pass  # Column already exists


def start_session(csv_file_path: str, manual_file_path: str = None) -> int:
    start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (start_time, end_time, file_path, manual_file_path) VALUES (?, NULL, ?, ?)",
            (start, csv_file_path, manual_file_path),
        )
        return cur.lastrowid


def end_session(session_id: int) -> None:
    end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET end_time = ? WHERE session_id = ?",
            (end, session_id),
        )


def get_sessions(limit: int = 20, offset: int = 0):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT session_id, start_time, end_time, file_path, manual_file_path "
            "FROM sessions ORDER BY session_id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def get_session_by_id(session_id: int):
    with _connect() as conn:
        row = conn.execute(
            "SELECT session_id, start_time, end_time, file_path, manual_file_path "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None


def count_sessions() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def prune_old_sessions(keep_count: int = DEFAULT_KEEP) -> int:
    """Delete all but the most recent `keep_count` sessions and their CSVs.
    Returns the number of session rows removed."""
    with _connect() as conn:
        old = conn.execute(
            "SELECT session_id, file_path, manual_file_path FROM sessions "
            "ORDER BY session_id DESC LIMIT -1 OFFSET ?",
            (keep_count,),
        ).fetchall()
        if not old:
            return 0
        ids = [r["session_id"] for r in old]
        for r in old:
            try:
                os.remove(r["file_path"])
            except OSError:
                pass
            if r["manual_file_path"]:
                try:
                    os.remove(r["manual_file_path"])
                except OSError:
                    pass
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM sessions WHERE session_id IN ({placeholders})", ids)
        return len(ids)
