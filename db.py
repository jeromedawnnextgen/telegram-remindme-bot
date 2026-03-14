import os
import sqlite3
from datetime import datetime

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
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def add_reminder(chat_id: int, task: str, fire_at: datetime) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (chat_id, task, fire_at, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, task, fire_at.isoformat(), datetime.utcnow().isoformat()),
        )
        conn.commit()
        return cur.lastrowid


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


def mark_fired(reminder_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))
        conn.commit()


def delete_reminder(reminder_id: int, chat_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND chat_id = ? AND fired = 0",
            (reminder_id, chat_id),
        )
        conn.commit()
        return cur.rowcount > 0
