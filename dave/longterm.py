"""Langzeitgedächtnis: SQLite für feste Fakten, ChromaDB für Erinnerungen nach Ähnlichkeit.

- facts:       feste Fakten über Leute (user_id gesetzt) oder den Server (user_id NULL)
- self_notes:  Daves eigene Entwicklung (Meinungen, Insider-Witze, Spitznamen)
- memories:    kurze Gesprächs-Zusammenfassungen, in ChromaDB nach Bedeutung durchsuchbar.
               In SQLite steht nur, wen eine Erinnerung betrifft (für /vergiss_mich).

Fehlt ChromaDB oder klappt es nicht, läuft alles andere trotzdem (nur ohne Ähnlichkeitssuche).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
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
"""


def _key(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .!")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text[:MAX_TEXT]


def default_embedding(model_dir: Path):
    """Chromas Standard-Embedding, aber mit dauerhaftem Modell-Ordner und einmal geladenem Modell.

    Chroma legt das ~80-MB-Modell sonst unter ~/.cache ab – auf dem Server ist das nach jedem Neustart weg.
    Außerdem baut die Standard-Funktion bei jedem Aufruf ein neues Modell-Objekt; hier passiert das nur einmal.
    Der Name bleibt "default", damit bestehende Sammlungen ohne Konflikt weiter funktionieren.
    """
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = Path(model_dir) / ONNXMiniLM_L6_V2.MODEL_NAME

    class CachedDefault(DefaultEmbeddingFunction):
        _model = None
        _model_lock = threading.Lock()

        def __call__(self, input):  # noqa: A002 – Name von Chroma vorgegeben
            with self._model_lock:
                if CachedDefault._model is None:
                    CachedDefault._model = ONNXMiniLM_L6_V2()
            return CachedDefault._model(input)

    return CachedDefault()


@dataclass
class Stats:
    facts: int
    people: int
    server_facts: int
    memories: int
    self_notes: int
    since: float | None


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
            log.info("ChromaDB bereit (%d Erinnerungen)", self.collection.count())
        except Exception as e:  # noqa: BLE001 – Gedächtnis darf Dave nie abstürzen lassen
            log.warning("ChromaDB nicht verfügbar, Erinnerungen nach Ähnlichkeit sind aus: %s", e)
            self.collection = None

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
        return Stats(facts, people, server, self.memory_count(guild_id), notes, since)

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
        try:
            n = self.collection.count()
            if n == 0:
                return []
            res = self.collection.query(query_texts=[query], n_results=min(k, n), where={"guild_id": guild_id})
        except Exception as e:  # noqa: BLE001
            log.warning("Erinnern fehlgeschlagen: %s", e)
            return []
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
