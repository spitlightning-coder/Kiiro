"""Tägliches: Geburtstage (9 Uhr) und Frage des Tages (19 Uhr)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import date, datetime

import discord
from discord import app_commands

from .actions import TZ
from .extras import norm
from .serverdata import ServerData

log = logging.getLogger("dave.daily")

BIRTHDAY_HOUR = 9
QOTD_HOUR = 19
BIRTHDAY_ROLE = "🎂 Geburtstagskind"
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember"]

QOTD_SYSTEM = """Du denkst dir die "Frage des Tages" für einen lockeren deutschen Gaming- und Community-Discord aus.
- Genau EINE Frage, kurz (max. 120 Zeichen), die viele Leute gern beantworten: "Was wäre wenn", Vorlieben, Gaming,
  Filme, Essen, Weltraum, lustige Gedankenspiele.
- Nichts Heikles (Politik, Religion, Beziehungen, Geld, Gesundheit), keine Ja/Nein-Fragen.
- Antworte NUR mit der Frage, ohne Anführungszeichen."""

QOTD_FALLBACK = [
    "Welches Spiel würdest du gern zum ersten Mal nochmal spielen können?",
    "Wenn du eine Superkraft haben könntest – welche und warum?",
    "Welches Essen könntest du jeden Tag essen, ohne dass es dir langweilig wird?",
    "Auf welchen Planeten würdest du als Erstes reisen, wenn es ginge?",
    "Welcher Film ist komplett unterbewertet?",
    "Welcher Videospiel-Charakter wäre der beste Mitbewohner?",
    "Was ist dein absolutes Comfort-Game, wenn der Tag mies war?",
    "Wenn du in einem Spieluniversum leben müsstest – in welchem?",
    "Welche Erfindung aus Sci-Fi hättest du am liebsten heute schon?",
    "Was war dein erstes Videospiel überhaupt?",
    "Welches Lied läuft bei dir gerade in Dauerschleife?",
    "Welcher Soundtrack ist der beste aller Zeiten?",
    "Was würdest du mit einer Million Euro als Erstes machen – aber nur Spaß-Sachen?",
    "Welches Spiel hat das beste Ende, das du kennst?",
    "Zeitreise: Vergangenheit oder Zukunft – und wohin genau?",
    "Welche Serie sollte jeder mal gesehen haben?",
    "Was ist das Nerdigste, das du besitzt?",
    "Welches Tier wäre das beste Haustier, wenn alles erlaubt wäre?",
    "Welcher Bosskampf hat dich am meisten Nerven gekostet?",
    "Welche drei Dinge nimmst du auf eine einsame Insel mit?",
    "Welches Spiel würdest du gern als Film sehen – und wer spielt mit?",
    "Was ist deine unpopulärste Gaming-Meinung?",
    "Pizza mit Ananas: Verbrechen oder Genie?",
    "Welche Fähigkeit würdest du gern sofort perfekt können?",
    "Welche Spielwelt hat dich am meisten beeindruckt?",
    "Was ist dein Lieblings-Snack beim Zocken?",
    "Welches Lichtschwert hättest du gern – Farbe und Form?",
    "Was würdest du einem Alien als Erstes über die Erde zeigen?",
    "Welches Spiel hast du am längsten gespielt?",
    "Welcher Ort auf der Welt steht ganz oben auf deiner Reiseliste?",
]

_DATE = re.compile(r"^\s*(\d{1,2})\s*[.\-/ ]\s*(\d{1,2})\s*\.?\s*(?:\d{2,4})?\s*$")


def parse_birthday(text: str) -> tuple[int, int] | None:
    """'24.12.' / '24.12.2005' / '3.7' -> (Tag, Monat). Das Jahr wird bewusst ignoriert (kein Alter speichern)."""
    m = _DATE.match(text)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    try:
        date(2024, month, day)  # Schaltjahr -> 29.2. erlaubt
    except ValueError:
        return None
    return day, month


def is_birthday(day: int, month: int, today: date) -> bool:
    if (day, month) == (today.day, today.month):
        return True
    # 29.2. feiern wir in Nicht-Schaltjahren am 28.2.
    leap = today.year % 4 == 0 and (today.year % 100 != 0 or today.year % 400 == 0)
    return (day, month) == (29, 2) and not leap and (today.day, today.month) == (28, 2)


def days_until(day: int, month: int, today: date) -> int:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            d = date(year, 2, 28)
        if d >= today:
            return (d - today).days
    return 365


class Daily:
    def __init__(self, bot: discord.Client, data: ServerData, brain, actions):
        self.bot, self.data, self.brain, self.actions = bot, data, brain, actions
        self._task: asyncio.Task | None = None

    def main_channel(self, guild: discord.Guild, key: str) -> discord.TextChannel | None:
        cid = self.data.get(guild.id, key)
        ch = guild.get_channel(cid) if cid else discord.utils.find(lambda c: c.name.endswith("main-chat"),
                                                                   guild.text_channels)
        return ch if ch and ch.permissions_for(guild.me).send_messages else None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(TZ)
            today = now.strftime("%Y-%m-%d")
            for guild in list(self.bot.guilds):
                try:
                    if now.hour >= BIRTHDAY_HOUR and self.data.get(guild.id, "bday_last") != today:
                        self.data.set(guild.id, "bday_last", today)
                        await self.birthdays(guild, now.date())
                    if self.data.get(guild.id, "qotd_last") is None:  # erster Start: erst morgen loslegen
                        self.data.set(guild.id, "qotd_last", today)
                    if now.hour >= QOTD_HOUR and self.data.get(guild.id, "qotd_last") != today:
                        self.data.set(guild.id, "qotd_last", today)
                        if self.data.get(guild.id, "qotd_on", True):
                            await self.question(guild)
                except Exception:  # noqa: BLE001
                    log.exception("Tägliche Aufgaben fehlgeschlagen")
            await asyncio.sleep(60)

    # ---------- Geburtstage ----------

    async def birthday_role(self, guild: discord.Guild) -> discord.Role | None:
        role = discord.utils.find(lambda r: norm(r.name) == norm(BIRTHDAY_ROLE), guild.roles)
        if role is None and guild.me.guild_permissions.manage_roles:
            try:
                role = await guild.create_role(name=BIRTHDAY_ROLE, colour=discord.Colour(0xEB459E), hoist=True,
                                               reason="Geburtstagsrolle für Dave")
            except discord.HTTPException as e:
                log.warning("Geburtstagsrolle konnte nicht erstellt werden: %s", e)
        return role

    async def birthdays(self, guild: discord.Guild, today: date) -> list[int]:
        if self.actions.stopped(guild):
            return []
        kids = [uid for uid, d, m in self.data.birthdays(guild.id) if is_birthday(d, m, today)]
        role = await self.birthday_role(guild)
        if role:  # Rolle von gestern abnehmen, heute vergeben
            for member in list(role.members):
                if member.id not in kids:
                    try:
                        await member.remove_roles(role, reason="Geburtstag vorbei")
                    except discord.HTTPException:
                        pass
        members = [m for m in (guild.get_member(u) for u in kids) if m]
        if not members:
            return []
        if role:
            for m in members:
                try:
                    await m.add_roles(role, reason="Geburtstag 🎂")
                except discord.HTTPException:
                    pass
        channel = self.main_channel(guild, "bday_channel")
        if channel is None:
            return [m.id for m in members]
        names = ", ".join(m.display_name for m in members)
        prompt = [{"role": "user", "content": f"System: Heute hat {names} Geburtstag. Schreib einen kurzen, "
                                              "herzlichen und lockeren Glückwunsch (1-2 Sätze, gern mit einem kleinen "
                                              "Witz). Keine Altersangaben. Schreib nur den Glückwunsch."}]
        text = None
        try:
            text = await self.brain.reply(prompt, server=guild.name, channel=channel.name)
        except Exception:  # noqa: BLE001
            pass
        if not text or text.startswith(("Mein Hirn", "Ich werde gerade")):
            text = "Alles Gute zum Geburtstag! Lass dich feiern! 🥳"
        embed = discord.Embed(title="🎂 Geburtstag!", description=text, color=0xEB459E)
        await channel.send(" ".join(m.mention for m in members), embed=embed,
                           allowed_mentions=discord.AllowedMentions(users=members, roles=False, everyone=False))
        await self.actions.audit(guild, f"🎂 Geburtstag: {names}")
        return [m.id for m in members]

    # ---------- Frage des Tages ----------

    async def question(self, guild: discord.Guild) -> str | None:
        if self.actions.stopped(guild):
            return None
        channel = self.main_channel(guild, "qotd_channel")
        if channel is None:
            return None
        recent: list[str] = self.data.get(guild.id, "qotd_recent", [])
        q = None
        try:
            avoid = "\n".join(f"- {r}" for r in recent[-15:])
            raw = await self.brain.complete(QOTD_SYSTEM, f"Nicht wiederholen:\n{avoid}" if avoid else "Los geht's.",
                                            max_tokens=120)
            if raw:
                q = raw.strip().strip('"„“').splitlines()[0][:200]
        except Exception:  # noqa: BLE001
            pass
        if not q or "?" not in q or q in recent:
            unused = [f for f in QOTD_FALLBACK if f not in recent] or QOTD_FALLBACK
            q = random.choice(unused)
        self.data.set(guild.id, "qotd_recent", (recent + [q])[-40:])
        embed = discord.Embed(title="❓ Frage des Tages", description=f"**{q}**", color=0xFEE75C)
        embed.set_footer(text="Antwortet einfach hier im Chat!")
        await channel.send(embed=embed)
        return q


def register_daily(tree: app_commands.CommandTree, daily: Daily) -> None:
    @tree.command(name="geburtstag", description="Trag deinen Geburtstag ein – Dave gratuliert dir am Tag")
    @app_commands.describe(datum="z.B. 24.12. (Jahr wird nicht gespeichert)")
    @app_commands.guild_only()
    async def geburtstag(interaction: discord.Interaction, datum: str):
        parsed = parse_birthday(datum)
        if not parsed:
            await interaction.response.send_message("Das Datum hab ich nicht verstanden. Probier z.B. `24.12.`",
                                                    ephemeral=True)
            return
        day, month = parsed
        daily.data.set_birthday(interaction.guild_id, interaction.user.id, day, month)
        await interaction.response.send_message(f"🎂 Gespeichert: **{day}. {MONATE[month - 1]}**. "
                                                "Ich gratuliere dir am Tag! (Löschen: `/geburtstag_loeschen`)",
                                                ephemeral=True)

    @tree.command(name="geburtstag_loeschen", description="Löscht deinen Geburtstag wieder")
    @app_commands.guild_only()
    async def geburtstag_loeschen(interaction: discord.Interaction):
        ok = daily.data.delete_birthday(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message("Gelöscht." if ok else "Du hattest keinen eingetragen.", ephemeral=True)

    @tree.command(name="geburtstage", description="Die nächsten Geburtstage auf dem Server")
    @app_commands.guild_only()
    async def geburtstage(interaction: discord.Interaction):
        today = datetime.now(TZ).date()
        rows = []
        for uid, d, m in daily.data.birthdays(interaction.guild_id):
            member = interaction.guild.get_member(uid)
            if member:
                rows.append((days_until(d, m, today), d, m, member.display_name))
        if not rows:
            await interaction.response.send_message("Noch keine Geburtstage eingetragen. Mach den Anfang mit "
                                                    "`/geburtstag`!", ephemeral=True)
            return
        rows.sort()
        lines = [f"{'🎉 **heute**' if n == 0 else f'in {n} Tag' + ('en' if n != 1 else '')} · "
                 f"**{name}** ({d}. {MONATE[m - 1]})" for n, d, m, name in rows[:15]]
        await interaction.response.send_message(embed=discord.Embed(title="🎂 Nächste Geburtstage",
                                                                    description="\n".join(lines), color=0xEB459E),
                                                allowed_mentions=discord.AllowedMentions.none())
