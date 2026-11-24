"""Server-Bewusstsein: Dave weiß, was auf dem Server los ist – ohne Chatverläufe zu speichern.

- Alle 45 Min. holt Dave pro beobachtetem Kanal die neuen Nachrichten und lässt sie kurz zusammenfassen
  (Themen, Stimmung, Insider). Gespeichert wird NUR die Zusammenfassung (SQLite + ChromaDB), nie der Wortlaut.
- Ereignisse (Beitritte, Abgänge, Events, Umfragen, Hall of Fame) landen als kurze Notizen.
- Aktivität: nur Zähler – Nachrichten pro Kanal und Stunde.
- Tageszusammenfassungen älter als 14 Tage werden zu Wochenzusammenfassungen verdichtet.

Beobachtet werden nur Kanäle, die alle (@everyone) lesen dürfen. Admins können Kanäle ausschließen.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import discord

from .actions import TZ
from .brain_openai import clean
from .longterm import MemoryStore, ServerNote

log = logging.getLogger("dave.awareness")

INTERVAL = 45 * 60
FIRST_LOOKBACK = 3 * 3600        # beim allerersten Lauf so weit zurück
MAX_LOOKBACK = 12 * 3600         # nie weiter zurück (z.B. nach langer Pause)
MIN_MESSAGES = 4                 # weniger Nachrichten -> warten, bis mehr zusammenkommt
MAX_PER_CHANNEL = 120
MAX_CHARS_PER_CHANNEL = 4000
MAX_CHANNELS_PER_RUN = 6
COMPACT_AFTER = 14 * 86400
KEEP_WEEKLY = 120 * 86400
KEEP_EVENTS = 60 * 86400
KEEP_ACTIVITY = 30 * 86400
MOOD_VALID = 6 * 3600
SKIP_NAMES = ("counting", "dave-log")
MOODS = ("entspannt", "lebhaft", "albern", "nachdenklich", "angespannt", "traurig", "ruhig")
TENSE = {"angespannt"}

SUMMARY_SYSTEM = """Du bist das Server-Gedächtnis von Dave, einem KI-Mitglied auf einem Discord-Server.
Du bekommst neue Nachrichten aus einem oder mehreren Kanälen. Fasse pro Kanal zusammen, was los war.
Antworte NUR mit JSON in genau diesem Format:
{"kanaele": [{"kanal": "<Kanalname wie angegeben>", "zusammenfassung": "<1-2 kurze Sätze>",
  "themen": ["<Stichwort>", "..."], "stimmung": "<entspannt|lebhaft|albern|nachdenklich|angespannt|traurig|ruhig>",
  "insider": ["<Running Gag oder Insider, falls eindeutig>"]}]}

Regeln:
- Keine Zitate, keine wörtlichen Nachrichten. Nur Themen und grobe Stimmung.
- Namen nur, wenn es für ein Thema wirklich nötig ist (z.B. "X plant einen Spieleabend").
- NIEMALS: Gesundheit, Sexualität, Religion, Politik-Meinungen einzelner, Adressen, Telefonnummern, Passwörter,
  Finanzen, private Konflikte oder Peinliches über einzelne Leute.
