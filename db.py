import os
import sqlite3
from datetime import datetime, timezone

_data_dir = os.environ.get("DATA_DIR", ".")
os.makedirs(_data_dir, exist_ok=True)
DB_PATH = os.path.join(_data_dir, "reminders.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                task       TEXT NOT NULL,
                fire_at    TEXT NOT NULL,
                fired      INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                recurrence TEXT
            )
        """)
        # Add recurrence column to existing tables (no-op if already present)
        try:
            conn.execute("ALTER TABLE reminders ADD COLUMN recurrence TEXT")
        except sqlite3.OperationalError:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                task       TEXT NOT NULL,
                project    TEXT,
                done       INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                done_at    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                note       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

def add_reminder(chat_id: int, task: str, fire_at: datetime, recurrence: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (chat_id, task, fire_at, created_at, recurrence) VALUES (?, ?, ?, ?, ?)",
            (chat_id, task, fire_at.isoformat(), datetime.now(timezone.utc).isoformat(), recurrence),
        )
        conn.commit()
        return cur.lastrowid


def get_reminder(reminder_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        return dict(row) if row else None


def get_pending(chat_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE chat_id = ? AND fired = 0 ORDER BY fire_at",
            (chat_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_pending() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE fired = 0 ORDER BY fire_at"
        ).fetchall()
        return [dict(r) for r in rows]


def get_todays_reminders(chat_id: int, start_utc: datetime, end_utc: datetime) -> list:
    """One-time reminders firing between start and end (UTC datetimes)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE chat_id = ? AND fired = 0 "
            "AND recurrence IS NULL AND fire_at >= ? AND fire_at < ? ORDER BY fire_at",
            (chat_id, start_utc.isoformat(), end_utc.isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_fired(reminder_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))
        conn.commit()


def update_reminder_fire_at(reminder_id: int, fire_at: datetime):
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET fire_at = ? WHERE id = ?",
            (fire_at.isoformat(), reminder_id),
        )
        conn.commit()


def delete_reminder(reminder_id: int, chat_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND chat_id = ? AND fired = 0",
            (reminder_id, chat_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def add_task(chat_id: int, task: str, project: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (chat_id, task, project, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, task, project, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def get_tasks(chat_id: int, project: str | None = None, include_done: bool = False) -> list:
    with get_conn() as conn:
        if project:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE chat_id = ? AND project = ? AND done = ? ORDER BY created_at",
                (chat_id, project, 1 if include_done else 0),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE chat_id = ? AND done = ? ORDER BY created_at",
                (chat_id, 1 if include_done else 0),
            ).fetchall()
        return [dict(r) for r in rows]


def complete_task(task_id: int, chat_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE tasks SET done = 1, done_at = ? WHERE id = ? AND chat_id = ? AND done = 0",
            (datetime.now(timezone.utc).isoformat(), task_id, chat_id),
        )
        conn.commit()
        return cur.rowcount > 0


def complete_task_by_name(chat_id: int, name: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE tasks SET done = 1, done_at = ? WHERE chat_id = ? AND done = 0 AND task LIKE ?",
            (datetime.now(timezone.utc).isoformat(), chat_id, f"%{name}%"),
        )
        conn.commit()
        return cur.rowcount > 0


def clear_completed_tasks(chat_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE chat_id = ? AND done = 1", (chat_id,))
        conn.commit()
        return cur.rowcount


def count_completed_yesterday(chat_id: int, date_str: str) -> int:
    """Count tasks completed on a specific date (YYYY-MM-DD prefix match against UTC done_at)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE chat_id = ? AND done_at LIKE ?",
            (chat_id, f"{date_str}%"),
        ).fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def add_note(chat_id: int, note: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (chat_id, note, created_at) VALUES (?, ?, ?)",
            (chat_id, note, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def get_notes(chat_id: int, limit: int = 10) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notes WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
