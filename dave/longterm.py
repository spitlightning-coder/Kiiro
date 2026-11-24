"""Langzeitgedächtnis: SQLite für feste Fakten, ChromaDB für Erinnerungen nach Ähnlichkeit.

- facts:       feste Fakten über Leute (user_id gesetzt) oder den Server (user_id NULL)
- self_notes:  Daves eigene Entwicklung (Meinungen, Insider-Witze, Spitznamen)
- memories:    kurze Gesprächs-Zusammenfassungen, in ChromaDB nach Bedeutung durchsuchbar.
               In SQLite steht nur, wen eine Erinnerung betrifft (für /vergiss_mich).

Fehlt ChromaDB oder klappt es nicht, läuft alles andere trotzdem (nur ohne Ähnlichkeitssuche).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

log = logging.getLogger("dave.longterm")

MAX_FACTS_PER_USER = 40
MAX_SERVER_FACTS = 60
MAX_SELF_NOTES = 40
MAX_TEXT = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    user_id INTEGER,
    about TEXT NOT NULL,
    fact TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS facts_unique ON facts(guild_id, IFNULL(user_id, 0), fact_key);
CREATE TABLE IF NOT EXISTS self_notes (
    id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    note_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(guild_id, note_key)
);
CREATE TABLE IF NOT EXISTS memory_users (
    memory_id TEXT NOT NULL,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_users_idx ON memory_users(guild_id, user_id);
CREATE TABLE IF NOT EXISTS user_prefs (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    stil TEXT NOT NULL DEFAULT 'normal',
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS server_notes (
    id TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    mood TEXT,
    topics TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS server_notes_idx ON server_notes(guild_id, kind, created_at);
CREATE TABLE IF NOT EXISTS activity (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    hour INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id, hour)
);
"""

STYLES = ("normal", "einfach")
SERVER_NOTE_MAX = 600


