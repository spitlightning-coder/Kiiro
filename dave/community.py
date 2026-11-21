"""Eigene Sprachkanäle, Spieleabend-Planer (/event) und „Was hab ich verpasst?“ (/verpasst)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

import discord
from discord import app_commands

from .actions import TZ, RateLimit
from .extras import norm
from .serverdata import ServerData
from .zeit import describe, parse_when

log = logging.getLogger("dave.community")

EVENT_PREFIX = "dave:event:"
MAX_TEMP_CHANNELS = 20


# =====================================================================
# 🔊 Eigene Sprachkanäle („Raum erstellen“)
# =====================================================================

class TempVoice:
    def __init__(self, bot: discord.Client, data: ServerData, actions):
        self.bot, self.data, self.actions = bot, data, actions
        self.limit = RateLimit(3)  # pro Person 3 neue Räume pro Stunde
        self._lock = asyncio.Lock()

    def creator(self, guild: discord.Guild) -> discord.VoiceChannel | None:
        cid = self.data.get(guild.id, "tempvoice_creator")
        if cid:
            ch = guild.get_channel(cid)
            return ch if isinstance(ch, discord.VoiceChannel) else None
        return discord.utils.find(lambda c: "erstellen" in c.name.lower() or "create" in c.name.lower(),
                                  guild.voice_channels)

    def temp_ids(self, guild_id: int) -> dict[str, int]:
        return self.data.get(guild_id, "tempvoice_channels", {})  # channel_id (str) -> owner_id

    def _save(self, guild_id: int, ids: dict) -> None:
        self.data.set(guild_id, "tempvoice_channels", ids)

    def owner(self, channel: discord.abc.GuildChannel) -> int | None:
        return self.temp_ids(channel.guild.id).get(str(channel.id))

    async def on_voice(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        guild = member.guild
        if self.actions.stopped(guild):
            return
        creator = self.creator(guild)
        if creator and after.channel and after.channel.id == creator.id:
            await self._create(member, creator)
        if before.channel and before.channel != after.channel:
            await self._cleanup(before.channel)

    async def _create(self, member: discord.Member, creator: discord.VoiceChannel) -> None:
        guild, me = member.guild, member.guild.me
        perms = creator.category.permissions_for(me) if creator.category else me.guild_permissions
        if not (perms.manage_channels and perms.move_members):
            log.warning("Für eigene Räume fehlen mir 'Kanäle verwalten' und/oder 'Mitglieder verschieben'")
            return
        async with self._lock:
            ids = self.temp_ids(guild.id)
            mine = [cid for cid, owner in ids.items() if owner == member.id and guild.get_channel(int(cid))]
            if mine:  # hat schon einen Raum -> einfach dorthin
                await member.move_to(guild.get_channel(int(mine[0])))
                return
            if len(ids) >= MAX_TEMP_CHANNELS or not self.limit.allow((guild.id, member.id)):
                await member.move_to(None)
                return
            overwrites = dict(creator.overwrites)
            overwrites[member] = discord.PermissionOverwrite(manage_channels=True, move_members=True, connect=True)
            channel = await guild.create_voice_channel(
                f"「🔊」{member.display_name[:60]}s Raum", category=creator.category, overwrites=overwrites,
                bitrate=min(creator.bitrate, guild.bitrate_limit), reason=f"Eigener Raum für {member}")
            ids[str(channel.id)] = member.id
            self._save(guild.id, ids)
        try:
            await member.move_to(channel)
        except discord.HTTPException:
            pass
        await self.actions.audit(guild, f"🔊 Raum erstellt für {member}: {channel.name}")

    async def _cleanup(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.VoiceChannel) or channel.members:
            return
        ids = self.temp_ids(channel.guild.id)
        if str(channel.id) not in ids:
            return
        ids.pop(str(channel.id), None)
        self._save(channel.guild.id, ids)
        try:
            await channel.delete(reason="Eigener Raum leer")
        except discord.HTTPException as e:
            log.warning("Leeren Raum konnte ich nicht löschen: %s", e)

    async def cleanup_all(self, guild: discord.Guild) -> None:
        """Beim Start: leere Räume aufräumen, verschwundene aus der Liste werfen."""
        ids = self.temp_ids(guild.id)
        for cid in list(ids):
            ch = guild.get_channel(int(cid))
            if ch is None:
                ids.pop(cid)
            elif not getattr(ch, "members", None):
                ids.pop(cid)
                try:
                    await ch.delete(reason="Eigener Raum leer (Aufräumen)")
                except discord.HTTPException:
                    pass
        self._save(guild.id, ids)


# =====================================================================
# 📅 Spieleabend-Planer
# =====================================================================

STATUS_LABELS = {"ja": ("✅", "Bin dabei"), "vielleicht": ("🤔", "Vielleicht"), "nein": ("❌", "Kann nicht")}


def event_view(event_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    styles = {"ja": discord.ButtonStyle.success, "vielleicht": discord.ButtonStyle.secondary,
              "nein": discord.ButtonStyle.danger}
    for status, (emoji, label) in STATUS_LABELS.items():
        view.add_item(discord.ui.Button(label=label, emoji=emoji, style=styles[status],
                                        custom_id=f"{EVENT_PREFIX}{event_id}:{status}"))
    return view


def event_embed(event: dict, rsvps: dict[str, list[int]]) -> discord.Embed:
    when = datetime.fromtimestamp(event["starts_at"], TZ)
    ts = int(event["starts_at"])
    embed = discord.Embed(title=f"📅 {event['title']}", description=event["details"] or None, color=0xEB459E)
    embed.add_field(name="Wann", value=f"<t:{ts}:F>\n(<t:{ts}:R>)", inline=False)
    if event.get("role_id"):
        embed.add_field(name="Für", value=f"<@&{event['role_id']}>", inline=True)
    embed.add_field(name="Organisiert von", value=f"<@{event['creator_id']}>", inline=True)
    for status, (emoji, label) in STATUS_LABELS.items():
        people = rsvps.get(status, [])
        embed.add_field(name=f"{emoji} {label} ({len(people)})",
                        value=" ".join(f"<@{u}>" for u in people)[:1024] or "–", inline=False)
    embed.set_footer(text=f"Event #{event['id']} · Erinnerung 15 Min. vorher · {when:%d.%m.%Y %H:%M}")
    return embed


class Events:
    def __init__(self, bot: discord.Client, data: ServerData, actions):
        self.bot, self.data, self.actions = bot, data, actions
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for event in self.data.due_events(time.time()):
                    await self._remind(event)
            except Exception:  # noqa: BLE001
                log.exception("Event-Schleife")
            await asyncio.sleep(30)

    async def _remind(self, event: dict) -> None:
        stage = event["stage"]
        self.data.set_event_stage(event["id"], stage + 1)
        guild = self.bot.get_guild(event["guild_id"])
        channel = guild.get_channel(event["channel_id"]) if guild else None
        if channel is None or self.actions.stopped(guild):
            return
        going = self.data.rsvps(event["id"])
        ping_ids = going["ja"] + (going["vielleicht"] if stage == 0 else [])
        if not ping_ids:
            return
        pings = " ".join(f"<@{u}>" for u in ping_ids)
        if stage == 0 and time.time() < event["starts_at"]:
            text = f"⏰ {pings} **{event['title']}** geht <t:{int(event['starts_at'])}:R> los!"
        elif stage <= 1:
            text = f"🎮 {pings} **{event['title']}** startet jetzt! Viel Spaß!"
            self.data.set_event_stage(event["id"], 2)
        else:
            return
        await channel.send(text, allowed_mentions=discord.AllowedMentions(
            users=[discord.Object(u) for u in ping_ids], roles=False, everyone=False))

    async def button(self, interaction: discord.Interaction) -> bool:
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith(EVENT_PREFIX):
            return False
        try:
            event_id, status = cid[len(EVENT_PREFIX):].split(":")
            event = self.data.get_event(int(event_id))
        except ValueError:
            event = None
        if not event:
            await interaction.response.send_message("Das Event gibt's nicht mehr.", ephemeral=True)
            return True
        current = {s for s, users in self.data.rsvps(event["id"]).items() if interaction.user.id in users}
        self.data.rsvp(event["id"], interaction.user.id, None if status in current else status)
        await interaction.response.edit_message(embed=event_embed(event, self.data.rsvps(event["id"])),
                                                view=event_view(event["id"]))
        return True


def register_events(tree: app_commands.CommandTree, events: Events) -> None:
    @tree.command(name="event", description="Plant einen Spieleabend mit Zusagen und Erinnerung")
    @app_commands.describe(titel="z.B. 'GTA-Abend'", wann="z.B. 'heute um 20 Uhr', 'morgen um 19:30', 'am 24.12. um 18 Uhr'",
                           rolle="Wer gepingt werden soll (z.B. @🚗 GTA). Leer = Event-Ping",
                           details="Optional: was genau, wo, wer bringt was mit …")
    @app_commands.guild_only()
    async def event(interaction: discord.Interaction, titel: str, wann: str,
                    rolle: discord.Role | None = None, details: str = ""):
        guild = interaction.guild
        if events.actions.stopped(guild):
            await interaction.response.send_message("Ich bin gerade per Notbremse gestoppt.", ephemeral=True)
            return
        now = datetime.now(TZ)
        found = parse_when(wann, now)
        if not found or found[0] < now + timedelta(minutes=5) or found[0] > now + timedelta(days=90):
            await interaction.response.send_message(
                "Die Zeit hab ich nicht verstanden oder sie liegt nicht zwischen 5 Minuten und 90 Tagen in der Zukunft. "
                "Probier z.B. 'heute um 20 Uhr' oder 'morgen um 19:30'.", ephemeral=True)
            return
        if rolle is None:
            rolle = discord.utils.find(lambda r: norm(r.name) == "event-ping", guild.roles)
        if rolle is not None and (rolle.is_default() or (rolle.permissions.administrator)):
            rolle = None  # @everyone oder Admin-Rollen werden nie gepingt
        eid = events.data.add_event(guild.id, interaction.channel_id, interaction.user.id, titel.strip(),
                                    details.strip(), found[0].timestamp(), rolle.id if rolle else None)
        events.data.rsvp(eid, interaction.user.id, "ja")
        ev = events.data.get_event(eid)
        await interaction.response.send_message(
            content=f"{rolle.mention} Neues Event!" if rolle else "Neues Event!",
            embed=event_embed(ev, events.data.rsvps(eid)), view=event_view(eid),
            allowed_mentions=discord.AllowedMentions(roles=[rolle] if rolle else [], everyone=False, users=False))
        msg = await interaction.original_response()
        events.data.set_event_message(eid, msg.id)
        await events.actions.audit(guild, f"📅 Event #{eid} „{titel}“ von {interaction.user} "
                                          f"({describe(found[0], now)})")


# =====================================================================
# 📝 „Was hab ich verpasst?“
# =====================================================================

SUMMARY_SYSTEM = """Du fasst Discord-Chatverläufe auf Deutsch zusammen, locker und kurz.
- 3 bis 7 Stichpunkte mit "•", das Wichtigste zuerst: Themen, Pläne, Verabredungen, Entscheidungen, offene Fragen.
- Nenne Leute beim Namen, wenn es hilft (ohne @).
- Nichts erfinden, nur was im Verlauf steht. Kein Smalltalk-Kleinkram.
- Wenn kaum etwas passiert ist, sag das in einem Satz."""


def register_summary(tree: app_commands.CommandTree, brain, actions) -> None:
    limit = RateLimit(4)  # pro Person 4 Zusammenfassungen pro Stunde (schont das Limit)

    @tree.command(name="verpasst", description="Dave fasst zusammen, was hier in letzter Zeit los war")
    @app_commands.describe(stunden="Wie weit zurück (1-24, Standard 3)")
    @app_commands.guild_only()
    async def verpasst(interaction: discord.Interaction, stunden: app_commands.Range[int, 1, 24] = 3):
        if actions.stopped(interaction.guild):
            await interaction.response.send_message("Ich bin gerade per Notbremse gestoppt.", ephemeral=True)
            return
        if not limit.allow((interaction.guild_id, interaction.user.id)):
            await interaction.response.send_message("Du hast schon ein paar Zusammenfassungen geholt – "
                                                    "versuch's später nochmal.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        after = discord.utils.utcnow() - timedelta(hours=stunden)
        lines = []
        try:
            async for m in interaction.channel.history(limit=250, after=after, oldest_first=True):
                if m.content and not m.author.bot or (m.author == interaction.client.user and m.content):
                    lines.append(f"{m.author.display_name}: {m.clean_content[:300]}")
        except discord.Forbidden:
            await interaction.followup.send("Ich darf den Verlauf hier nicht lesen.", ephemeral=True)
            return
        if len(lines) < 3:
            await interaction.followup.send(f"In den letzten {stunden} Stunden war hier kaum was los. 🦗",
                                            ephemeral=True)
            return
        transcript = "\n".join(lines)[-12000:]
        summary = await brain.complete(SUMMARY_SYSTEM, f"Kanal: #{interaction.channel.name}\n\n{transcript}",
                                       max_tokens=600)
        if not summary:
            await interaction.followup.send("Mein Hirn hängt gerade, versuch's gleich nochmal.", ephemeral=True)
            return
        embed = discord.Embed(title=f"📝 Das war los in #{interaction.channel.name}",
                              description=summary[:4000], color=0x57F287)
        embed.set_footer(text=f"Letzte {stunden} Std. · {len(lines)} Nachrichten")
        await interaction.followup.send(embed=embed, ephemeral=True)
