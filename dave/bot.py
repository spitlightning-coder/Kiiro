"""Discord-Teil: Nachrichten lesen, entscheiden ob Dave antwortet, Antwort schicken."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path

import discord
from discord import app_commands

from .actions import Actions, RateLimit, role_problem
from .backup import Backups, register_backup
from .community import Events, TempVoice, register_events, register_summary
from .config import ROOT, Config
from .daily import Daily, register_daily
from .extras import (Counting, ensure_roles, norm, parse_groups, parse_setting, refresh_role_message, register_fun,
                     setting_reply, settings_line)
from .learning import Learner, Line, build_context, parse_merk_dir
from .longterm import MemoryStore
from .memory import ChannelMemory
from .freegames import FreeGames
from .games import Hangmen, register_games
from .guard import AntiSpam, HallOfFame, register_guard
from .news import News
from .proactive import Proactive
from .quiz import Quiz, register_quiz
from .quotes import register_quotes
from .serverdata import ServerData
from .tagebuch import register_tagebuch
from .wortel import register_wortel

log = logging.getLogger("dave.bot")

DISCORD_LIMIT = 2000
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
             ".heic": "image/heic", ".heif": "image/heif"}
MAX_IMAGES = 3
DM_PER_HOUR = 40
DM_HINT = ("## Privatchat\n"
           "Du schreibst gerade privat mit {name} (Mitglied von {guild}). Niemand sonst liest mit. "
           "Server-Sachen wie Rollen, Erinnerungen, Events oder Quiz gehen hier nicht – dafür auf den Server "
           "bzw. die Slash-Befehle dort verweisen. Erzähl hier nichts über andere Leute, was die nicht selbst "
           "öffentlich gesagt haben.")
MAX_IMAGE_BYTES = 8_000_000
INVITE = ("https://discord.com/oauth2/authorize?client_id={id}&scope=bot+applications.commands"
          "&permissions=1374674840592")


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


def image_attachments(message: discord.Message, *, include_reply: bool = True) -> list[tuple[discord.Attachment, str]]:
    """Bild-Anhänge der Nachricht (und der Nachricht, auf die sie antwortet), die Dave sich ansehen kann."""
    sources = [message]
    ref = message.reference
    if include_reply and ref and isinstance(ref.resolved, discord.Message):
        sources.append(ref.resolved)
    found = []
    for msg in sources:
        for a in msg.attachments:
            mime = (a.content_type or "").split(";")[0].strip().lower()
            if not mime:
                mime = IMAGE_EXT.get(Path(a.filename).suffix.lower(), "")
            if mime in IMAGE_TYPES and a.size <= MAX_IMAGE_BYTES:
                found.append((a, mime))
    return found[:MAX_IMAGES]


async def load_images(found: list[tuple[discord.Attachment, str]]) -> list[tuple[str, bytes]]:
    images = []
    for attachment, mime in found:
        try:
            images.append((mime, await attachment.read()))
        except discord.HTTPException as e:
            log.warning("Bild %s nicht ladbar: %s", attachment.filename, e)
    return images


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
        self.daily = Daily(self, server, brain, self.actions) if server else None
        self.hof = HallOfFame(self, server, self.actions) if server else None
        self.antispam = AntiSpam(self, server, self.actions) if server else None
        if server:
            register_daily(self.tree, self.daily)
            register_guard(self.tree, server, self.actions)
        self.hangmen = Hangmen()
        register_games(self.tree, self.hangmen)
        if server:
            register_wortel(self.tree, server)
            register_quotes(self.tree, server)
        if server:
            register_quiz(self.tree, Quiz(server, brain, self.actions))
        self.backups = Backups(config.data_dir) if store or server else None
        if self.backups:
            register_backup(self.tree, self.backups)
        self.dm_limit = RateLimit(DM_PER_HOUR)
        self._dm_told: set[tuple[int, str]] = set()
        if server:
            self._register_dm_admin(server)
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
        if self.daily:
            self.daily.start()
        if self.backups:
            self.backups.start()
        if self.config.autoreload:
            asyncio.create_task(self._watch_code())

    # ---------- Selbst-Update: geänderter Code -> sauber beenden, der Dienst startet neu ----------

    @staticmethod
    def _code_stamp() -> dict[str, float]:
        files = list((ROOT / "dave").glob("*.py")) + list((ROOT / "prompts").glob("*.md")) + [ROOT / ".env"]
        return {str(f): f.stat().st_mtime for f in files if f.exists()}

    async def _watch_code(self) -> None:
        stamp = _BOOT_STAMP  # Stand beim Laden des Codes – so geht keine Änderung während des Starts verloren
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

    def _seed_selfroles(self, guild: discord.Guild, extra: list[discord.Role] = ()) -> None:
        """Gibt die Rollen aus DAVE_SELFROLES einmalig frei (danach gelten /dave rolle_freigeben/_sperren)."""
        data, groups = self.actions.data, self.actions.role_groups
        wanted = {norm(n) for _t, names in groups for n in names}
        key = "selfroles_seeded:" + ",".join(sorted(wanted))
        if not wanted or data.get(guild.id, key):
            return
        added = []
        for role in {r.id: r for r in [*guild.roles, *extra]}.values():
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
            if await self.actions.role_button(interaction):
                return
            if self.events and await self.events.button(interaction):
                return
            if self.antispam:
                await self.antispam.button(interaction)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.hof:
            try:
                await self.hof.on_reaction(payload)
            except discord.HTTPException as e:
                log.warning("Hall of Fame: %s", e)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.on_raw_reaction_add(payload)

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
        register_tagebuch(self.tree, store, self.brain)

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
                    created = await ensure_roles(guild, self.actions.data, self.config.selfroles)
                    if created:
                        await asyncio.sleep(2)  # Discord-Cache die neuen Rollen mitbekommen lassen
                    self._seed_selfroles(guild, created)
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
        settings = settings_line(self.actions.data, message.guild.id) if self.actions and message.guild else None
        memory = await self._memory_context(message, history)
        return "\n\n".join(p for p in (settings, memory) if p) or None

    async def _memory_context(self, message: discord.Message, history: list[dict]) -> str | None:
        if not self.store or message.guild is None:
            return None
        people = [message.author] + [m for m in message.mentions if self.user is None or m.id != self.user.id]
        ids = list(dict.fromkeys(p.id for p in people))
        names = {p.id: p.display_name for p in people}
        query = history[-1]["content"][-500:] if history else message.clean_content
        recalled = await asyncio.to_thread(self.store.recall, message.guild.id, query)
        return build_context(self.store, message.guild.id, ids, names, recalled)

    # ---------- Privatchat (Direktnachrichten) ----------

    def _register_dm_admin(self, server: ServerData) -> None:
        admin = next((c for c in self.tree.get_commands() if c.name == "dave"), None)
        if admin is None:
            return

        @admin.command(name="privatchat", description="Ob man Dave per Direktnachricht schreiben kann")
        async def privatchat(interaction: discord.Interaction, an: bool):
            if not interaction.user.guild_permissions.manage_guild:
                await interaction.response.send_message("Das dürfen nur Admins.", ephemeral=True)
                return
            server.set(interaction.guild_id, "dm_on", an)
            await interaction.response.send_message(
                f"Privatchat mit Dave ist jetzt {'an' if an else 'aus'}.", ephemeral=True)
            if self.actions:
                await self.actions.audit(interaction.guild, f"⚙️ Privatchat {'an' if an else 'aus'} von {interaction.user}")

    def home_guild(self, user: discord.abc.User) -> discord.Guild | None:
        """Der Server, über den jemand Dave kennt. Fremde ohne gemeinsamen Server bekommen keine Antwort."""
        return next((g for g in self.guilds if g.get_member(user.id)), None)

    async def _tell_once(self, message: discord.Message, key: str, text: str) -> None:
        if (message.author.id, key) not in self._dm_told:
            self._dm_told.add((message.author.id, key))
            await message.channel.send(text)

    async def _on_dm(self, message: discord.Message) -> None:
        guild = self.home_guild(message.author)
        if guild is None:
            log.info("Privatnachricht von jemandem ohne gemeinsamen Server ignoriert")
            return
        data = self.actions.data if self.actions else None
        if data and (data.stopped(guild.id) or not data.get(guild.id, "dm_on", True)):
            await self._tell_once(message, "aus", "Privatchat ist gerade ausgeschaltet. Schreib mir auf dem Server.")
            return
        if not self.dm_limit.allow((message.author.id,)):
            await self._tell_once(message, "limit", "Kurze Pause, wir haben grad echt viel geschrieben. "
                                                    "In einer Stunde geht's weiter.")
            return
        self._dm_told.discard((message.author.id, "limit"))
        channel_id = message.channel.id
        text = message.content
        own_images = image_attachments(message, include_reply=False)
        if own_images:
            marker = "[schickt ein Bild]" if len(own_images) == 1 else f"[schickt {len(own_images)} Bilder]"
            text = f"{text} {marker}".strip()
        name = getattr(guild.get_member(message.author.id), "display_name", message.author.display_name)
        self.memory.add(channel_id, name, text)
        if self.store and (what := parse_merk_dir(text)):
            self.store.add_fact(guild.id, message.author.id, name, what, source="manuell")
        async with self._locks[channel_id]:
            history = self.memory.as_messages(channel_id)
            if not history:
                return
            async with message.channel.typing():
                images = await load_images(image_attachments(message))
                parts = [settings_line(data, guild.id) if data else None, DM_HINT.format(name=name, guild=guild.name)]
                if self.store:  # nur Fakten über die Person selbst, keine Erinnerungen aus anderen Kanälen
                    parts.append(build_context(self.store, guild.id, [message.author.id],
                                               {message.author.id: name}, []))
                extra = {"images": images} if images else {}
                answer = await self.brain.reply(history, server=guild.name, channel=f"Privatchat mit {name}",
                                                context="\n\n".join(p for p in parts if p), **extra)
            if not answer:
                return
            for chunk in split_message(answer):
                await message.channel.send(chunk)
            self.memory.add(channel_id, "Dave", answer, from_dave=True)
        log.info("Privatchat-Antwort an %s", message.author.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return  # eigene Nachrichten speichern wir direkt beim Senden
        if message.author.bot and not self.config.reply_to_bots:
            return
        if message.guild is None:
            if not message.author.bot:
                await self._on_dm(message)
            return
        if self.actions and self.actions.stopped(message.guild):
            return  # Notbremse: Dave hört weg, bis /dave weiter
        if self.proactive:
            self.proactive.note_activity(message.channel.id)
        if self.antispam:
            await self.antispam.on_message(message)
        if self.counting and await self.counting.handle(message):
            return  # Counting-Kanal: nur zählen, nicht quatschen
        if await self.hangmen.handle(message):
            return  # Galgenmännchen-Rateversuch

        channel_id = message.channel.id
        text = message.clean_content
        # clean_content macht aus <@123> lesbare @Namen
        own_images = image_attachments(message, include_reply=False)
        if own_images:
            marker = "[schickt ein Bild]" if len(own_images) == 1 else f"[schickt {len(own_images)} Bilder]"
            text = f"{text} {marker}".strip()
        self.memory.add(channel_id, message.author.display_name, text)
        addressed = self._is_addressed(message)
        self._note(message, Line(message.author.id, message.author.display_name, text), addressed)

        if not addressed:
            return

        if self.actions and (setting := parse_setting(text)):
            key, value = setting
            self.actions.data.set(message.guild.id, key, value)
            confirmation = setting_reply(key, value)
            await message.reply(confirmation, mention_author=False)
            self.memory.add(channel_id, "Dave", confirmation, from_dave=True)
            await self.actions.audit(message.guild, f"⚙️ {key.capitalize()} auf {value} % gesetzt von {message.author}")
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
                images = await load_images(image_attachments(message))
                context = await self._context(message, history)
                extra = {"images": images} if images else {}
                answer = await self.brain.reply(
                    history,
                    server=message.guild.name,
                    channel=getattr(message.channel, "name", "?"),
                    context=context,
                    **extra,
                )
                if images:
                    log.info("Dave hat sich %d Bild(er) angesehen", len(images))
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


_BOOT_STAMP = Dave._code_stamp()
