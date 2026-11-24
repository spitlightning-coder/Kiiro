"""⭐ Hall of Fame und 🛡️ Anti-Spam.

Anti-Spam löscht nie selbst: Dave meldet Verdächtiges in #dave-log, Moderatoren entscheiden per Knopf
(Nachricht löschen / 10 Min. Timeout / ignorieren). Die Knöpfe prüfen die Rechte dessen, der klickt.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands

from .serverdata import ServerData

log = logging.getLogger("dave.guard")

STAR = "⭐"
DEFAULT_STARS = 3
MOD_PREFIX = "dave:mod:"

# ---------------------------------------------------------------------
# ⭐ Hall of Fame
# ---------------------------------------------------------------------


class HallOfFame:
    def __init__(self, bot: discord.Client, data: ServerData, actions):
        self.bot, self.data, self.actions = bot, data, actions

    def channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        cid = self.data.get(guild.id, "hof_channel")
        ch = guild.get_channel(cid) if cid else discord.utils.find(
            lambda c: c.name.endswith("hall-of-fame") or c.name.endswith("starboard"), guild.text_channels)
        if ch and ch.permissions_for(guild.me).send_messages and ch.permissions_for(guild.me).embed_links:
            return ch
        return None

    def threshold(self, guild_id: int) -> int:
        return self.data.get(guild_id, "hof_stars", DEFAULT_STARS)

    @staticmethod
    def embed(message: discord.Message, stars: int) -> discord.Embed:
        e = discord.Embed(description=message.content[:3500] or None, color=0xFEE75C, timestamp=message.created_at)
        e.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        e.add_field(name="​", value=f"[Zur Nachricht]({message.jump_url}) · #{message.channel.name}", inline=False)
        image = next((a.url for a in message.attachments if (a.content_type or "").startswith("image")), None)
        if image is None and message.embeds and message.embeds[0].image:
            image = message.embeds[0].image.url
        if image:
            e.set_image(url=image)
        e.set_footer(text=f"{STAR} {stars}")
        return e

    async def on_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.emoji) != STAR or payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or self.actions.stopped(guild):
            return
        board = self.channel(guild)
        if board is None or payload.channel_id == board.id:
            return
        source = guild.get_channel_or_thread(payload.channel_id)
        if source is None:
            return
        try:
            message = await source.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        reaction = discord.utils.get(message.reactions, emoji=STAR)
        stars = 0
        if reaction:
            users = [u async for u in reaction.users()]
            stars = sum(1 for u in users if u.id != message.author.id and not u.bot)  # Selbst-Sterne zählen nicht
        existing = self.data.starboard_get(guild.id, message.id)
        if existing:
            try:
                board_msg = await board.fetch_message(existing)
                await board_msg.edit(embed=self.embed(message, stars))
            except discord.HTTPException:
                pass
            return
        if stars >= self.threshold(guild.id):
            board_msg = await board.send(f"{STAR} **{message.author.display_name}** hat es in die Hall of Fame "
                                         "geschafft!", embed=self.embed(message, stars),
                                         allowed_mentions=discord.AllowedMentions.none())
            self.data.starboard_set(guild.id, message.id, board_msg.id)
            log.info("Hall of Fame: Nachricht %s mit %d Sternen", message.id, stars)
            awareness = getattr(self.actions, "awareness", None)
            if awareness:
                awareness.note_event(guild.id, f"Eine Nachricht von {message.author.display_name} aus "
                                               f"#{getattr(message.channel, 'name', '?')} ist mit {stars} ⭐ "
                                               "in der Hall of Fame gelandet.", message.channel.id)


# ---------------------------------------------------------------------
# 🛡️ Anti-Spam
# ---------------------------------------------------------------------

SCAM_PATTERNS = [
    r"\bfree\s+(discord\s+)?nitro\b", r"\bnitro\s+(for\s+)?free\b", r"gratis\s+nitro",
    r"d[i1l]sc[o0]rd(?!\.com|\.gg|\.gift|app\.com|\.media|\.new)[\w-]*\.(?:gift|com|ru|xyz|io|link|org|net|click)",
    r"dlscord|discrod|disc0rd|discorcl", r"steamcomm?un[i1]ty(?!\.com)|stearncommunity|steamcommunlty|steamcommnuity",
    r"\b(?:airdrop|claim\s+your\s+(?:reward|gift|prize))\b", r"\b(?:crypto|bitcoin|eth)\b.{0,40}\b(?:giveaway|double)\b",
    r"\bonlyfans\b.{0,30}\b(?:free|leak)", r"18\+.{0,20}(?:free|leak).{0,20}https?://",
]
_SCAM = re.compile("|".join(SCAM_PATTERNS), re.IGNORECASE)
_LINK = re.compile(r"https?://\S+", re.IGNORECASE)


class SpamDetector:
    """Merkt sich die letzten Nachrichten pro Person und entscheidet, ob etwas verdächtig ist."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.recent: dict[tuple[int, int], deque] = defaultdict(deque)  # (guild, user) -> (zeit, text, kanal)
        self.alerted: dict[tuple[int, int], float] = {}

    def check(self, guild_id: int, user_id: int, channel_id: int, text: str, mentions: int,
              everyone: bool, account_age_days: float | None = None) -> str | None:
        now = self._clock()
        q = self.recent[(guild_id, user_id)]
        q.append((now, text.strip().lower(), channel_id))
        while q and q[0][0] < now - 30:
            q.popleft()
        reason = None
        if _SCAM.search(text):
            reason = "sieht nach Scam/Phishing aus"
        elif everyone and _LINK.search(text):
            reason = "@everyone/@here mit Link"
        elif mentions >= 6:
            reason = f"Massen-Erwähnung ({mentions} Leute)"
        elif sum(1 for t, _x, _c in q if t >= now - 10) >= 6:
            reason = "sehr viele Nachrichten in kurzer Zeit"
        elif text.strip() and sum(1 for _t, x, _c in q if x == text.strip().lower()) >= 3:
            chans = {c for _t, x, c in q if x == text.strip().lower()}
            reason = "dieselbe Nachricht mehrfach" + (f" in {len(chans)} Kanälen" if len(chans) > 1 else "")
        elif account_age_days is not None and account_age_days < 3 and _LINK.search(text):
            reason = "brandneuer Account postet Links"
        if reason is None:
            return None
        if now - self.alerted.get((guild_id, user_id), -999) < 120:
            return None  # nicht spammen beim Melden von Spam
        self.alerted[(guild_id, user_id)] = now
        return reason


