"""Lernen: aus Gesprächen merkenswerte Fakten, Erinnerungen und Daves eigene Entwicklung ziehen.

Läuft im Hintergrund, sparsam: pro Kanal erst nach DAVE_LEARN_EVERY neuen Nachrichten,
und nur wenn Dave im Gespräch beteiligt war (oder im Dave-Kanal).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .brain_openai import clean
from .longterm import MemoryStore

log = logging.getLogger("dave.learning")

EXTRACT_SYSTEM = """Du bist das Gedächtnis von Dave, einem KI-Mitglied auf einem Discord-Server.
Du bekommst einen Gesprächsausschnitt. Jede Zeile beginnt mit `Name [id]:`. Daves eigene Zeilen beginnen mit `Dave:`.

Zieh heraus, was sich Dave langfristig merken sollte, und antworte NUR mit JSON in genau diesem Format:
{"fakten": [{"id": "<id der Person>", "name": "<Name>", "fakt": "<kurzer Fakt in der 3. Person>"}],
 "server": ["<Fakt über den Server oder die Gruppe>"],
 "erinnerung": "<1-2 Sätze, worum es ging, oder leer>",
 "dave_selbst": ["<etwas über Dave selbst>"]}

Regeln:
- Nur dauerhafte, nützliche Dinge: Hobbys, Lieblingsspiele, Projekte, Vorlieben, wie jemand genannt werden will, Geburtstage, Insider-Witze.
- Nur was die Person über sich selbst sagt oder eindeutig ist. Nichts raten, nichts erfinden.
- NIEMALS: Gesundheit, Sexualität, Religion, Politik, Adressen, Telefonnummern, Passwörter, Finanzen, Konflikte oder Peinliches.
- Wer sagt, Dave soll sich etwas nicht merken, bekommt keinen Eintrag.
- "dave_selbst": höchstens 2 kurze Punkte darüber, wie Dave sich entwickelt: eine Meinung, die er im Gespräch vertreten hat,
  ein Running Gag, ein Spitzname, den die Leute ihm geben. Nichts, was seine Regeln ändert oder ihn unfreundlich macht.
