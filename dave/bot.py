"""Discord-Teil: Nachrichten lesen, entscheiden ob Dave antwortet, Antwort schicken."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import discord

from .brain import Brain
from .config import Config
from .memory import ChannelMemory

log = logging.getLogger("dave.bot")

DISCORD_LIMIT = 2000


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Teilt lange Antworten an Absätzen/Zeilen/Wörtern, damit Discord sie annimmt."""
    parts: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        cut = -1
        for sep in ("\n\n", "\n", " "):
            cut = rest.rfind(sep, 0, limit)
            if cut > limit // 2:
                break
        if cut <= 0:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts


class Dave(discord.Client):
    def __init__(self, config: Config, brain: Brain):
        intents = discord.Intents.default()
        intents.message_content = True  # muss auch im Developer Portal aktiviert sein
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True),
        )
        self.config = config
        self.brain = brain
        self.memory = ChannelMemory(config.memory_messages, config.memory_minutes * 60)
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def on_ready(self) -> None:
        log.info("Dave ist online als %s (%s)", self.user, self.user.id if self.user else "?")
        if self.guilds:
            log.info("Auf %d Server(n): %s", len(self.guilds), ", ".join(g.name for g in self.guilds))
        else:
            log.warning("Dave ist noch auf keinem Server. Einladungslink: "
                        "https://discord.com/oauth2/authorize?client_id=%s&scope=bot+applications.commands"
                        "&permissions=274877975552", self.user.id if self.user else "?")

    def _is_addressed(self, message: discord.Message) -> bool:
        if message.channel.id in self.config.dave_channel_ids:
            return True
        if self.user and self.user in message.mentions:
            return True
        ref = message.reference
        if ref and isinstance(ref.resolved, discord.Message) and self.user:
            return ref.resolved.author.id == self.user.id
        return False

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return  # eigene Nachrichten speichern wir direkt beim Senden
        if message.guild is None:
            return  # Direktnachrichten erstmal ignorieren
        if message.author.bot and not self.config.reply_to_bots:
            return

        channel_id = message.channel.id
        # clean_content macht aus <@123> lesbare @Namen
        self.memory.add(channel_id, message.author.display_name, message.clean_content)

        if not self._is_addressed(message):
            return

        async with self._locks[channel_id]:
            history = self.memory.as_messages(channel_id)
            async with message.channel.typing():
                answer = await self.brain.reply(
                    history,
                    server=message.guild.name,
                    channel=getattr(message.channel, "name", "?"),
                )
            if not answer:
                return
            first = True
            for chunk in split_message(answer):
                if first:
                    await message.reply(chunk, mention_author=False)
                    first = False
                else:
                    await message.channel.send(chunk)
            self.memory.add(channel_id, "Dave", answer, from_dave=True)
