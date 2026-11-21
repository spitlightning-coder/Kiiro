"""Server-Einstellungen und Erinnerungen (SQLite, data/server.sqlite3).

Getrennt vom Langzeitgedächtnis, damit Server-Aktionen auch ohne Gedächtnis laufen.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (guild_id, key)
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    due_at REAL NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(done, due_at);
"""


@dataclass
class Reminder:
    id: int
    guild_id: int
    channel_id: int
    user_id: int
    due_at: float
    text: str


class ServerData:
    def __init__(self, data_dir: Path | str, clock=time.time):
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path / "server.sqlite3", check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- Einstellungen ----------

    def get(self, guild_id: int, key: str, default=None):
        with self._lock:
            row = self.db.execute("SELECT value FROM settings WHERE guild_id = ? AND key = ?",
                                  (guild_id, key)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, guild_id: int, key: str, value) -> None:
        with self._lock:
            self.db.execute("INSERT INTO settings (guild_id, key, value) VALUES (?, ?, ?) "
                            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
                            (guild_id, key, json.dumps(value)))
            self.db.commit()

    def stopped(self, guild_id: int) -> bool:
        return bool(self.get(guild_id, "stopped", False))

    def self_roles(self, guild_id: int) -> list[int]:
        return [int(r) for r in self.get(guild_id, "self_roles", [])]

    def set_self_role(self, guild_id: int, role_id: int, allowed: bool) -> None:
        roles = set(self.self_roles(guild_id))
        roles.add(role_id) if allowed else roles.discard(role_id)
        self.set(guild_id, "self_roles", sorted(roles))

    # ---------- Erinnerungen ----------

    def add_reminder(self, guild_id: int, channel_id: int, user_id: int, due_at: float, text: str) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO reminders (guild_id, channel_id, user_id, due_at, text, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (guild_id, channel_id, user_id, due_at, text[:500], self._clock()))
            self.db.commit()
            return cur.lastrowid

    def open_reminders(self, guild_id: int, user_id: int) -> list[Reminder]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, guild_id, channel_id, user_id, due_at, text FROM reminders "
                "WHERE guild_id = ? AND user_id = ? AND done = 0 ORDER BY due_at", (guild_id, user_id)).fetchall()
        return [Reminder(*r) for r in rows]

    def due_reminders(self, now: float | None = None) -> list[Reminder]:
        now = self._clock() if now is None else now
        with self._lock:
            rows = self.db.execute(
                "SELECT id, guild_id, channel_id, user_id, due_at, text FROM reminders "
                "WHERE done = 0 AND due_at <= ? ORDER BY due_at LIMIT 20", (now,)).fetchall()
        return [Reminder(*r) for r in rows]

    def finish_reminder(self, reminder_id: int) -> None:
        with self._lock:
            self.db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,))
            self.db.commit()

    def cancel_reminder(self, guild_id: int, user_id: int, reminder_id: int) -> bool:
        with self._lock:
            cur = self.db.execute("UPDATE reminders SET done = 1 WHERE id = ? AND guild_id = ? AND user_id = ? "
                                  "AND done = 0", (reminder_id, guild_id, user_id))
            self.db.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self.db.close()
