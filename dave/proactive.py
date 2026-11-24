"""Proaktives Schreiben: Dave meldet sich ab und zu von selbst – mit festen Grenzen.

- höchstens 1× pro Kanal alle 2 Stunden und 6× pro Server und Tag
- Ruhezeit 23–8 Uhr, nie wenn gerade jemand schreibt (Kanal mind. 30 Min. still)
- nur harmlose Nachrichten, pingt nie jemanden
- Modus: "test" (Standard: Ideen nur in #dave-log), "an", "aus"
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime

import discord

from .actions import TZ
from .brain import WOCHENTAGE

log = logging.getLogger("dave.proactive")

CHECK_EVERY = 15 * 60
PER_CHANNEL_GAP = 2 * 3600
PER_DAY = 6
QUIET_FROM, QUIET_TO = 23, 8
MIN_SILENCE = 30 * 60
CHANCE = 0.35
MODES = ("aus", "test", "an")

IDEAS = [
    "eine lockere Frage an alle, über die man gut quatschen kann",
    "einen kurzen, überraschenden Fakt aus Wissenschaft oder Weltraum mit einer Frage dazu",
    "eine Gaming-Frage (z.B. was gerade gezockt wird oder ein Spiel, das man unterschätzt)",
    "eine kurze Anspielung auf etwas aus deinem Gedächtnis über den Server, falls passend",
    "einen spontanen Gedanken, wie ihn ein Kumpel im Chat schreiben würde",
    "einen Hinweis auf eine aktuelle News oder ein Gratis-Spiel, falls unten eins steht",
]


def in_quiet_hours(now: datetime) -> bool:
    return now.hour >= QUIET_FROM or now.hour < QUIET_TO


def clean_post(text: str | None) -> str | None:
    if not text or text.startswith(("Mein Hirn", "Ich werde gerade", "Dazu sag ich")):
        return None
    text = text.replace("@everyone", "everyone").replace("@here", "here").strip().strip('"').strip()
    if len(text) > 450:
        text = text[:450].rsplit(" ", 1)[0] + " …"
    return text or None


class Proactive:
    def __init__(self, bot: discord.Client, data, brain, actions, store=None):
        self.bot, self.data, self.brain, self.actions, self.store = bot, data, brain, actions, store
        self.last_activity: dict[int, float] = {}  # channel_id -> Zeitpunkt der letzten Nachricht
        self._task: asyncio.Task | None = None

    # ---------- Zustand ----------

    def mode(self, guild_id: int) -> str:
        return self.data.get(guild_id, "proactive_mode", "test")

    def channels(self, guild: discord.Guild) -> list[discord.TextChannel]:
        ids = self.data.get(guild.id, "proactive_channels")
        if ids is None:
            chans = [c for c in guild.text_channels if c.name.endswith("main-chat")]
        else:
            chans = [c for c in (guild.get_channel(i) for i in ids) if isinstance(c, discord.TextChannel)]
        return [c for c in chans if c.permissions_for(guild.me).send_messages]

    def set_channel(self, guild: discord.Guild, channel_id: int, on: bool) -> None:
        ids = {c.id for c in self.channels(guild)}
        ids.add(channel_id) if on else ids.discard(channel_id)
        self.data.set(guild.id, "proactive_channels", sorted(ids))

    def _today(self) -> str:
        return datetime.now(TZ).strftime("%Y-%m-%d")

    def posts_today(self, guild_id: int) -> int:
        day = self.data.get(guild_id, "proactive_day")
        return self.data.get(guild_id, "proactive_count", 0) if day == self._today() else 0

    def note_activity(self, channel_id: int) -> None:
        self.last_activity[channel_id] = time.time()

    def can_post(self, guild: discord.Guild, channel: discord.TextChannel, now: float | None = None,
                 force: bool = False) -> str | None:
        """None = darf, sonst Grund."""
        now = now or time.time()
        if self.actions.stopped(guild):
            return "Notbremse"
        if self.mode(guild.id) == "aus" and not force:
            return "aus"
        if not force and in_quiet_hours(datetime.now(TZ)):
            return "Ruhezeit"
        if self.posts_today(guild.id) >= PER_DAY and not force:
            return "Tageslimit"
        last_post = self.data.get(guild.id, f"proactive_last:{channel.id}", 0)
        if now - last_post < PER_CHANNEL_GAP and not force:
            return "Kanal-Pause"
        if now - self.last_activity.get(channel.id, 0) < MIN_SILENCE and not force:
            return "es wird gerade geschrieben"
        awareness = getattr(self.actions, "awareness", None)
        if awareness and awareness.is_tense(guild.id, channel.id):
            return "Stimmung gerade angespannt"
        return None

    # ---------- Schleife ----------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(120)
        while not self.bot.is_closed():
            for guild in list(self.bot.guilds):
                try:
                    if random.random() < CHANCE:
                        await self.try_post(guild)
                except Exception:  # noqa: BLE001
                    log.exception("Proaktives Schreiben fehlgeschlagen")
            await asyncio.sleep(CHECK_EVERY)

    async def try_post(self, guild: discord.Guild, force: bool = False) -> str:
        chans = self.channels(guild)
        random.shuffle(chans)
        for channel in chans:
            reason = self.can_post(guild, channel, force=force)
            if reason is None:
                return await self.post(guild, channel)
        return "kein passender Kanal gerade (Limits, Ruhezeit oder es wird geschrieben)"

    async def compose(self, guild: discord.Guild, channel: discord.TextChannel) -> str | None:
        idea = random.choice(IDEAS)
        extra = []
        notes = self.store.self_notes(guild.id, 6) if self.store else []
        facts = self.store.server_facts(guild.id, 6) if self.store else []
        if notes or facts:
            extra.append("Aus deinem Gedächtnis: " + "; ".join(notes + [f.fact for f in facts]))
        awareness = getattr(self.actions, "awareness", None)
        if awareness:
            topics = awareness.situation(guild)["topics"][:5]
            if topics:
                extra.append("Worüber auf dem Server gerade geredet wird: " + ", ".join(topics))
        recent = self.data.get(guild.id, "last_headline")
        if recent:
            extra.append(f"Aktuelle Schlagzeile: {recent}")
        now = datetime.now(TZ)
        prompt = (f"System: Es ist {WOCHENTAGE[now.weekday()]}, {now:%H:%M} Uhr und im Kanal #{channel.name} ist es gerade ruhig. "
                  f"Schreib von dir aus eine kurze Nachricht (1-2 Sätze), z.B. {idea}. "
                  "Nichts Heikles, niemanden direkt ansprechen oder anpingen, keine Begrüßung wie 'Hallo zusammen'. "
                  "Schreib nur die Nachricht.\n" + "\n".join(extra))
        text = await self.brain.reply([{"role": "user", "content": prompt}], server=guild.name, channel=channel.name)
        return clean_post(text)

    async def post(self, guild: discord.Guild, channel: discord.TextChannel) -> str:
        text = await self.compose(guild, channel)
        if not text:
            return "mir ist gerade nichts Gutes eingefallen"
        now = time.time()
        self.data.set(guild.id, f"proactive_last:{channel.id}", now)
        self.data.set(guild.id, "proactive_day", self._today())
        self.data.set(guild.id, "proactive_count", self.posts_today(guild.id) + 1)
        if self.mode(guild.id) == "an":
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
            if hasattr(self.bot, "memory"):
                self.bot.memory.add(channel.id, "Dave", text, from_dave=True)
            await self.actions.audit(guild, f"💬 Von sich aus in #{channel.name}: {text}")
            return f"in #{channel.name} geschrieben"
        await self.actions.audit(guild, f"💭 (Testmodus) Würde in #{channel.name} schreiben: {text}")
        return f"Testmodus: Idee für #{channel.name} steht in #dave-log"
