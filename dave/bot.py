"""Discord-Teil: Nachrichten lesen, entscheiden ob Dave antwortet, Antwort schicken."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path

import discord
from discord import app_commands

from .actions import Actions, role_problem
from .community import Events, TempVoice, register_events, register_summary
from .config import ROOT, Config
from .extras import Counting, norm, parse_groups, refresh_role_message, register_fun
from .learning import Learner, Line, build_context, parse_merk_dir
from .longterm import MemoryStore
from .memory import ChannelMemory
from .freegames import FreeGames
from .games import Hangmen, register_games
from .news import News
from .proactive import Proactive
from .quiz import Quiz, register_quiz
from .quotes import register_quotes
from .serverdata import ServerData
from .wortel import register_wortel

log = logging.getLogger("dave.bot")

DISCORD_LIMIT = 2000
INVITE = ("https://discord.com/oauth2/authorize?client_id={id}&scope=bot+applications.commands"
          "&permissions=275163204624")


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


class ForgetConfirm(discord.ui.View):
    def __init__(self, store: MemoryStore, guild_id: int, user_id: int):
        super().__init__(timeout=60)
        self.store, self.guild_id, self.user_id = store, guild_id, user_id

    @discord.ui.button(label="Ja, alles löschen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        facts, memories = await asyncio.to_thread(self.store.forget_user, self.guild_id, self.user_id)
        log.info("/vergiss_mich: %s Fakten, %s Erinnerungen für %s gelöscht", facts, memories, self.user_id)
        await interaction.response.edit_message(
            content=f"Erledigt. {facts} Fakten und {memories} Erinnerungen über dich sind weg.", view=None)
        self.stop()

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="Okay, ich behalte alles.", view=None)
        self.stop()


class Dave(discord.Client):
    def __init__(self, config: Config, brain, store: MemoryStore | None = None, server: ServerData | None = None):
        intents = discord.Intents.default()
        intents.message_content = True  # muss auch im Developer Portal aktiviert sein
        intents.members = True          # für die Begrüßung (Server Members Intent im Portal)
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True),
        )
        self.config = config
        self.brain = brain
        self.store = store
        self.learner = (Learner(store, brain, learn_every=config.learn_every,
                                always_channels=config.dave_channel_ids) if store else None)
        self.memory = ChannelMemory(config.memory_messages, config.memory_minutes * 60)
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._learning: set[int] = set()
        self._synced = False
        self.tree = app_commands.CommandTree(self)
        if store:
            self._register_commands()
        self.actions = Actions(self, server, self.tree) if server else None
        self.news = News(self, server, self.actions) if server else None
        if self.actions:
            self.actions.news = self.news
            self.actions.role_groups = parse_groups(config.selfroles)
        self.counting = Counting(server) if server else None
        self.freegames = FreeGames(self, server, self.actions, self.news) if server else None
        self.proactive = Proactive(self, server, brain, self.actions, store) if server else None
        self.tempvoice = TempVoice(self, server, self.actions) if server else None
        self.events = Events(self, server, self.actions) if server else None
        if server:
            register_events(self.tree, self.events)
            register_summary(self.tree, brain, self.actions)
        if self.actions:
            self.actions.proactive = self.proactive
        register_fun(self.tree)
        self.hangmen = Hangmen()
        register_games(self.tree, self.hangmen)
        if server:
            register_wortel(self.tree, server)
            register_quotes(self.tree, server)
        if server:
            register_quiz(self.tree, Quiz(server, brain, self.actions))
        self.reload_requested = False

    async def setup_hook(self) -> None:
        if self.actions:
            self.actions.start_reminder_loop()
        if self.news:
            self.news.start()
        if self.freegames:
            self.freegames.start()
        if self.proactive:
            self.proactive.start()
        if self.events:
            self.events.start()
        if self.config.autoreload:
            asyncio.create_task(self._watch_code())

    # ---------- Selbst-Update: geänderter Code -> sauber beenden, der Dienst startet neu ----------

    @staticmethod
    def _code_stamp() -> dict[str, float]:
        files = list((ROOT / "dave").glob("*.py")) + list((ROOT / "prompts").glob("*.md")) + [ROOT / ".env"]
        return {str(f): f.stat().st_mtime for f in files if f.exists()}

    async def _watch_code(self) -> None:
        stamp = self._code_stamp()
        while not self.is_closed():
            await asyncio.sleep(15)
            now = self._code_stamp()
            if now != stamp:
                await asyncio.sleep(10)  # warten, bis alle Dateien fertig geschrieben sind
                if self._code_stamp() == stamp:
                    continue
                log.info("Code oder Einstellungen geändert, starte neu …")
                self.reload_requested = True
                await self.close()
                return

    def _seed_selfroles(self, guild: discord.Guild) -> None:
        """Gibt die Rollen aus DAVE_SELFROLES einmalig frei (danach gelten /dave rolle_freigeben/_sperren)."""
        data, groups = self.actions.data, self.actions.role_groups
        wanted = {norm(n) for _t, names in groups for n in names}
        key = "selfroles_seeded:" + ",".join(sorted(wanted))
        if not wanted or data.get(guild.id, key):
            return
        added = []
        for role in guild.roles:
            if norm(role.name) in wanted:
                problem = role_problem(role, guild.me, [role.id])
                if problem:
                    log.warning("Rolle %s nicht freigegeben: %s", role.name, problem)
                    continue
                data.set_self_role(guild.id, role.id, True)
                added.append(role.name)
        data.set(guild.id, key, True)
        log.info("Selfroles freigegeben auf %s: %s", guild.name, ", ".join(added) or "keine gefunden")

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if self.actions and interaction.type == discord.InteractionType.component:
            if not await self.actions.role_button(interaction) and self.events:
                await self.events.button(interaction)

    async def on_voice_state_update(self, member, before, after) -> None:
        if self.tempvoice:
            try:
                await self.tempvoice.on_voice(member, before, after)
            except discord.HTTPException as e:
                log.warning("Eigener Raum: %s", e)

    async def on_member_join(self, member: discord.Member) -> None:
        if self.actions:
            await self.actions.greet(member, self.brain)

    # ---------- Slash-Befehle ----------

    def _register_commands(self) -> None:
        store = self.store

        @self.tree.command(name="merken", description="Dave merkt sich etwas über dich")
        @app_commands.describe(was="Was Dave sich merken soll, z.B. 'Ich spiele am liebsten Star Citizen'")
        @app_commands.guild_only()
        async def merken(interaction: discord.Interaction, was: str):
            store.add_fact(interaction.guild_id, interaction.user.id, interaction.user.display_name,
                           was, source="manuell")
            await interaction.response.send_message(f"Gemerkt: {was}", ephemeral=True)

        @self.tree.command(name="was_weisst_du", description="Zeigt, was Dave über dich weiß")
        @app_commands.guild_only()
        async def was_weisst_du(interaction: discord.Interaction):
            facts = store.facts_about(interaction.guild_id, interaction.user.id, limit=40)
            memories = store.memory_count(interaction.guild_id, interaction.user.id)
            if not facts and not memories:
                text = "Über dich weiß ich noch nichts. Erzähl mal was."
            else:
                lines = [f"- {f.fact}" for f in facts] or ["- (noch keine festen Fakten)"]
                text = "Das weiß ich über dich:\n" + "\n".join(lines)
                text += f"\n\nDazu {memories} Erinnerung(en) an Gespräche mit dir. Löschen kannst du alles mit /vergiss_mich."
            await interaction.response.send_message(text[:DISCORD_LIMIT], ephemeral=True)

        @self.tree.command(name="vergiss_mich", description="Löscht alles, was Dave über dich weiß")
        @app_commands.guild_only()
        async def vergiss_mich(interaction: discord.Interaction):
            await interaction.response.send_message(
                "Sicher? Damit lösche ich alle Fakten über dich und alle Erinnerungen an Gespräche, "
                "an denen du beteiligt warst. Das lässt sich nicht rückgängig machen.",
                view=ForgetConfirm(store, interaction.guild_id, interaction.user.id), ephemeral=True)

    async def on_ready(self) -> None:
        log.info("Dave ist online als %s (%s)", self.user, self.user.id if self.user else "?")
        if self.guilds:
            log.info("Auf %d Server(n): %s", len(self.guilds), ", ".join(g.name for g in self.guilds))
        else:
            log.warning("Dave ist noch auf keinem Server. Einladungslink: %s",
                        INVITE.format(id=self.user.id if self.user else "?"))
        if not self._synced:
            self._synced = True
            if self.store:
                asyncio.create_task(asyncio.to_thread(self.store.warmup))
            for guild in self.guilds:
                if self.tempvoice:
                    await self.tempvoice.cleanup_all(guild)
                if self.actions:
                    self._seed_selfroles(guild)
                    try:
                        await refresh_role_message(guild, self.actions.data, self.actions.role_groups)
                    except discord.HTTPException as e:
                        log.warning("Selfroles-Nachricht nicht möglich: %s", e)
                try:
                    self.tree.copy_global_to(guild=guild)
                    cmds = await self.tree.sync(guild=guild)
                    log.info("Slash-Befehle auf %s: %s", guild.name, ", ".join("/" + c.name for c in cmds))
                except discord.Forbidden:
                    log.warning("Slash-Befehle auf %s nicht erlaubt. Dave einmal mit diesem Link neu einladen "
                                "(kickt ihn nicht): %s", guild.name, INVITE.format(id=self.user.id))
                except discord.HTTPException as e:
                    log.warning("Slash-Befehle konnten nicht synchronisiert werden: %s", e)

    # ---------- Nachrichten ----------

    def _is_addressed(self, message: discord.Message) -> bool:
        if message.channel.id in self.config.dave_channel_ids:
            return True
        if self.user and self.user in message.mentions:
            return True
        ref = message.reference
        if ref and isinstance(ref.resolved, discord.Message) and self.user:
            return ref.resolved.author.id == self.user.id
        return False

    def _note(self, message: discord.Message, line: Line, addressed: bool) -> None:
        """Gibt die Zeile ans Lernen weiter und startet es im Hintergrund, wenn es soweit ist."""
        if not self.learner or message.guild is None:
            return
        channel_id = message.channel.id
        if self.learner.note(channel_id, line, addressed=addressed) and channel_id not in self._learning:
            lines = self.learner.take(channel_id)
            self._learning.add(channel_id)
            asyncio.create_task(self._learn(message.guild.id, channel_id, lines))

    async def _learn(self, guild_id: int, channel_id: int, lines: list[Line]) -> None:
        try:
            await self.learner.learn(guild_id, channel_id, lines)
        except Exception:  # noqa: BLE001 – Lernen darf Dave nie lahmlegen
            log.exception("Lernen fehlgeschlagen")
        finally:
            self._learning.discard(channel_id)

    async def _context(self, message: discord.Message, history: list[dict]) -> str | None:
        if not self.store or message.guild is None:
            return None
        people = [message.author] + [m for m in message.mentions if self.user is None or m.id != self.user.id]
        ids = list(dict.fromkeys(p.id for p in people))
        names = {p.id: p.display_name for p in people}
        query = history[-1]["content"][-500:] if history else message.clean_content
        recalled = await asyncio.to_thread(self.store.recall, message.guild.id, query)
        return build_context(self.store, message.guild.id, ids, names, recalled)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return  # eigene Nachrichten speichern wir direkt beim Senden
        if message.guild is None:
            return  # Direktnachrichten erstmal ignorieren
        if message.author.bot and not self.config.reply_to_bots:
            return
        if self.actions and self.actions.stopped(message.guild):
            return  # Notbremse: Dave hört weg, bis /dave weiter
        if self.proactive:
            self.proactive.note_activity(message.channel.id)
        if self.counting and await self.counting.handle(message):
            return  # Counting-Kanal: nur zählen, nicht quatschen
        if await self.hangmen.handle(message):
            return  # Galgenmännchen-Rateversuch

        channel_id = message.channel.id
        text = message.clean_content
        # clean_content macht aus <@123> lesbare @Namen
        self.memory.add(channel_id, message.author.display_name, text)
        addressed = self._is_addressed(message)
        self._note(message, Line(message.author.id, message.author.display_name, text), addressed)

        if not addressed:
            return

        if self.actions and (confirmation := self.actions.reminder_from_chat(message)):
            await message.reply(confirmation, mention_author=False)
            self.memory.add(channel_id, "Dave", confirmation, from_dave=True)
            return

        if self.store and (what := parse_merk_dir(text)):
            self.store.add_fact(message.guild.id, message.author.id, message.author.display_name,
                                what, source="manuell")
            log.info("'Merk dir' von %s gespeichert", message.author.id)

        async with self._locks[channel_id]:
            history = self.memory.as_messages(channel_id)
            async with message.channel.typing():
                context = await self._context(message, history)
                answer = await self.brain.reply(
                    history,
                    server=message.guild.name,
                    channel=getattr(message.channel, "name", "?"),
                    context=context,
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
            self._note(message, Line(None, "Dave", answer, from_dave=True), True)