def _key(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .!")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text[:MAX_TEXT]


def embed_threads() -> int:
    """Wie viele Kerne wir wirklich haben (Container-Limit beachten), höchstens 2."""
    cpus = None
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            cpus = max(1, int(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    if cpus is None:
        try:
            cpus = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            cpus = os.cpu_count() or 1
    return 1 if cpus <= 2 else 2


def default_embedding(model_dir: Path):
    """Chromas Standard-Embedding, aber mit dauerhaftem Modell-Ordner und einmal geladenem Modell.

    Chroma legt das ~80-MB-Modell sonst unter ~/.cache ab – auf dem Server ist das nach jedem Neustart weg.
    Außerdem baut die Standard-Funktion bei jedem Aufruf ein neues Modell-Objekt; hier passiert das nur einmal.
    Der Name bleibt "default", damit bestehende Sammlungen ohne Konflikt weiter funktionieren.
    """
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = Path(model_dir) / ONNXMiniLM_L6_V2.MODEL_NAME

    class FewThreads(ONNXMiniLM_L6_V2):
        """Wie das Original, aber mit wenigen Rechen-Threads.

        onnxruntime startet sonst so viele Threads, wie der Host Kerne hat – in einem Container mit 1-2 echten
        Kernen bremsen die sich gegenseitig aus (aus 50 ms werden 10 s).
        """

        @cached_property
        def model(self):
            so = self.ort.SessionOptions()
            so.log_severity_level = 3
            so.graph_optimization_level = self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.intra_op_num_threads = embed_threads()
            so.inter_op_num_threads = 1
            so.execution_mode = self.ort.ExecutionMode.ORT_SEQUENTIAL
            # Threads nach getaner Arbeit schlafen legen statt im Kreis drehen (frisst sonst die CPU)
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            so.add_session_config_entry("session.inter_op.allow_spinning", "0")
            return self.ort.InferenceSession(
                os.path.join(self.DOWNLOAD_PATH, self.EXTRACTED_FOLDER_NAME, "model.onnx"),
                providers=["CPUExecutionProvider"], sess_options=so)

    class CachedDefault(DefaultEmbeddingFunction):
        _model = None
        _model_lock = threading.Lock()

        def __call__(self, input):  # noqa: A002 – Name von Chroma vorgegeben
            with self._model_lock:
                if CachedDefault._model is None:
                    CachedDefault._model = FewThreads()
            started = time.monotonic()
            out = CachedDefault._model(input)
            took = time.monotonic() - started
            if took > 1:
                log.info("Embedding für %d Text(e) dauerte %.1f s", len(input), took)
            return out

    return CachedDefault()


@dataclass
class Stats:
    facts: int
    people: int
    server_facts: int
    memories: int
    self_notes: int
    since: float | None
    server_notes: int = 0


@dataclass
class ServerNote:
    id: str
    channel_id: int | None
    kind: str
    text: str
    mood: str | None
    topics: list[str]
    created_at: float


@dataclass
class Fact:
    user_id: int | None
    about: str
    fact: str


class MemoryStore:
    def __init__(self, data_dir: Path | str, *, embedding_function=None, use_chroma: bool = True,
                 clock=time.time):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.data_dir / "dave.sqlite3", check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.collection = None
        self.server_collection = None
        if use_chroma:
            self._init_chroma(embedding_function)

    def _init_chroma(self, embedding_function) -> None:
        try:
            import chromadb

            if embedding_function is None:
                embedding_function = default_embedding(self.data_dir / "models")
            self._embed = embedding_function
            client = chromadb.PersistentClient(path=str(self.data_dir / "chroma"))
            self.collection = client.get_or_create_collection(
                "dave_memories", metadata={"hnsw:space": "cosine"}, embedding_function=embedding_function)
            self.server_collection = client.get_or_create_collection(
                "dave_server", metadata={"hnsw:space": "cosine"}, embedding_function=embedding_function)
            log.info("ChromaDB bereit (%d Erinnerungen, %d Server-Notizen)", self.collection.count(),
                     self.server_collection.count())
        except Exception as e:  # noqa: BLE001 – Gedächtnis darf Dave nie abstürzen lassen
            log.warning("ChromaDB nicht verfügbar, Erinnerungen nach Ähnlichkeit sind aus: %s", e)
            self.collection = None
            self.server_collection = None

    def warmup(self) -> None:
        """Lädt das Embedding-Modell vorab (beim ersten Mal ~80 MB Download), damit die erste Antwort nicht hängt."""
        if self.collection is None:
            return
        try:
            self._embed(["Aufwärmen"])
            log.info("Embedding-Modell geladen")
        except Exception as e:  # noqa: BLE001
            log.warning("Embedding-Modell konnte nicht geladen werden, Erinnerungen nach Ähnlichkeit sind aus: %s", e)
            self.collection = None
            self.server_collection = None

    # ---------- Fakten ----------

    def add_fact(self, guild_id: int, user_id: int | None, about: str, fact: str, source: str = "auto") -> bool:
        fact = _clip(fact)
        if not fact:
            return False
        now = self._clock()
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO facts (guild_id, user_id, about, fact, fact_key, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO UPDATE SET updated_at = excluded.updated_at",
                (guild_id, user_id, _clip(about) or "?", fact, _key(fact), source, now, now),
            )
            limit = MAX_FACTS_PER_USER if user_id is not None else MAX_SERVER_FACTS
            self.db.execute(
                "DELETE FROM facts WHERE id IN (SELECT id FROM facts WHERE guild_id = ? AND IFNULL(user_id, 0) = ? "
                "ORDER BY updated_at DESC LIMIT -1 OFFSET ?)",
                (guild_id, user_id or 0, limit),
            )
            self.db.commit()
            return cur.rowcount > 0

    def facts_about(self, guild_id: int, user_id: int, limit: int = 10) -> list[Fact]:
        with self._lock:
            rows = self.db.execute(
                "SELECT user_id, about, fact FROM facts WHERE guild_id = ? AND user_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (guild_id, user_id, limit),
            ).fetchall()
        return [Fact(*r) for r in rows]

    def server_facts(self, guild_id: int, limit: int = 10) -> list[Fact]:
        with self._lock:
            rows = self.db.execute(
                "SELECT user_id, about, fact FROM facts WHERE guild_id = ? AND user_id IS NULL "
                "ORDER BY updated_at DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [Fact(*r) for r in rows]

    # ---------- Daves eigene Entwicklung ----------

    def add_self_note(self, guild_id: int, note: str) -> bool:
        note = _clip(note)
        if not note:
            return False
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO self_notes (guild_id, note, note_key, created_at) VALUES (?, ?, ?, ?)",
                (guild_id, note, _key(note), self._clock()),
            )
            self.db.execute(
                "DELETE FROM self_notes WHERE id IN (SELECT id FROM self_notes WHERE guild_id = ? "
                "ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (guild_id, MAX_SELF_NOTES),
            )
            self.db.commit()
            return cur.rowcount > 0

    def self_notes(self, guild_id: int, limit: int = 12) -> list[str]:
        with self._lock:
            rows = self.db.execute(
                "SELECT note FROM self_notes WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [r[0] for r in rows]

    def stats(self, guild_id: int) -> Stats:
        with self._lock:
            facts, people = self.db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM facts WHERE guild_id = ? AND user_id IS NOT NULL",
                (guild_id,)).fetchone()
            server = self.db.execute("SELECT COUNT(*) FROM facts WHERE guild_id = ? AND user_id IS NULL",
                                     (guild_id,)).fetchone()[0]
            notes = self.db.execute("SELECT COUNT(*) FROM self_notes WHERE guild_id = ?", (guild_id,)).fetchone()[0]
            since = self.db.execute(
                "SELECT MIN(t) FROM (SELECT MIN(created_at) AS t FROM facts WHERE guild_id = ? "
                "UNION ALL SELECT MIN(created_at) FROM self_notes WHERE guild_id = ?)",
                (guild_id, guild_id)).fetchone()[0]
            snotes = self.db.execute("SELECT COUNT(*) FROM server_notes WHERE guild_id = ?",
                                     (guild_id,)).fetchone()[0]
        return Stats(facts, people, server, self.memory_count(guild_id), notes, since, snotes)

    # ---------- Erinnerungen (ChromaDB) ----------

    def add_memory(self, guild_id: int, channel_id: int, text: str, user_ids: list[int]) -> str | None:
        text = _clip(text)
        if not text or self.collection is None:
            return None
        memory_id = f"m-{uuid.uuid4().hex}"
        try:
            self.collection.add(
                ids=[memory_id],
                documents=[text],
                metadatas=[{"guild_id": guild_id, "channel_id": channel_id, "created_at": self._clock()}],
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Erinnerung konnte nicht gespeichert werden: %s", e)
            return None
        with self._lock:
            self.db.executemany(
                "INSERT INTO memory_users (memory_id, guild_id, user_id) VALUES (?, ?, ?)",
                [(memory_id, guild_id, u) for u in set(user_ids)],
            )
            self.db.commit()
        return memory_id

    def recall(self, guild_id: int, query: str, k: int = 3, max_distance: float = 0.6) -> list[str]:
        if self.collection is None or not query.strip():
            return []
        started = time.monotonic()
        try:
            n = self.collection.count()
            if n == 0:
                return []
            res = self.collection.query(query_texts=[query], n_results=min(k, n), where={"guild_id": guild_id})
        except Exception as e:  # noqa: BLE001
            log.warning("Erinnern fehlgeschlagen: %s", e)
            return []
        finally:
            if time.monotonic() - started > 1:
                log.info("Gedächtnissuche dauerte %.1f s", time.monotonic() - started)
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[None] * len(docs)])[0]
        return [d for d, dist in zip(docs, dists) if dist is None or dist <= max_distance]

    def memory_count(self, guild_id: int, user_id: int | None = None) -> int:
        with self._lock:
            if user_id is None:
                row = self.db.execute("SELECT COUNT(DISTINCT memory_id) FROM memory_users WHERE guild_id = ?",
                                      (guild_id,)).fetchone()
            else:
                row = self.db.execute("SELECT COUNT(DISTINCT memory_id) FROM memory_users "
                                      "WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        return row[0]

    # ---------- Sprachstil pro Person ----------

    def get_style(self, guild_id: int, user_id: int) -> str:
        with self._lock:
            row = self.db.execute("SELECT stil FROM user_prefs WHERE guild_id = ? AND user_id = ?",
                                  (guild_id, user_id)).fetchone()
        return row[0] if row and row[0] in STYLES else "normal"

    def set_style(self, guild_id: int, user_id: int, stil: str) -> None:
        if stil not in STYLES:
            raise ValueError(stil)
        with self._lock:
            self.db.execute("INSERT INTO user_prefs (guild_id, user_id, stil) VALUES (?, ?, ?) "
                            "ON CONFLICT(guild_id, user_id) DO UPDATE SET stil = excluded.stil",
                            (guild_id, user_id, stil))
            self.db.commit()

    # ---------- Server-Bewusstsein: Zusammenfassungen, Ereignisse, Aktivität ----------

    def add_server_note(self, guild_id: int, kind: str, text: str, *, channel_id: int | None = None,
                        mood: str | None = None, topics: list[str] | None = None,
                        created_at: float | None = None) -> str | None:
        """Speichert eine Server-Notiz (nur Zusammenfassungen/Ereignisse, nie Wortlaut) in SQLite und ChromaDB."""
        text = " ".join(text.split())[:SERVER_NOTE_MAX]
        if not text:
            return None
        note_id = f"s-{uuid.uuid4().hex}"
        when = created_at if created_at is not None else self._clock()
        with self._lock:
            self.db.execute(
                "INSERT INTO server_notes (id, guild_id, channel_id, kind, text, mood, topics, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (note_id, guild_id, channel_id, kind, text, mood, "|".join(t.strip() for t in topics or [] if t.strip()),
                 when))
            self.db.commit()
        if self.server_collection is not None:
            try:
                meta = {"guild_id": guild_id, "kind": kind, "created_at": when}
                if channel_id is not None:
                    meta["channel_id"] = channel_id
                self.server_collection.add(ids=[note_id], documents=[text], metadatas=[meta])
            except Exception as e:  # noqa: BLE001
                log.warning("Server-Notiz nicht in ChromaDB: %s", e)
        return note_id

    def server_notes(self, guild_id: int, *, kinds: tuple[str, ...] = ("summary", "weekly", "event"),
                     since: float | None = None, until: float | None = None, channel_id: int | None = None,
                     limit: int = 20) -> list[ServerNote]:
        """Neueste zuerst."""
        sql = f"SELECT id, channel_id, kind, text, mood, topics, created_at FROM server_notes WHERE guild_id = ? " \
              f"AND kind IN ({','.join('?' * len(kinds))})"
        args: list = [guild_id, *kinds]
        if since is not None:
            sql += " AND created_at >= ?"
            args.append(since)
        if until is not None:
            sql += " AND created_at < ?"
            args.append(until)
        if channel_id is not None:
            sql += " AND channel_id = ?"
            args.append(channel_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self.db.execute(sql, args).fetchall()
        return [ServerNote(r[0], r[1], r[2], r[3], r[4], [t for t in (r[5] or "").split("|") if t], r[6])
                for r in rows]

    def delete_server_notes(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._lock:
            self.db.execute(f"DELETE FROM server_notes WHERE id IN ({','.join('?' * len(ids))})", ids)
            self.db.commit()
        if self.server_collection is not None:
            try:
                self.server_collection.delete(ids=ids)
            except Exception as e:  # noqa: BLE001
                log.warning("Server-Notizen nicht aus ChromaDB gelöscht: %s", e)

    def recall_server(self, guild_id: int, query: str, k: int = 3, max_distance: float = 0.65) -> list[str]:
        if self.server_collection is None or not query.strip():
            return []
        started = time.monotonic()
        try:
            n = self.server_collection.count()
            if n == 0:
                return []
            res = self.server_collection.query(query_texts=[query], n_results=min(k, n),
                                               where={"guild_id": guild_id})
        except Exception as e:  # noqa: BLE001
            log.warning("Server-Erinnern fehlgeschlagen: %s", e)
            return []
        finally:
            if time.monotonic() - started > 1:
                log.info("Server-Suche dauerte %.1f s", time.monotonic() - started)
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[None] * len(docs)])[0]
        return [d for d, dist in zip(docs, dists) if dist is None or dist <= max_distance]

    def count_activity(self, guild_id: int, channel_id: int, when: float | None = None, n: int = 1) -> None:
        hour = int((when if when is not None else self._clock()) // 3600)
        with self._lock:
            self.db.execute("INSERT INTO activity (guild_id, channel_id, hour, count) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(guild_id, channel_id, hour) DO UPDATE SET count = count + excluded.count",
                            (guild_id, channel_id, hour, n))
            self.db.commit()

    def activity_by_channel(self, guild_id: int, since: float) -> list[tuple[int, int]]:
        """[(channel_id, Nachrichten)] seit `since`, meiste zuerst."""
        with self._lock:
            rows = self.db.execute("SELECT channel_id, SUM(count) FROM activity WHERE guild_id = ? AND hour >= ? "
                                   "GROUP BY channel_id ORDER BY 2 DESC", (guild_id, int(since // 3600))).fetchall()
        return [(r[0], r[1]) for r in rows]

    def activity_hours(self, guild_id: int, since: float) -> list[tuple[int, int]]:
        """[(Stunde als Epoch-Stunde, Nachrichten)] seit `since` – Umrechnung in Uhrzeit macht der Aufrufer."""
        with self._lock:
            rows = self.db.execute("SELECT hour, SUM(count) FROM activity WHERE guild_id = ? AND hour >= ? "
                                   "GROUP BY hour", (guild_id, int(since // 3600))).fetchall()
        return [(r[0], r[1]) for r in rows]

    def prune_activity(self, before: float) -> int:
        with self._lock:
            n = self.db.execute("DELETE FROM activity WHERE hour < ?", (int(before // 3600),)).rowcount
            self.db.commit()
        return n

    # ---------- /vergiss_mich ----------

    def forget_user(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """Löscht alle Fakten über die Person und alle Erinnerungen, an denen sie beteiligt war."""
        with self._lock:
            facts = self.db.execute("DELETE FROM facts WHERE guild_id = ? AND user_id = ?",
                                    (guild_id, user_id)).rowcount
            ids = [r[0] for r in self.db.execute(
                "SELECT DISTINCT memory_id FROM memory_users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)).fetchall()]
            if ids:
                marks = ",".join("?" * len(ids))
                self.db.execute(f"DELETE FROM memory_users WHERE memory_id IN ({marks})", ids)
            self.db.commit()
        if ids and self.collection is not None:
            try:
                self.collection.delete(ids=ids)
            except Exception as e:  # noqa: BLE001
                log.warning("Erinnerungen konnten nicht aus ChromaDB gelöscht werden: %s", e)
        return facts, len(ids)

    def close(self) -> None:
        with self._lock:
            self.db.close()
