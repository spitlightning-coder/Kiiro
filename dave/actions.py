"""Phase 1 Server-Aktionen: Begrüßung, Selbstwahl-Rollen, Erinnerungen, Prüfkette, #dave-log, Notbremse.

Grundsatz: Der Code entscheidet, nicht das Sprachmodell. Jede Aktion läuft durch die Prüfkette
(Notbremse, Freigabe, Rechte, Rollen-Hierarchie, Limits) und wird geloggt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from .extras import ROLE_BUTTON_PREFIX, refresh_role_message
from .serverdata import ServerData
from .zeit import describe, parse_reminder, parse_when

log = logging.getLogger("dave.actions")

TZ = ZoneInfo("Europe/Berlin")
LOG_CHANNEL_NAME = "dave-log"
MAX_OPEN_REMINDERS = 15
MAX_REMINDER_DAYS = 366
ROLE_CHANGES_PER_HOUR = 10

# Rollen mit diesen Rechten vergibt Dave nie, auch wenn jemand sie freigibt.
DANGEROUS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "kick_members", "ban_members",
             "manage_messages", "mention_everyone", "moderate_members", "manage_webhooks", "manage_nicknames",
             "manage_expressions", "manage_events", "manage_threads", "view_audit_log")


class RateLimit:
    def __init__(self, per_hour: int, clock=time.monotonic):
        self.per_hour, self._clock = per_hour, clock
        self._hits: dict[tuple, deque] = defaultdict(deque)

    def allow(self, key: tuple) -> bool:
        now = self._clock()
        q = self._hits[key]
        while q and q[0] < now - 3600:
            q.popleft()
        if len(q) >= self.per_hour:
            return False
        q.append(now)
        return True


def role_problem(role: discord.Role, me: discord.Member, allowed_ids: list[int]) -> str | None:
    """Prüfkette für Rollen. None = darf, sonst ein Grund auf Deutsch."""
    if role.id not in allowed_ids:
        return f"„{role.name}“ ist nicht zur Selbstwahl freigegeben."
    if role.is_default() or role.managed:
        return f"„{role.name}“ kann man nicht von Hand vergeben."
    bad = [p for p in DANGEROUS if getattr(role.permissions, p, False)]
    if bad:
        return f"„{role.name}“ hat Admin-/Moderationsrechte ({', '.join(bad[:3])}), die vergebe ich nie."
    if not me.guild_permissions.manage_roles:
        return "Mir fehlt das Recht „Rollen verwalten“."
    if role >= me.top_role:
        return f"„{role.name}“ steht über meiner eigenen Rolle. Zieh meine Rolle in den Servereinstellungen höher."
    return None


class Actions:
    def __init__(self, bot: discord.Client, data: ServerData, tree: app_commands.CommandTree):
        self.bot = bot
        self.data = data
        self.role_limit = RateLimit(ROLE_CHANGES_PER_HOUR)
        self._reminder_task: asyncio.Task | None = None
        self.news = None  # wird vom Bot gesetzt
        self.role_groups: list = []
        self.proactive = None  # wird vom Bot gesetzt  # Gruppen für die Selfroles-Nachricht (aus DAVE_SELFROLES)
        self._register(tree)

    # ---------- Log ----------

    def _log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        cid = self.data.get(guild.id, "log_channel")
        channel = guild.get_channel(cid) if cid else discord.utils.find(
            lambda c: c.name.endswith(LOG_CHANNEL_NAME), guild.text_channels)  # findet auch „「📋」dave-log“
        if channel and channel.permissions_for(guild.me).send_messages:
            return channel
        return None

    async def audit(self, guild: discord.Guild, text: str) -> None:
        log.info("[%s] %s", guild.name, text)
        channel = self._log_channel(guild)
        if channel:
            try:
                await channel.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.warning("Konnte nicht in #%s loggen: %s", channel.name, e)

    def stopped(self, guild: discord.Guild | None) -> bool:
        return bool(guild and self.data.stopped(guild.id))

    # ---------- Begrüßung ----------

    def welcome_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        cid = self.data.get(guild.id, "welcome_channel")
        if cid:
            return guild.get_channel(cid)
        return discord.utils.find(lambda c: c.name.endswith(("ankunft", "welcome", "willkommen", "welcome-and-bye")),
                                  guild.text_channels)

    async def greet(self, member: discord.Member, brain) -> None:
        guild = member.guild
        if member.bot or self.stopped(guild) or not self.data.get(guild.id, "welcome_on", True):
            return
        channel = self.welcome_channel(guild)
        if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(guild.me).send_messages:
            log.warning("Begrüßungskanal fehlt oder kein Schreibrecht auf %s", guild.name)
            return
        hint = " Erwähne kurz, dass man sich mit /rollen Rollen aussuchen kann." if self.data.self_roles(guild.id) else ""
        prompt = [{"role": "user", "content":
                   f"System: {member.display_name} ist gerade neu auf den Server gekommen. Begrüß die Person in "
                   f"1-2 lockeren Sätzen.{hint} Schreib nur die Begrüßung."}]
        text = None
        try:
            text = await brain.reply(prompt, server=guild.name, channel=channel.name)
        except Exception:  # noqa: BLE001
            log.exception("Begrüßungstext fehlgeschlagen")
        if not text or text.startswith(("Mein Hirn", "Ich werde gerade")):
            text = f"Willkommen auf {guild.name}! Schön, dass du da bist."
        await channel.send(f"{member.mention} {text}",
                           allowed_mentions=discord.AllowedMentions(users=[member], everyone=False, roles=False))
        await self.audit(guild, f"👋 Begrüßt: {member} in #{channel.name}")

    # ---------- Erinnerungen ----------

    def start_reminder_loop(self) -> None:
        if self._reminder_task is None or self._reminder_task.done():
            self._reminder_task = asyncio.create_task(self._reminder_loop())

    async def _reminder_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for r in self.data.due_reminders():
                    await self._deliver(r)
            except Exception:  # noqa: BLE001
                log.exception("Erinnerungs-Schleife")
            await asyncio.sleep(20)

    async def _deliver(self, r) -> None:
        self.data.finish_reminder(r.id)  # zuerst abhaken: lieber einmal verpasst als zehnmal gepingt
        guild = self.bot.get_guild(r.guild_id)
        if guild is None or self.data.stopped(r.guild_id):
            return
        late = time.time() - r.due_at > 300
        text = f"⏰ <@{r.user_id}> Erinnerung{' (etwas verspätet)' if late else ''}: {r.text}"
        channel = guild.get_channel(r.channel_id)
        mentions = discord.AllowedMentions(users=[discord.Object(r.user_id)], everyone=False, roles=False)
        try:
            if channel and channel.permissions_for(guild.me).send_messages:
                await channel.send(text, allowed_mentions=mentions)
            else:
                user = await self.bot.fetch_user(r.user_id)
                await user.send(text)
        except discord.HTTPException as e:
            log.warning("Erinnerung %s nicht zustellbar: %s", r.id, e)

    def create_reminder(self, guild: discord.Guild, channel_id: int, user: discord.abc.User,
                        when: datetime, text: str) -> str:
        """Prüfkette für Erinnerungen. Gibt die Antwort für den Nutzer zurück."""
        now = datetime.now(TZ)
        if self.stopped(guild):
            return "Ich bin gerade per Notbremse gestoppt."
        if not text.strip():
            return "Woran soll ich dich denn erinnern?"
        if (when - now).total_seconds() < 50:
            return "Das ist zu knapp, mindestens eine Minute bitte."
        if (when - now).days > MAX_REMINDER_DAYS:
            return "So weit plane ich nicht voraus, maximal ein Jahr."
        if len(self.data.open_reminders(guild.id, user.id)) >= MAX_OPEN_REMINDERS:
            return f"Du hast schon {MAX_OPEN_REMINDERS} offene Erinnerungen. Lösch erst welche mit /erinnerungen."
        rid = self.data.add_reminder(guild.id, channel_id, user.id, when.timestamp(), text.strip())
        asyncio.create_task(self.audit(guild, f"⏰ Erinnerung #{rid} für {user} {describe(when, now)}"))
        return f"Alles klar, ich erinnere dich {describe(when, now)} an: {text.strip()}"

    def reminder_from_chat(self, message: discord.Message) -> str | None:
        """'Dave, erinner mich in 10 Minuten an X' -> Bestätigung, oder None wenn es keine Erinnerung ist."""
        found = parse_reminder(message.clean_content, datetime.now(TZ))
        if not found:
            return None
        when, what = found
        return self.create_reminder(message.guild, message.channel.id, message.author, when, what)

    # ---------- Rollen ----------

    async def toggle_roles(self, member: discord.Member, wanted: set[int]) -> list[str]:
        guild, me = member.guild, member.guild.me
        allowed = self.data.self_roles(guild.id)
        results = []
        for rid in allowed:
            role = guild.get_role(rid)
            if role is None:
                continue
            has = role in member.roles
            if (rid in wanted) == has:
                continue
            problem = role_problem(role, me, allowed)
            if problem:
                results.append(f"❌ {problem}")
                continue
            if not self.role_limit.allow((guild.id, member.id)):
                results.append("❌ Zu viele Rollenwechsel in letzter Zeit, versuch's in einer Stunde nochmal.")
                break
            try:
                if has:
                    await member.remove_roles(role, reason="Selbstwahl über Dave")
                    results.append(f"➖ {role.name}")
                else:
                    await member.add_roles(role, reason="Selbstwahl über Dave")
                    results.append(f"➕ {role.name}")
                await self.audit(guild, f"🎭 {member} {'entfernt' if has else 'nimmt'} Rolle {role.name}")
            except discord.Forbidden:
                results.append(f"❌ Discord lässt mich „{role.name}“ nicht vergeben.")
        return results or ["Nichts geändert."]

    async def role_button(self, interaction: discord.Interaction) -> bool:
        """Klick auf einen Knopf der Selfroles-Nachricht. True, wenn es einer war."""
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith(ROLE_BUTTON_PREFIX) or interaction.guild is None:
            return False
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.stopped(interaction.guild):
            await interaction.followup.send("Ich bin gerade per Notbremse gestoppt.", ephemeral=True)
            return True
        rid = int(cid[len(ROLE_BUTTON_PREFIX):])
        member = interaction.guild.get_member(interaction.user.id) or interaction.user
        allowed = self.data.self_roles(interaction.guild.id)
        has = {r.id for r in member.roles if r.id in allowed}
        results = await self.toggle_roles(member, has ^ {rid})
        await interaction.followup.send("\n".join(results), ephemeral=True)
        return True

    # ---------- Slash-Befehle ----------

    def _register(self, tree: app_commands.CommandTree) -> None:
        actions = self

        @tree.command(name="rollen", description="Such dir Rollen aus")
        @app_commands.guild_only()
        async def rollen(interaction: discord.Interaction):
            guild = interaction.guild
            if actions.stopped(guild):
                await interaction.response.send_message("Ich bin gerade per Notbremse gestoppt.", ephemeral=True)
                return
            roles = [r for r in (guild.get_role(i) for i in actions.data.self_roles(guild.id)) if r][:25]
            if not roles:
                await interaction.response.send_message("Hier sind noch keine Rollen zur Selbstwahl freigegeben.",
                                                        ephemeral=True)
                return
            await interaction.response.send_message("Wähl aus, welche Rollen du haben willst:",
                                                    view=RoleView(actions, interaction.user, roles), ephemeral=True)

        @tree.command(name="erinnerung", description="Dave erinnert dich an etwas")
        @app_commands.describe(wann="z.B. 'in 30 Minuten', 'um 18:30', 'morgen um 9', 'am 24.12. um 18 Uhr'",
                               was="Woran Dave dich erinnern soll")
        @app_commands.guild_only()
        async def erinnerung(interaction: discord.Interaction, wann: str, was: str):
            found = parse_when(wann, datetime.now(TZ))
            if not found:
                answer = "Die Zeit hab ich nicht verstanden. Probier z.B. 'in 30 Minuten' oder 'morgen um 9'."
            else:
                answer = actions.create_reminder(interaction.guild, interaction.channel_id, interaction.user,
                                                 found[0], was)
            await interaction.response.send_message(answer, ephemeral=True)

        @tree.command(name="erinnerungen", description="Zeigt deine offenen Erinnerungen")
        @app_commands.guild_only()
        async def erinnerungen(interaction: discord.Interaction):
            items = actions.data.open_reminders(interaction.guild_id, interaction.user.id)
            if not items:
                text = "Du hast keine offenen Erinnerungen."
            else:
                now = datetime.now(TZ)
                text = "Deine Erinnerungen:\n" + "\n".join(
                    f"`#{r.id}` {describe(datetime.fromtimestamp(r.due_at, TZ), now)}: {r.text}" for r in items)
                text += "\n\nLöschen mit /erinnerung_loeschen."
            await interaction.response.send_message(text[:2000], ephemeral=True)

        @tree.command(name="erinnerung_loeschen", description="Löscht eine deiner Erinnerungen")
        @app_commands.describe(nummer="Die Nummer aus /erinnerungen")
        @app_commands.guild_only()
        async def erinnerung_loeschen(interaction: discord.Interaction, nummer: int):
            ok = actions.data.cancel_reminder(interaction.guild_id, interaction.user.id, nummer)
            await interaction.response.send_message("Gelöscht." if ok else "Die Erinnerung gibt's nicht (mehr).",
                                                    ephemeral=True)

        admin = app_commands.Group(name="dave", description="Dave verwalten (nur für Admins)",
                                   default_permissions=discord.Permissions(manage_guild=True), guild_only=True)

        async def is_admin(interaction: discord.Interaction) -> bool:
            if interaction.user.guild_permissions.manage_guild:
                return True
            await interaction.response.send_message("Das dürfen nur Leute mit „Server verwalten“.", ephemeral=True)
            return False

        @admin.command(name="stopp", description="Notbremse: Dave hält sofort alles an")
        async def stopp(interaction: discord.Interaction):
            if not await is_admin(interaction):
                return
            actions.data.set(interaction.guild_id, "stopped", True)
            await interaction.response.send_message("🛑 Notbremse gezogen. Ich antworte und handle nicht mehr, "
                                                    "bis jemand /dave weiter macht.")
            await actions.audit(interaction.guild, f"🛑 Notbremse von {interaction.user}")

        @admin.command(name="weiter", description="Löst die Notbremse wieder")
        async def weiter(interaction: discord.Interaction):
            if not await is_admin(interaction):
                return
            actions.data.set(interaction.guild_id, "stopped", False)
            await interaction.response.send_message("✅ Bin wieder da.")
            await actions.audit(interaction.guild, f"✅ Notbremse gelöst von {interaction.user}")

        @admin.command(name="status", description="Zeigt Daves Einstellungen auf diesem Server")
        async def status(interaction: discord.Interaction):
            if not await is_admin(interaction):
                return
            g, d = interaction.guild, actions.data
            welcome = actions.welcome_channel(g)
            roles = [g.get_role(r) for r in d.self_roles(g.id)]
            logch = actions._log_channel(g)
            lines = [
                f"Notbremse: {'🛑 gezogen' if d.stopped(g.id) else '✅ aus'}",
                f"Begrüßung: {'an' if d.get(g.id, 'welcome_on', True) else 'aus'}"
                f"{f' in #{welcome.name}' if welcome else ''}",
                "Selbstwahl-Rollen: " + (", ".join(r.name for r in roles if r) or "keine"),
                f"Log-Kanal: {('#' + logch.name) if logch else 'keiner (nur Logdatei)'}",
                f"Rollen verwalten: {'✅' if g.me.guild_permissions.manage_roles else '❌ fehlt'}",
                f"News: {'an' if d.get(g.id, 'news_on', True) else 'aus'}"
                f"{(' in #' + actions.news.channel(g).name) if actions.news and actions.news.channel(g) else ' (kein Kanal)'}"
                f", {len(actions.news.feeds(g.id)) if actions.news else 0} Quellen",
                f"Proaktiv: {actions.proactive.mode(g.id) if actions.proactive else '-'}"
                f" ({actions.proactive.posts_today(g.id) if actions.proactive else 0}/6 heute)",
            ]
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @admin.command(name="begruessung", description="Begrüßung neuer Mitglieder an/aus")
        @app_commands.describe(an="Begrüßung an oder aus", kanal="In welchem Kanal begrüßt wird")
        async def begruessung(interaction: discord.Interaction, an: bool, kanal: discord.TextChannel | None = None):
            if not await is_admin(interaction):
                return
            g = interaction.guild
            if kanal:
                actions.data.set(g.id, "welcome_channel", kanal.id)
            if an and not actions.welcome_channel(g):
                await interaction.response.send_message("Sag mir noch den Kanal: /dave begruessung an:True kanal:#…",
                                                        ephemeral=True)
                return
            actions.data.set(g.id, "welcome_on", an)
            await interaction.response.send_message(f"Begrüßung ist jetzt {'an' if an else 'aus'}.", ephemeral=True)
            await actions.audit(g, f"⚙️ Begrüßung {'an' if an else 'aus'} von {interaction.user}")

        @admin.command(name="rolle_freigeben", description="Gibt eine Rolle für /rollen frei")
        async def rolle_freigeben(interaction: discord.Interaction, rolle: discord.Role):
            if not await is_admin(interaction):
                return
            g = interaction.guild
            problem = role_problem(rolle, g.me, [rolle.id])
            if problem:
                await interaction.response.send_message(f"Geht nicht: {problem}", ephemeral=True)
                return
            actions.data.set_self_role(g.id, rolle.id, True)
            await interaction.response.send_message(f"„{rolle.name}“ kann man sich jetzt mit /rollen nehmen.",
                                                    ephemeral=True)
            await actions.audit(g, f"⚙️ Rolle {rolle.name} freigegeben von {interaction.user}")
            await refresh_role_message(g, actions.data, actions.role_groups)

        @admin.command(name="rolle_sperren", description="Nimmt eine Rolle aus /rollen raus")
        async def rolle_sperren(interaction: discord.Interaction, rolle: discord.Role):
            if not await is_admin(interaction):
                return
            actions.data.set_self_role(interaction.guild_id, rolle.id, False)
            await interaction.response.send_message(f"„{rolle.name}“ ist nicht mehr wählbar.", ephemeral=True)
            await actions.audit(interaction.guild, f"⚙️ Rolle {rolle.name} gesperrt von {interaction.user}")
            await refresh_role_message(interaction.guild, actions.data, actions.role_groups)

        @admin.command(name="logkanal", description="Legt fest, wohin Dave seine Aktionen loggt")
        async def logkanal(interaction: discord.Interaction, kanal: discord.TextChannel):
            if not await is_admin(interaction):
                return
            if not kanal.permissions_for(interaction.guild.me).send_messages:
                await interaction.response.send_message(f"In #{kanal.name} darf ich nicht schreiben.", ephemeral=True)
                return
            actions.data.set(interaction.guild_id, "log_channel", kanal.id)
            await interaction.response.send_message(f"Ich logge ab jetzt in #{kanal.name}.", ephemeral=True)
            await actions.audit(interaction.guild, f"⚙️ Log-Kanal gesetzt von {interaction.user}")

        @admin.command(name="news", description="Automatische News an/aus")
        @app_commands.describe(an="News an oder aus", kanal="Wohin die News kommen (sonst der Kanal mit 'news' im Namen)")
        async def news_cmd(interaction: discord.Interaction, an: bool, kanal: discord.TextChannel | None = None):
            if not await is_admin(interaction):
                return
            if kanal:
                actions.data.set(interaction.guild_id, "news_channel", kanal.id)
            actions.data.set(interaction.guild_id, "news_on", an)
            await interaction.response.send_message(f"News sind jetzt {'an' if an else 'aus'}.", ephemeral=True)
            await actions.audit(interaction.guild, f"⚙️ News {'an' if an else 'aus'} von {interaction.user}")

        @admin.command(name="news_quellen", description="Zeigt die News-Quellen")
        async def news_quellen(interaction: discord.Interaction):
            if not await is_admin(interaction):
                return
            feeds = actions.news.feeds(interaction.guild_id) if actions.news else []
            await interaction.response.send_message("News-Quellen:\n" + "\n".join(f"• <{f}>" for f in feeds),
                                                    ephemeral=True)

        @admin.command(name="news_quelle_hinzu", description="Fügt einen RSS-Feed hinzu")
        async def news_quelle_hinzu(interaction: discord.Interaction, url: str):
            if not await is_admin(interaction):
                return
            if not url.startswith(("http://", "https://")) or not actions.news:
                await interaction.response.send_message("Das ist keine gültige Adresse.", ephemeral=True)
                return
            feeds = list(actions.news.feeds(interaction.guild_id))
            if url not in feeds:
                feeds.append(url)
            actions.data.set(interaction.guild_id, "news_feeds", feeds[:15])
            await interaction.response.send_message(f"Quelle hinzugefügt ({len(feeds)} insgesamt).", ephemeral=True)
            await actions.audit(interaction.guild, f"⚙️ News-Quelle hinzu von {interaction.user}: {url}")

        @admin.command(name="news_quelle_weg", description="Entfernt einen RSS-Feed")
        async def news_quelle_weg(interaction: discord.Interaction, url: str):
            if not await is_admin(interaction):
                return
            feeds = [f for f in (actions.news.feeds(interaction.guild_id) if actions.news else []) if f != url]
            actions.data.set(interaction.guild_id, "news_feeds", feeds)
            await interaction.response.send_message(f"Erledigt, noch {len(feeds)} Quellen.", ephemeral=True)
            await actions.audit(interaction.guild, f"⚙️ News-Quelle entfernt von {interaction.user}: {url}")

        @admin.command(name="gratis_spiele", description="Gratis-Spiele-Alarm an/aus")
        @app_commands.describe(an="An oder aus", kanal="Wohin (sonst Kanal mit 'gratis' im Namen bzw. News-Kanal)")
        async def gratis_spiele(interaction: discord.Interaction, an: bool, kanal: discord.TextChannel | None = None):
            if not await is_admin(interaction):
                return
            if kanal:
                actions.data.set(interaction.guild_id, "freegames_channel", kanal.id)
            actions.data.set(interaction.guild_id, "freegames_on", an)
            await interaction.response.send_message(f"Gratis-Spiele-Alarm ist jetzt {'an' if an else 'aus'}.",
                                                    ephemeral=True)
            await actions.audit(interaction.guild, f"⚙️ Gratis-Spiele {'an' if an else 'aus'} von {interaction.user}")

        @admin.command(name="proaktiv", description="Ob Dave von sich aus schreibt: aus, test (nur Log) oder an")
        @app_commands.choices(modus=[app_commands.Choice(name="aus", value="aus"),
                                     app_commands.Choice(name="test (Ideen nur in #dave-log)", value="test"),
                                     app_commands.Choice(name="an", value="an")])
        async def proaktiv(interaction: discord.Interaction, modus: app_commands.Choice[str]):
            if not await is_admin(interaction):
                return
            actions.data.set(interaction.guild_id, "proactive_mode", modus.value)
            chans = actions.proactive.channels(interaction.guild) if actions.proactive else []
            await interaction.response.send_message(
                f"Proaktives Schreiben: **{modus.value}**. Kanäle: "
                f"{', '.join('#' + c.name for c in chans) or 'keine'} (ändern mit /dave proaktiv_kanal).",
                ephemeral=True)
            await actions.audit(interaction.guild, f"⚙️ Proaktiv: {modus.value} von {interaction.user}")

        @admin.command(name="proaktiv_kanal", description="Kanal für proaktive Nachrichten erlauben/sperren")
        async def proaktiv_kanal(interaction: discord.Interaction, kanal: discord.TextChannel, an: bool):
            if not await is_admin(interaction) or not actions.proactive:
                return
            actions.proactive.set_channel(interaction.guild, kanal.id, an)
            chans = actions.proactive.channels(interaction.guild)
            await interaction.response.send_message(
                f"Okay. Proaktive Kanäle: {', '.join('#' + c.name for c in chans) or 'keine'}", ephemeral=True)

        @admin.command(name="proaktiv_jetzt", description="Testet proaktives Schreiben sofort (ignoriert die Pausen)")
        async def proaktiv_jetzt(interaction: discord.Interaction):
            if not await is_admin(interaction) or not actions.proactive:
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            result = await actions.proactive.try_post(interaction.guild, force=True)
            await interaction.followup.send(f"Ergebnis: {result}", ephemeral=True)

        tree.add_command(admin)


class RoleView(discord.ui.View):
    def __init__(self, actions: Actions, member: discord.Member, roles: list[discord.Role]):
        super().__init__(timeout=120)
        self.actions, self.member = actions, member
        select = discord.ui.Select(
            placeholder="Rollen auswählen …", min_values=0, max_values=len(roles),
            options=[discord.SelectOption(label=r.name[:100], value=str(r.id), default=r in member.roles)
                     for r in roles])
        select.callback = self._chosen
        self.add_item(select)
        self.select = select

    async def _chosen(self, interaction: discord.Interaction):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Das ist nicht dein Menü.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.guild.get_member(self.member.id) or self.member
        results = await self.actions.toggle_roles(member, {int(v) for v in self.select.values})
        await interaction.followup.send("\n".join(results), ephemeral=True)
        self.stop()