def mod_view(channel_id: int, message_id: int, user_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action, label, emoji, style in (("delete", "Nachricht löschen", "🗑️", discord.ButtonStyle.danger),
                                        ("timeout", "10 Min. Timeout", "🔇", discord.ButtonStyle.secondary),
                                        ("ignore", "Alles gut", "✅", discord.ButtonStyle.success)):
        view.add_item(discord.ui.Button(label=label, emoji=emoji, style=style,
                                        custom_id=f"{MOD_PREFIX}{action}:{channel_id}:{message_id}:{user_id}"))
    return view


class AntiSpam:
    def __init__(self, bot: discord.Client, data: ServerData, actions):
        self.bot, self.data, self.actions = bot, data, actions
        self.detector = SpamDetector()

    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None or message.author.bot or not self.data.get(guild.id, "antispam_on", True):
            return
        member = message.author
        if isinstance(member, discord.Member) and (member.guild_permissions.manage_messages or
                                                   member.guild_permissions.administrator):
            return  # Mods/Admins werden nicht überwacht
        age = (discord.utils.utcnow() - member.created_at).total_seconds() / 86400
        reason = self.detector.check(guild.id, member.id, message.channel.id, message.content,
                                     len(message.mentions), message.mention_everyone, age)
        if reason:
            await self.report(message, reason)

    async def report(self, message: discord.Message, reason: str) -> None:
        guild = message.guild
        channel = self.actions._log_channel(guild)
        log.warning("Anti-Spam: %s von %s in #%s", reason, message.author, message.channel)
        if channel is None:
            return
        e = discord.Embed(title="🛡️ Verdächtige Nachricht", color=0xED4245,
                          description=(message.content[:1500] or "_(kein Text)_"))
        e.add_field(name="Von", value=f"{message.author.mention} ({message.author})", inline=True)
        e.add_field(name="Wo", value=f"{message.channel.mention} · [Zur Nachricht]({message.jump_url})", inline=True)
        e.add_field(name="Warum", value=reason, inline=False)
        e.set_footer(text="Dave löscht nichts von selbst – ihr entscheidet.")
        await channel.send(embed=e, view=mod_view(message.channel.id, message.id, message.author.id),
                           allowed_mentions=discord.AllowedMentions.none())

    async def button(self, interaction: discord.Interaction) -> bool:
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith(MOD_PREFIX) or interaction.guild is None:
            return False
        try:
            action, channel_id, message_id, user_id = cid[len(MOD_PREFIX):].split(":")
            channel_id, message_id, user_id = int(channel_id), int(message_id), int(user_id)
        except ValueError:
            return True
        guild, clicker = interaction.guild, interaction.user
        perms = clicker.guild_permissions
        done = None
        if action == "ignore":
            if not perms.manage_messages:
                await interaction.response.send_message("Das dürfen nur Moderatoren.", ephemeral=True)
                return True
            done = f"✅ Als harmlos markiert von {clicker.display_name}"
        elif action == "delete":
            if not perms.manage_messages:
                await interaction.response.send_message("Dafür brauchst du „Nachrichten verwalten“.", ephemeral=True)
                return True
            ch = guild.get_channel_or_thread(channel_id)
            try:
                msg = await ch.fetch_message(message_id)
                await msg.delete()
                done = f"🗑️ Gelöscht von {clicker.display_name}"
            except discord.NotFound:
                done = "🗑️ War schon weg"
            except (discord.Forbidden, AttributeError):
                await interaction.response.send_message("Ich darf dort nichts löschen (mir fehlt „Nachrichten "
                                                        "verwalten“).", ephemeral=True)
                return True
        elif action == "timeout":
            if not perms.moderate_members:
                await interaction.response.send_message("Dafür brauchst du „Mitglieder moderieren“.", ephemeral=True)
                return True
            target = guild.get_member(user_id)
            if target is None:
                done = "🔇 Person ist nicht mehr auf dem Server"
            elif target.top_role >= guild.me.top_role or target.guild_permissions.administrator or target == guild.owner:
                await interaction.response.send_message("Die Person steht über mir oder ist Admin – das geht nicht.",
                                                        ephemeral=True)
                return True
            else:
                try:
                    await target.timeout(timedelta(minutes=10), reason=f"Spam-Verdacht, bestätigt von {clicker}")
                    done = f"🔇 10 Min. Timeout von {clicker.display_name}"
                except discord.Forbidden:
                    await interaction.response.send_message("Mir fehlt „Mitglieder moderieren“.", ephemeral=True)
                    return True
        if done is None:
            return True
        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = 0x57F287 if action == "ignore" else 0x95A5A6
        embed.add_field(name="Erledigt", value=done, inline=False)
        await interaction.response.edit_message(embed=embed, view=None)
        await self.actions.audit(guild, f"🛡️ {done} (Nachricht {message_id})")
        return True


def register_guard(tree: app_commands.CommandTree, data: ServerData, actions) -> None:
    admin = next((c for c in tree.get_commands() if c.name == "dave"), None)
    if admin is None:
        return

    @admin.command(name="hall_of_fame", description="Wie viele ⭐ eine Nachricht für die Hall of Fame braucht")
    async def hall_of_fame(interaction: discord.Interaction, sterne: app_commands.Range[int, 1, 20]):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Das dürfen nur Admins.", ephemeral=True)
            return
        data.set(interaction.guild_id, "hof_stars", sterne)
        await interaction.response.send_message(f"Ab jetzt reichen **{sterne}** ⭐ für die Hall of Fame.",
                                                ephemeral=True)

    @admin.command(name="anti_spam", description="Anti-Spam-Meldungen an/aus")
    async def anti_spam(interaction: discord.Interaction, an: bool):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Das dürfen nur Admins.", ephemeral=True)
            return
        data.set(interaction.guild_id, "antispam_on", an)
        await interaction.response.send_message(f"Anti-Spam ist jetzt {'an' if an else 'aus'}.", ephemeral=True)
        await actions.audit(interaction.guild, f"⚙️ Anti-Spam {'an' if an else 'aus'} von {interaction.user}")

    @admin.command(name="frage_des_tages", description="Frage des Tages an/aus (jeden Abend um 19 Uhr)")
    async def frage_des_tages(interaction: discord.Interaction, an: bool, kanal: discord.TextChannel | None = None):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Das dürfen nur Admins.", ephemeral=True)
            return
        if kanal:
            data.set(interaction.guild_id, "qotd_channel", kanal.id)
        data.set(interaction.guild_id, "qotd_on", an)
        await interaction.response.send_message(f"Frage des Tages ist jetzt {'an' if an else 'aus'}.", ephemeral=True)