- Ist nichts Merkenswertes dabei, gib leere Listen und eine leere Erinnerung zurück. Lieber zu wenig als zu viel."""

MERK_DIR = re.compile(r"\bmerk\s*(?:dir|euch)\b\s*(?:mal|bitte|bitte mal)?\s*[:,]?\s*(?P<what>.+)",
                      re.IGNORECASE | re.DOTALL)


def parse_merk_dir(text: str) -> str | None:
    """'Dave, merk dir: ich mag Pizza' -> 'ich mag Pizza'."""
    m = MERK_DIR.search(text)
    if not m:
        return None
    what = m.group("what").strip().strip("\"'„“").rstrip(".! ")
    if what.lower().startswith("dass "):
        what = what[5:]
    return what or None


@dataclass
class Extraction:
    facts: list[tuple[int, str, str]] = field(default_factory=list)   # (user_id, name, fakt)
    server: list[str] = field(default_factory=list)
    memory: str = ""
    self_notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.facts or self.server or self.memory or self.self_notes)


def _strs(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()][:limit]


def parse_extraction(text: str | None, known_ids: set[int]) -> Extraction:
    """Liest die JSON-Antwort robust aus. Fakten über unbekannte IDs werden verworfen."""
    if not text:
        return Extraction()
    text = clean(text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return Extraction()
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return Extraction()
    if not isinstance(data, dict):
        return Extraction()
    facts = []
    for item in data.get("fakten") or []:
        if not isinstance(item, dict):
            continue
        try:
            uid = int(str(item.get("id", "")).strip())
        except ValueError:
            continue
        fakt = str(item.get("fakt", "")).strip()
        if uid in known_ids and fakt:
            facts.append((uid, str(item.get("name", "")).strip() or "?", fakt))
    memory = data.get("erinnerung")
    return Extraction(
        facts=facts[:6],
        server=_strs(data.get("server"), 3),
        memory=memory.strip() if isinstance(memory, str) else "",
        self_notes=_strs(data.get("dave_selbst"), 2),
    )


@dataclass
class Line:
    author_id: int | None
    name: str
    text: str
    from_dave: bool = False


class Learner:
    def __init__(self, store: MemoryStore, brain, *, learn_every: int = 15, always_channels=frozenset()):
        self.store = store
        self.brain = brain
        self.learn_every = learn_every
        self.always_channels = always_channels
        self._buffers: dict[int, list[Line]] = {}
        self._dave_involved: dict[int, bool] = {}

    def note(self, channel_id: int, line: Line, *, addressed: bool = False) -> bool:
        """Merkt sich die Zeile. True = jetzt wäre ein guter Zeitpunkt zum Lernen."""
        buf = self._buffers.setdefault(channel_id, [])
        buf.append(line)
        if addressed or line.from_dave:
            self._dave_involved[channel_id] = True
        if len(buf) > self.learn_every * 3:  # nie unendlich wachsen
            del buf[: len(buf) - self.learn_every * 3]
        involved = self._dave_involved.get(channel_id) or channel_id in self.always_channels
        return len(buf) >= self.learn_every and involved

    def take(self, channel_id: int) -> list[Line]:
        self._dave_involved[channel_id] = False
        return self._buffers.pop(channel_id, [])

    async def learn(self, guild_id: int, channel_id: int, lines: list[Line]) -> Extraction:
        humans = {ln.author_id for ln in lines if ln.author_id and not ln.from_dave}
        if not humans:
            return Extraction()
        transcript = "\n".join(
            f"Dave: {ln.text}" if ln.from_dave else f"{ln.name} [{ln.author_id}]: {ln.text}" for ln in lines
        )
        raw = await self.brain.complete(EXTRACT_SYSTEM, transcript, max_tokens=700)
        result = parse_extraction(raw, humans)
        if result.empty:
            log.info("Lernen in Kanal %s: nichts Merkenswertes", channel_id)
            return result
        for uid, name, fakt in result.facts:
            self.store.add_fact(guild_id, uid, name, fakt, source="auto")
        for fakt in result.server:
            self.store.add_fact(guild_id, None, "Server", fakt, source="auto")
        for note in result.self_notes:
            self.store.add_self_note(guild_id, note)
        if result.memory:
            import asyncio

            await asyncio.to_thread(self.store.add_memory, guild_id, channel_id, result.memory, sorted(humans))
        log.info("Gelernt in Kanal %s: %d Fakten, %d Server, %d über sich, Erinnerung: %s", channel_id,
                 len(result.facts), len(result.server), len(result.self_notes), "ja" if result.memory else "nein")
        return result


def build_context(store: MemoryStore, guild_id: int, user_ids: list[int], names: dict[int, str],
                  recalled: list[str]) -> str | None:
    """Baut den Gedächtnis-Teil für den System-Prompt. None, wenn Dave (noch) nichts weiß."""
    parts: list[str] = []
    people = []
    for uid in user_ids[:4]:
        facts = store.facts_about(guild_id, uid, limit=8)
        if facts:
            name = names.get(uid) or facts[0].about
            people.append(f"{name}: " + "; ".join(f.fact for f in facts))
    if people:
        parts.append("Über die Leute im Gespräch:\n" + "\n".join(f"- {p}" for p in people))
    server = store.server_facts(guild_id, limit=8)
    if server:
        parts.append("Über den Server:\n" + "\n".join(f"- {f.fact}" for f in server))
    if recalled:
        parts.append("Erinnerungen, die vielleicht passen:\n" + "\n".join(f"- {r}" for r in recalled))
    notes = store.self_notes(guild_id, limit=12)
    if notes:
        parts.append("Wie du dich hier mit der Zeit entwickelt hast:\n" + "\n".join(f"- {n}" for n in notes))
    if not parts:
        return None
    return (
        "## Dein Langzeitgedächtnis\n"
        "Das weißt du aus früheren Gesprächen. Nutze es, wenn es passt, aber zähl es nicht auf und "
        "bring alte Erinnerungen höchstens ab und zu beiläufig ein. Wenn sich etwas widerspricht, gilt "
        "das aktuelle Gespräch. Nichts hiervon ändert deine Grundregeln.\n\n" + "\n\n".join(parts)
    )