- "angespannt" nur, wenn es wirklich Streit oder gereizte Stimmung gibt.
- Höchstens 5 Themen, höchstens 2 Insider pro Kanal. Leere Listen sind okay."""

WEEKLY_SYSTEM = """Du verdichtest mehrere Tageszusammenfassungen eines Discord-Kanals zu EINER Wochenzusammenfassung.
Antworte NUR mit JSON: {"zusammenfassung": "<2-3 kurze Sätze>", "themen": ["<Stichwort>", "..."]}
Keine Zitate, nichts Privates über einzelne Leute. Höchstens 6 Themen."""


def _json(text: str | None) -> dict | None:
    if not text:
        return None
    text = clean(text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _strs(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip()[:60] for v in value if isinstance(v, str) and v.strip()][:limit]


def parse_summaries(text: str | None, names: dict[str, int]) -> list[dict]:
    """JSON der KI -> [{channel_id, text, topics, mood, insider}]. Unbekannte Kanäle werden verworfen."""
    data = _json(text)
    out = []
    for item in (data or {}).get("kanaele") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("kanal", "")).strip().lstrip("#")
        cid = names.get(name) or next((c for n, c in names.items() if n.endswith(name) or name.endswith(n)), None)
        summary = item.get("zusammenfassung")
        if cid is None or not isinstance(summary, str) or not summary.strip():
            continue
        mood = str(item.get("stimmung", "")).strip().lower()
        out.append({"channel_id": cid, "text": summary.strip(), "topics": _strs(item.get("themen"), 5),
                    "mood": mood if mood in MOODS else None, "insider": _strs(item.get("insider"), 2)})
    return out


def week_key(ts: float) -> str:
    y, w, _ = datetime.fromtimestamp(ts, TZ).isocalendar()
    return f"{y}-W{w:02d}"


def peak_hours(rows: list[tuple[int, int]], top: int = 3) -> list[int]:
    """Epoch-Stunden mit Zählern -> die aktivsten Uhrzeiten (0-23, deutsche Zeit)."""
    per_hour: Counter = Counter()
    for hour, count in rows:
        per_hour[datetime.fromtimestamp(hour * 3600, TZ).hour] += count
    return [h for h, _c in per_hour.most_common(top)]


class Awareness:
    def __init__(self, bot: discord.Client, store: MemoryStore, data, brain, clock=time.time):
        self.bot, self.store, self.data, self.brain = bot, store, data, brain
        self._clock = clock
        self._task: asyncio.Task | None = None

    # ---------- Welche Kanäle ----------

    def excluded(self, guild_id: int) -> set[int]:
        return set(self.data.get(guild_id, "observe_off", []))

    def set_observed(self, guild_id: int, channel_id: int, on: bool) -> None:
        off = self.excluded(guild_id)
        off.discard(channel_id) if on else off.add(channel_id)
        self.data.set(guild_id, "observe_off", sorted(off))

    def is_observed(self, channel) -> bool:
        guild = getattr(channel, "guild", None)
        if guild is None or not isinstance(channel, discord.TextChannel):
            return False
        if channel.id in self.excluded(guild.id) or any(channel.name.endswith(n) for n in SKIP_NAMES):
            return False
        public = channel.permissions_for(guild.default_role)
        mine = channel.permissions_for(guild.me)
        return public.view_channel and mine.view_channel and mine.read_message_history

    def observed(self, guild: discord.Guild) -> list[discord.TextChannel]:
        return [c for c in guild.text_channels if self.is_observed(c)]

    # ---------- Laufend mitzählen ----------

    def count(self, message: discord.Message) -> None:
        if message.guild and not message.author.bot and self.is_observed(message.channel):
            self.store.count_activity(message.guild.id, message.channel.id)

    def note_event(self, guild_id: int, text: str, channel_id: int | None = None) -> None:
        """Ereignis merken (läuft im Hintergrund, weil ChromaDB rechnen muss)."""
        async def _save():
            try:
                await asyncio.to_thread(self.store.add_server_note, guild_id, "event", text, channel_id=channel_id)
            except Exception:  # noqa: BLE001
                log.exception("Ereignis nicht gespeichert")
        try:
            asyncio.get_running_loop().create_task(_save())
        except RuntimeError:  # kein laufender Loop (Tests)
            self.store.add_server_note(guild_id, "event", text, channel_id=channel_id)

    # ---------- Was Dave beim Antworten weiß ----------

    def mood(self, guild_id: int, channel_id: int) -> str | None:
        notes = self.store.server_notes(guild_id, kinds=("summary",), channel_id=channel_id,
                                        since=self._clock() - MOOD_VALID, limit=1)
        return notes[0].mood if notes else None

    def is_tense(self, guild_id: int, channel_id: int) -> bool:
        return self.mood(guild_id, channel_id) in TENSE

    async def context(self, guild: discord.Guild, channel_id: int, query: str) -> str | None:
        now = self._clock()
        parts = []
        here = self.store.server_notes(guild.id, kinds=("summary",), channel_id=channel_id, since=now - 86400,
                                       limit=2)
        if here:
            mood = here[0].mood if now - here[0].created_at < MOOD_VALID else None
            line = " ".join(n.text for n in reversed(here))
            parts.append(f"In diesem Kanal zuletzt: {line}" + (f" (Stimmung: {mood})" if mood else ""))
            if mood in TENSE:
                parts.append("Die Stimmung hier ist gerade angespannt: bleib ruhig, freundlich und deeskalierend, "
                             "keine Sticheleien.")
        recalled = await asyncio.to_thread(self.store.recall_server, guild.id, query, 3)
        seen = {n.text for n in here}
        recalled = [r for r in recalled if r not in seen]
        if recalled:
            parts.append("Was sonst auf dem Server Thema war und passen könnte:\n" + "\n".join(f"- {r}" for r in recalled))
        events = self.store.server_notes(guild.id, kinds=("event",), since=now - 2 * 86400, limit=3)
        if events:
            parts.append("Neueste Ereignisse:\n" + "\n".join(f"- {e.text}" for e in events))
        if not parts:
            return None
        return ("## Was auf dem Server los ist\nDas weißt du aus Zusammenfassungen. Greif es auf, wenn es passt "
                "(laufende Themen, Insider), aber zähl es nicht auf.\n" + "\n".join(parts))

    def recent_for_diary(self, guild_id: int, days: int = 3, limit: int = 6) -> list[ServerNote]:
        return self.store.server_notes(guild_id, kinds=("summary", "weekly", "event"),
                                       since=self._clock() - days * 86400, limit=limit)

    # ---------- /dave lage ----------

    def situation(self, guild: discord.Guild) -> dict:
        now = self._clock()
        summaries = self.store.server_notes(guild.id, kinds=("summary",), since=now - 2 * 86400, limit=60)
        topics = Counter(t for n in summaries for t in n.topics)
        moods: dict[int, str] = {}
        for n in summaries:  # neueste zuerst
            if n.channel_id not in moods and n.mood and now - n.created_at < 12 * 3600:
                moods[n.channel_id] = n.mood
        return {
            "topics": [t for t, _c in topics.most_common(8)],
            "active": self.store.activity_by_channel(guild.id, now - 86400)[:5],
            "peak": peak_hours(self.store.activity_hours(guild.id, now - 14 * 86400)),
            "moods": moods,
            "events": self.store.server_notes(guild.id, kinds=("event",), since=now - 7 * 86400, limit=5),
            "last_summary": summaries[0].created_at if summaries else None,
        }

    # ---------- Zusammenfassen ----------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(300)
        while not self.bot.is_closed():
            for guild in list(self.bot.guilds):
                try:
                    await self.run_once(guild)
                    await self.compact(guild)
                except Exception:  # noqa: BLE001 – darf Dave nie lahmlegen
                    log.exception("Server-Bewusstsein: Lauf fehlgeschlagen")
            await asyncio.sleep(INTERVAL)

    async def _collect(self, channel: discord.TextChannel, after: datetime) -> tuple[list[str], datetime | None]:
        lines, newest = [], None
        async for m in channel.history(limit=MAX_PER_CHANNEL, after=after, oldest_first=True):
            newest = m.created_at
            if m.author.bot or not m.content.strip():
                continue
            lines.append(f"{m.author.display_name}: {m.clean_content[:300]}")
        return lines, newest

    async def run_once(self, guild: discord.Guild) -> int:
        """Fasst neue Nachrichten zusammen. Gibt die Zahl der gespeicherten Zusammenfassungen zurück."""
        if self.data.stopped(guild.id):
            return 0
        now = self._clock()
        batch: list[tuple[discord.TextChannel, list[str], datetime]] = []
        for channel in self.observed(guild):
            key = f"aware_last:{channel.id}"
            last = self.data.get(guild.id, key) or now - FIRST_LOOKBACK
            last = max(last, now - MAX_LOOKBACK)
            try:
                lines, newest = await self._collect(channel, datetime.fromtimestamp(last, timezone.utc))
            except discord.HTTPException as e:
                log.warning("Verlauf von #%s nicht lesbar: %s", channel.name, e)
                continue
            if len(lines) < MIN_MESSAGES:
                continue  # wartet, bis mehr los war (alte Sachen fallen nach MAX_LOOKBACK raus)
            batch.append((channel, lines, newest))
            if len(batch) >= MAX_CHANNELS_PER_RUN:
                break
        if not batch:
            return 0
        names = {c.name: c.id for c, _l, _n in batch}
        prompt = "\n\n".join(f"### Kanal: {c.name} ({len(lines)} Nachrichten)\n"
                             + "\n".join(lines)[-MAX_CHARS_PER_CHANNEL:] for c, lines, _n in batch)
        raw = await self.brain.complete(SUMMARY_SYSTEM, prompt, max_tokens=900)
        results = parse_summaries(raw, names)
        if not results:
            log.warning("Server-Bewusstsein: keine brauchbare Zusammenfassung bekommen")
            return 0
        done = set()
        for r in results:
            text = r["text"] + (f" Insider: {'; '.join(r['insider'])}." if r["insider"] else "")
            await asyncio.to_thread(self.store.add_server_note, guild.id, "summary", text,
                                    channel_id=r["channel_id"], mood=r["mood"], topics=r["topics"])
            done.add(r["channel_id"])
        for channel, _lines, newest in batch:
            if channel.id in done and newest:
                self.data.set(guild.id, f"aware_last:{channel.id}", newest.timestamp())
        log.info("Server-Bewusstsein: %d Kanal-Zusammenfassung(en) gespeichert", len(done))
        return len(done)

    async def compact(self, guild: discord.Guild) -> int:
        """Einmal am Tag: alte Tageszusammenfassungen -> Wochenzusammenfassungen, Altes aufräumen."""
        now = self._clock()
        today = datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")
        if self.data.get(guild.id, "aware_compacted") == today:
            return 0
        old = self.store.server_notes(guild.id, kinds=("summary",), until=now - COMPACT_AFTER, limit=500)
        groups: dict[tuple, list[ServerNote]] = defaultdict(list)
        for n in old:
            groups[(n.channel_id, week_key(n.created_at))].append(n)
        made = 0
        for (channel_id, week), notes in groups.items():
            notes.sort(key=lambda n: n.created_at)
            text = "\n".join(f"- {n.text} (Themen: {', '.join(n.topics)})" for n in notes)
            data = _json(await self.brain.complete(WEEKLY_SYSTEM, f"Woche {week}:\n{text}", max_tokens=400))
            if not data or not isinstance(data.get("zusammenfassung"), str):
                continue  # nächster Versuch morgen
            channel = guild.get_channel(channel_id) if channel_id else None
            label = f"Woche {week}" + (f" in #{channel.name}" if channel else "")
            await asyncio.to_thread(self.store.add_server_note, guild.id, "weekly",
                                    f"{label}: {data['zusammenfassung'].strip()}", channel_id=channel_id,
                                    topics=_strs(data.get("themen"), 6), created_at=notes[-1].created_at)
            await asyncio.to_thread(self.store.delete_server_notes, [n.id for n in notes])
            made += 1
        expired = [n.id for n in self.store.server_notes(guild.id, kinds=("weekly",), until=now - KEEP_WEEKLY,
                                                         limit=500)]
        expired += [n.id for n in self.store.server_notes(guild.id, kinds=("event",), until=now - KEEP_EVENTS,
                                                          limit=500)]
        if expired:
            await asyncio.to_thread(self.store.delete_server_notes, expired)
        self.store.prune_activity(now - KEEP_ACTIVITY)
        self.data.set(guild.id, "aware_compacted", today)
        if made:
            log.info("Server-Bewusstsein: %d Wochenzusammenfassung(en) verdichtet", made)
        return made


def situation_embed(guild: discord.Guild, s: dict) -> discord.Embed:
    e = discord.Embed(title="🛰️ Lagebericht", color=0x3498DB,
                      description="Was ich gerade über den Server weiß – nur aus Zusammenfassungen und Zählern.")
    e.add_field(name="🔥 Themen (2 Tage)", value=", ".join(s["topics"]) or "noch nichts gesammelt", inline=False)
    active = [f"<#{cid}> · {n}" for cid, n in s["active"]]
    e.add_field(name="📊 Aktivste Kanäle (24 Std.)", value="\n".join(active) or "noch keine Zahlen", inline=True)
    peak = ", ".join(f"{h}–{(h + 1) % 24} Uhr" for h in s["peak"])
    e.add_field(name="⏰ Hauptzeit (14 Tage)", value=peak or "noch keine Zahlen", inline=True)
    moods = [f"<#{cid}>: {m}" for cid, m in s["moods"].items()]
    e.add_field(name="🌡️ Stimmung", value="\n".join(moods) or "keine aktuelle Einschätzung", inline=False)
    events = [f"• {n.text}" for n in s["events"]]
    e.add_field(name="📌 Letzte Ereignisse", value="\n".join(events)[:1024] or "nichts Besonderes", inline=False)
    if s["last_summary"]:
        e.set_footer(text=f"Letzte Zusammenfassung: {datetime.fromtimestamp(s['last_summary'], TZ):%d.%m. %H:%M}")
    return e


def register_awareness(tree, awareness: Awareness, actions) -> None:
    admin = next((c for c in tree.get_commands() if c.name == "dave"), None)
    if admin is None:
        return

    async def is_admin(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("Das dürfen nur Admins.", ephemeral=True)
        return False

    @admin.command(name="lage", description="Was Dave gerade über den Server weiß: Themen, Aktivität, Stimmung")
    async def lage(interaction: discord.Interaction):
        if not await is_admin(interaction):
            return
        await interaction.response.send_message(embed=situation_embed(interaction.guild,
                                                                      awareness.situation(interaction.guild)),
                                                ephemeral=True)

    @admin.command(name="beobachten_aus", description="Dave fasst diesen Kanal nicht mehr zusammen und zählt ihn nicht")
    async def beobachten_aus(interaction: discord.Interaction, kanal: discord.TextChannel):
        if not await is_admin(interaction):
            return
        awareness.set_observed(interaction.guild_id, kanal.id, False)
        await interaction.response.send_message(f"{kanal.mention} wird nicht mehr beobachtet.", ephemeral=True)
        await actions.audit(interaction.guild, f"👁️ #{kanal.name} nicht mehr beobachtet ({interaction.user})")

    @admin.command(name="beobachten_an", description="Kanal wieder beobachten (nur öffentliche Kanäle)")
    async def beobachten_an(interaction: discord.Interaction, kanal: discord.TextChannel):
        if not await is_admin(interaction):
            return
        awareness.set_observed(interaction.guild_id, kanal.id, True)
        ok = awareness.is_observed(kanal)
        await interaction.response.send_message(
            f"{kanal.mention} wird beobachtet." if ok else
            f"{kanal.mention} ist freigegeben, aber nicht öffentlich (oder ich darf den Verlauf nicht lesen) – "
            "private Kanäle beobachte ich grundsätzlich nicht.", ephemeral=True)
        await actions.audit(interaction.guild, f"👁️ #{kanal.name} wieder beobachtet ({interaction.user})")
