"""Discord-Teil: Nachrichten lesen, entscheiden ob Dave antwortet, Antwort schicken."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import discord
from discord import app_commands

from .actions import Actions, RateLimit, role_problem
from .awareness import Awareness, register_awareness
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
from .sprache import REPHRASE, SWITCH_INFO, is_confused, length_hint, register_stil, style_hint
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
MAX_IMAGE_BYTES = 15_000_000   # Original direkt laden höchstens bis hierhin
DIRECT_MAX_BYTES = 3_000_000   # größer -> verkleinert über den Discord-Medienproxy
MAX_IMAGE_SIDE = 1600
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
            if mime in IMAGE_TYPES:
                found.append((a, mime))
            else:
                log.info("Anhang %s (%s) ist kein Bild, das ich lesen kann", a.filename, mime or "?")
    return found[:MAX_IMAGES]


def resized_url(attachment: discord.Attachment, limit: int = MAX_IMAGE_SIDE) -> str | None:
    """Discord-Medienproxy liefert das Bild verkleinert als WebP – große Screenshots werden so klein und schnell."""
    w, h = attachment.width or 0, attachment.height or 0
    if not w or not h or (max(w, h) <= limit and attachment.size <= DIRECT_MAX_BYTES):
        return None
    scale = min(1.0, limit / max(w, h))
    url = attachment.proxy_url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}width={max(1, round(w * scale))}&height={max(1, round(h * scale))}&format=webp"


async def load_images(found: list[tuple[discord.Attachment, str]], http=None) -> list[tuple[str, bytes]]:
    images = []
    for attachment, mime in found:
        url = resized_url(attachment) if http is not None else None
        try:
            if url:
                images.append(("image/webp", await http.get_from_cdn(url)))
                continue
        except discord.HTTPException as e:
            log.warning("Verkleinertes Bild %s nicht ladbar (%s), versuche das Original", attachment.filename, e)
        if attachment.size > MAX_IMAGE_BYTES:
            log.warning("Bild %s ist zu groß (%d MB)", attachment.filename, attachment.size // 1_000_000)
            continue
        try:
            images.append((mime, await attachment.read()))
        except discord.HTTPException as e:
            log.warning("Bild %s nicht ladbar: %s", attachment.filename, e)
    return images


_PICTURE_TALK = re.compile(r"\b(bild|bilder|foto|screenshot|screen|pic|meme|anschauen|ansehen|angucken|schau|guck)",
                           re.IGNORECASE)


async def recent_images(message: discord.Message, seconds: int = 300) -> list[tuple[discord.Attachment, str]]:
    """'@Dave was sagst du zu dem Bild?' – das Bild steckt oft in der Nachricht davor."""
    if not _PICTURE_TALK.search(message.content or ""):
        return []
    try:
        async for older in message.channel.history(limit=8, before=message):
            if (message.created_at - older.created_at).total_seconds() > seconds:
                break
            if older.author.id == message.author.id:
                found = image_attachments(older, include_reply=False)
                if found:
                    return found
    except discord.HTTPException as e:
        log.warning("Ältere Nachrichten nicht lesbar: %s", e)
    return []


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
        self.awareness = Awareness(self, store, server, brain) if store and server else None
        if self.actions:
            self.actions.awareness = self.awareness
        if self.awareness:
            register_awareness(self.tree, self.awareness, self.actions)
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
        if self.awareness:
            self.awareness.start()
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
        if self.awareness and interaction.guild and interaction.type == discord.InteractionType.application_command:
            self._note_command(interaction)
        if self.actions and interaction.type == discord.InteractionType.component:
            if await self.actions.role_button(interaction):
                return
            if self.events and await self.events.button(interaction):
                return
            if self.antispam:
                await self.antispam.button(interaction)

    def _note_command(self, interaction: discord.Interaction) -> None:
        """Umfragen und Events als Server-Ereignis merken (nur Titel/Frage, wer's gestartet hat)."""
        data = interaction.data or {}
        name = data.get("name")
        opts = {o.get("name"): o.get("value") for o in data.get("options") or [] if isinstance(o, dict)}
        where = getattr(interaction.channel, "name", "?")
        who = interaction.user.display_name
        if name == "umfrage" and opts.get("frage"):
            self.awareness.note_event(interaction.guild.id, f"{who} hat in #{where} eine Umfrage gestartet: "
                                                            f"„{str(opts['frage'])[:120]}“", interaction.channel_id)
        elif name == "event" and opts.get("titel"):
            self.awareness.note_event(interaction.guild.id, f"{who} hat ein Event geplant: "
                                                            f"„{str(opts['titel'])[:120]}“ ({opts.get('wann', '?')})",
                                      interaction.channel_id)

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
        if self.awareness and not member.bot:
            self.awareness.note_event(member.guild.id, f"{member.display_name} ist dem Server beigetreten.")
        if self.actions:
            await self.actions.greet(member, self.brain)

    async def on_member_remove(self, member: discord.Member) -> None:
        if self.awareness and not member.bot:
            self.awareness.note_event(member.guild.id, f"{member.display_name} hat den Server verlassen.")

    # ---------- Slash-Befehle ----------

    def _register_commands(self) -> None:
        store = self.store
        register_tagebuch(self.tree, store, self.brain, lambda: self.awareness)
        register_stil(self.tree, store)

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
            # Alte globale Befehle (aus früheren Versionen) aufräumen – sonst zeigt Discord Befehle,
            # die es nicht mehr gibt ("Application command not found"). Dave nutzt nur Server-Befehle.
            try:
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
                log.info("Alte globale Slash-Befehle aufgeräumt")
            except discord.HTTPException as e:
                log.warning("Globale Befehle nicht aufräumbar: %s", e)

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

    async def _context(self, message: discord.Message, history: list[dict], extra: str | None = None) -> str | None:
        guild = message.guild
        settings = settings_line(self.actions.data, guild.id) if self.actions and guild else None
        name = message.author.display_name
        style = style_hint(self.store, guild.id, message.author.id, name) if guild else None
        length = length_hint(message.content or "")
        memory = await self._memory_context(message, history)
        server = None
        if self.awareness and guild:
            query = history[-1]["content"][-500:] if history else message.clean_content
            try:
                server = await self.awareness.context(guild, message.channel.id, query)
            except Exception:  # noqa: BLE001
                log.exception("Server-Kontext fehlgeschlagen")
        return "\n\n".join(p for p in (settings, style, length, server, memory, extra) if p) or None

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
        if not self.memory.recent(channel_id):
            await self._seed_memory(message)
        self.memory.add(channel_id, name, text)
        if self.store and (what := parse_merk_dir(text)):
            self.store.add_fact(guild.id, message.author.id, name, what, source="manuell")
        async with self._locks[channel_id]:
            history = self.memory.as_messages(channel_id)
            if not history:
                return
            async with message.channel.typing():
                found = image_attachments(message) or await recent_images(message)
                images = await load_images(found, self.http)
                parts = [settings_line(data, guild.id) if data else None, DM_HINT.format(name=name, guild=guild.name),
                         style_hint(self.store, guild.id, message.author.id, name), length_hint(message.content or "")]
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
        if self.awareness:
            self.awareness.count(message)
            poll = getattr(message, "poll", None)
            if poll is not None and self.awareness.is_observed(message.channel):
                question = getattr(getattr(poll, "question", None), "text", None) or str(getattr(poll, "question", ""))
                self.awareness.note_event(message.guild.id, f"{message.author.display_name} hat in "
                                          f"#{message.channel.name} eine Umfrage gestartet: „{question[:120]}“",
                                          message.channel.id)
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
        if not self.memory.recent(channel_id) and self._is_addressed(message):
            await self._seed_memory(message)  # z.B. nach einem Neustart: sonst fehlt der Zusammenhang
        self.memory.add(channel_id, message.author.display_name, text)
        addressed = self._is_addressed(message)
        self._note(message, Line(message.author.id, message.author.display_name, text), addressed)

        # „hä?“ direkt nach Daves Antwort -> einfacher erklären (auch ohne @Dave)
        last_dave = None
        if self.store and is_confused(message.content or ""):
            recent = self.memory.recent(channel_id)[:-1]
            now = self.memory._clock()
            last_dave = next((e for e in reversed(recent) if e.from_dave and now - e.at < 600), None)
            if last_dave and not addressed and recent and recent[-1] is last_dave and now - last_dave.at < 180:
                addressed = True

        if not addressed:
            return

        if last_dave is not None:
            await self._explain_simpler(message, last_dave.text)
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
                found = image_attachments(message) or await recent_images(message)
                images = await load_images(found, self.http)
                if found and not images:
                    log.warning("%d Bild(er) gefunden, aber keins ließ sich laden", len(found))
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


    async def _seed_memory(self, message: discord.Message, limit: int = 12) -> None:
        """Holt die letzten Nachrichten aus Discord ins Kurzzeitgedächtnis (nur im Arbeitsspeicher)."""
        after = discord.utils.utcnow() - timedelta(minutes=self.config.memory_minutes)
        older = []
        try:
            async for m in message.channel.history(limit=limit, before=message, after=after):
                older.append(m)
        except (discord.HTTPException, AttributeError) as e:
            log.info("Verlauf zum Aufwärmen nicht lesbar: %s", e)
            return
        for m in reversed(older):  # history liefert neueste zuerst
            from_dave = self.user is not None and m.author.id == self.user.id
            if m.author.bot and not from_dave:
                continue
            text = m.clean_content if m.guild else m.content
            if text.strip():
                self.memory.add(message.channel.id, "Dave" if from_dave else m.author.display_name, text,
                                from_dave=from_dave)
        if older:
            log.info("Kurzzeitgedächtnis für #%s mit %d Nachrichten aufgewärmt",
                     getattr(message.channel, "name", "privat"), len(older))

    async def _explain_simpler(self, message: discord.Message, last: str) -> None:
        guild, channel_id = message.guild, message.channel.id
        switched = self.store.get_style(guild.id, message.author.id) != "einfach"
        if switched:
            self.store.set_style(guild.id, message.author.id, "einfach")
            log.info("Stil für %s auf einfach gestellt (hat nachgefragt)", message.author.id)
        async with self._locks[channel_id]:
            history = self.memory.as_messages(channel_id)
            async with message.channel.typing():
                extra = REPHRASE.format(name=message.author.display_name, last=last[:1200])
                context = await self._context(message, history, extra)
                answer = await self.brain.reply(history, server=guild.name,
                                                channel=getattr(message.channel, "name", "?"), context=context)
            if not answer:
                return
            chunks = split_message(answer + (SWITCH_INFO if switched else ""))
            await message.reply(chunks[0], mention_author=False)
            for chunk in chunks[1:]:
                await message.channel.send(chunk)
            self.memory.add(channel_id, "Dave", answer, from_dave=True)


_BOOT_STAMP = Dave._code_stamp()
