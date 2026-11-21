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
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    creator_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    starts_at REAL NOT NULL,
    role_id INTEGER,
    stage INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS event_rsvp (
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    author_name TEXT NOT NULL,
    text TEXT NOT NULL,
    link TEXT NOT NULL,
    saved_by INTEGER NOT NULL,
    said_at REAL NOT NULL,
    UNIQUE(guild_id, link)
);
CREATE TABLE IF NOT EXISTS wordle (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    guesses TEXT NOT NULL DEFAULT '[]',
    result INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS quiz_scores (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    correct INTEGER NOT NULL DEFAULT 0,
    answered INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS news_seen (
    guild_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    seen_at REAL NOT NULL,
    PRIMARY KEY (guild_id, item_id)
);
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

    # ---------- Events ----------

    def add_event(self, guild_id: int, channel_id: int, creator_id: int, title: str, details: str,
                  starts_at: float, role_id: int | None) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO events (guild_id, channel_id, creator_id, title, details, starts_at, role_id) "
                                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                  (guild_id, channel_id, creator_id, title[:200], details[:800], starts_at, role_id))
            self.db.commit()
            return cur.lastrowid

    def set_event_message(self, event_id: int, message_id: int) -> None:
        with self._lock:
            self.db.execute("UPDATE events SET message_id = ? WHERE id = ?", (message_id, event_id))
            self.db.commit()

    def get_event(self, event_id: int) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT id, guild_id, channel_id, message_id, creator_id, title, details, starts_at, "
                                  "role_id, stage FROM events WHERE id = ?", (event_id,)).fetchone()
        keys = ("id", "guild_id", "channel_id", "message_id", "creator_id", "title", "details", "starts_at",
                "role_id", "stage")
        return dict(zip(keys, row)) if row else None

    def due_events(self, now: float) -> list[dict]:
        """Events, bei denen die 15-Minuten-Erinnerung (stage 0) oder der Start (stage 1) fällig ist."""
        with self._lock:
            ids = [r[0] for r in self.db.execute(
                "SELECT id FROM events WHERE (stage = 0 AND starts_at - 900 <= ?) OR (stage = 1 AND starts_at <= ?)",
                (now, now)).fetchall()]
        return [e for e in (self.get_event(i) for i in ids) if e]

    def set_event_stage(self, event_id: int, stage: int) -> None:
        with self._lock:
            self.db.execute("UPDATE events SET stage = ? WHERE id = ?", (stage, event_id))
            self.db.commit()

    def rsvp(self, event_id: int, user_id: int, status: str | None) -> None:
        with self._lock:
            if status is None:
                self.db.execute("DELETE FROM event_rsvp WHERE event_id = ? AND user_id = ?", (event_id, user_id))
            else:
                self.db.execute("INSERT INTO event_rsvp (event_id, user_id, status) VALUES (?, ?, ?) "
                                "ON CONFLICT(event_id, user_id) DO UPDATE SET status = excluded.status",
                                (event_id, user_id, status))
            self.db.commit()

    def rsvps(self, event_id: int) -> dict[str, list[int]]:
        with self._lock:
            rows = self.db.execute("SELECT user_id, status FROM event_rsvp WHERE event_id = ?", (event_id,)).fetchall()
        out: dict[str, list[int]] = {"ja": [], "vielleicht": [], "nein": []}
        for uid, status in rows:
            out.setdefault(status, []).append(uid)
        return out

    # ---------- Zitate ----------

    def add_quote(self, guild_id: int, author_id: int, author_name: str, text: str, link: str,
                  saved_by: int, said_at: float) -> int | None:
        with self._lock:
            cur = self.db.execute("INSERT OR IGNORE INTO quotes (guild_id, author_id, author_name, text, link, saved_by, "
                                  "said_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                  (guild_id, author_id, author_name[:64], text[:1000], link, saved_by, said_at))
            self.db.commit()
            return cur.lastrowid if cur.rowcount else None

    def random_quote(self, guild_id: int, author_id: int | None = None) -> dict | None:
        q = "SELECT id, author_id, author_name, text, link, said_at FROM quotes WHERE guild_id = ?"
        args: list = [guild_id]
        if author_id:
            q += " AND author_id = ?"
            args.append(author_id)
        with self._lock:
            row = self.db.execute(q + " ORDER BY RANDOM() LIMIT 1", args).fetchone()
        return dict(zip(("id", "author_id", "author_name", "text", "link", "said_at"), row)) if row else None

    def quote_count(self, guild_id: int) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM quotes WHERE guild_id = ?", (guild_id,)).fetchone()[0]

    def delete_quote(self, guild_id: int, quote_id: int, user_id: int, admin: bool = False) -> bool:
        with self._lock:
            cur = self.db.execute("DELETE FROM quotes WHERE guild_id = ? AND id = ? AND (? OR author_id = ? OR saved_by = ?)",
                                  (guild_id, quote_id, int(admin), user_id, user_id))
            self.db.commit()
            return cur.rowcount > 0

    # ---------- Worträtsel ----------

    def wordle_get(self, guild_id: int, user_id: int, day: str) -> tuple[list[str], int]:
        with self._lock:
            row = self.db.execute("SELECT guesses, result FROM wordle WHERE guild_id = ? AND user_id = ? AND day = ?",
                                  (guild_id, user_id, day)).fetchone()
        return (json.loads(row[0]), row[1]) if row else ([], 0)

    def wordle_set(self, guild_id: int, user_id: int, day: str, guesses: list[str], result: int) -> None:
        with self._lock:
            self.db.execute("INSERT INTO wordle (guild_id, user_id, day, guesses, result) VALUES (?, ?, ?, ?, ?) "
                            "ON CONFLICT(guild_id, user_id, day) DO UPDATE SET guesses = excluded.guesses, "
                            "result = excluded.result", (guild_id, user_id, day, json.dumps(guesses), result))
            self.db.commit()

    def wordle_stats(self, guild_id: int) -> list[tuple[int, int, int, float]]:
        """(user_id, gelöst, gespielt, Ø Versuche) – result: 1-6 gelöst, -1 verloren, 0 läuft."""
        with self._lock:
            return self.db.execute(
                "SELECT user_id, SUM(result > 0), SUM(result != 0), AVG(CASE WHEN result > 0 THEN result END) "
                "FROM wordle WHERE guild_id = ? GROUP BY user_id HAVING SUM(result != 0) > 0 "
                "ORDER BY SUM(result > 0) DESC, AVG(CASE WHEN result > 0 THEN result END) ASC LIMIT 10",
                (guild_id,)).fetchall()

    def wordle_streak(self, guild_id: int, user_id: int, today: str) -> int:
        with self._lock:
            rows = self.db.execute("SELECT day, result FROM wordle WHERE guild_id = ? AND user_id = ? AND result != 0 "
                                   "ORDER BY day DESC", (guild_id, user_id)).fetchall()
        from datetime import date, timedelta
        t = date.fromisoformat(today)
        streak, expected = 0, None
        if rows and date.fromisoformat(rows[0][0]) < t - timedelta(days=1):
            return 0  # Serie gerissen
        for day, result in rows:
            d = date.fromisoformat(day)
            if expected is not None and d != expected:
                break
            if result <= 0:
                break
            streak += 1
            expected = d - timedelta(days=1)
        return streak

    # ---------- Quiz ----------

    def add_quiz_result(self, guild_id: int, user_id: int, name: str, points: int, correct: bool) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO quiz_scores (guild_id, user_id, name, points, correct, answered) VALUES (?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET name = excluded.name, points = points + excluded.points, "
                "correct = correct + excluded.correct, answered = answered + 1",
                (guild_id, user_id, name[:64], points, int(correct)))
            self.db.commit()

    def quiz_top(self, guild_id: int, limit: int = 10) -> list[tuple[str, int, int, int]]:
        with self._lock:
            return self.db.execute("SELECT name, points, correct, answered FROM quiz_scores WHERE guild_id = ? "
                                   "ORDER BY points DESC, correct DESC LIMIT ?", (guild_id, limit)).fetchall()

    # ---------- News ----------

    def news_seen(self, guild_id: int, item_id: str) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM news_seen WHERE guild_id = ? AND item_id = ?",
                                   (guild_id, item_id)).fetchone() is not None

    def mark_news(self, guild_id: int, item_ids: list[str]) -> None:
        now = self._clock()
        with self._lock:
            self.db.executemany("INSERT OR IGNORE INTO news_seen (guild_id, item_id, seen_at) VALUES (?, ?, ?)",
                                [(guild_id, i, now) for i in item_ids])
            self.db.execute("DELETE FROM news_seen WHERE seen_at < ?", (now - 90 * 86400,))
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()
