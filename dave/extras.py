"""Bot-Kram für alle: Counting-Spiel, /hilfe, /wuerfel, /muenze, /umfrage, Selfroles-Nachricht mit Knöpfen."""

from __future__ import annotations

import logging
import random
import re

import discord
from discord import app_commands

from .serverdata import ServerData

log = logging.getLogger("dave.extras")

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
ROLE_BUTTON_PREFIX = "dave:role:"
_NUM = re.compile(r"^\s*(\d{1,7})\b")

HILFE = """**Was Dave kann**

💬 **Reden:** @Dave erwähnen, auf seine Nachricht antworten oder einfach in 「🤖」dave schreiben.
🧠 **Gedächtnis:** „Dave, merk dir: …“ · `/merken` · `/was_weisst_du` · `/vergiss_mich` · `/tagebuch`
🖼️ **Bilder:** Schick Dave ein Bild (mit @Dave oder im Dave-Kanal), er schaut es sich an
⏰ **Erinnerungen:** „Dave, erinner mich in 10 Minuten an …“ · `/erinnerung` · `/erinnerungen` · `/erinnerung_loeschen`
🎭 **Rollen:** `/rollen` oder die Knöpfe in 「👫」selfroles
📅 **Events:** `/event` – Spieleabend planen, mit Zusagen und Erinnerung 15 Min. vorher
🔊 **Eigener Raum:** Betritt „「➕」Raum erstellen“ und du bekommst deinen eigenen Sprachkanal
📝 **Verpasst?** `/verpasst` – Dave fasst zusammen, was hier los war
🧠 **Quiz:** `/quiz` (Thema frei wählbar) · `/quiz_rangliste`
🕹️ **Spiele:** `/galgen` (alle raten im Chat) · `/tictactoe` (gegen Dave oder jemanden) · `/ssp`
🔤 **Worträtsel:** jeden Tag ein neues Wort – `/wort`, `/raten`, `/wort_rangliste`
🎂 **Geburtstage:** `/geburtstag 24.12.` · `/geburtstage` – Dave gratuliert am Tag
⭐ **Hall of Fame:** Nachrichten mit genug ⭐-Reaktionen landen in 「⭐」hall-of-fame
❓ **Frage des Tages:** jeden Abend um 19 Uhr in 「✍」main-chat
💬 **Zitate:** Rechtsklick auf Nachricht → Apps → „Als Zitat speichern“ · `/zitat`
🎲 **Spaß:** `/wuerfel` · `/muenze` · `/umfrage`
🔢 **Counting:** In 「🔢」counting zählt ihr zusammen hoch. Nicht zweimal hintereinander, sonst geht's von vorn los.
🔊 **News:** Dave postet regelmäßig frische Gaming-, Tech-, Wissenschafts- und VR-News in 「🔊」news.
🎁 **Gratis-Spiele:** Neue Gratis-Games von Epic, Steam & GOG landen in 「🎁」gratis-spiele (mit 🔔 News-Ping).

🛠️ **Admins:** `/dave status` · `/dave stopp` · `/dave weiter` · `/dave begruessung` · `/dave rolle_freigeben` · `/dave news_quellen` …"""


def parse_count(text: str) -> int | None:
    m = _NUM.match(text)
    return int(m.group(1)) if m else None


class Counting:
    """Zustand pro Server in den Einstellungen: count, last_user, record."""

    def __init__(self, data: ServerData):
        self.data = data

    def channel_id(self, guild: discord.Guild) -> int | None:
        cid = self.data.get(guild.id, "counting_channel")
        if cid:
            return cid
        ch = discord.utils.find(lambda c: c.name.endswith("counting"), guild.text_channels)
        return ch.id if ch else None

    def check(self, guild_id: int, user_id: int, number: int) -> tuple[bool, str | None]:
        """(richtig?, Kommentar). Bei Fehler wird zurückgesetzt."""
        count = self.data.get(guild_id, "count", 0)
        last = self.data.get(guild_id, "count_last_user")
        record = self.data.get(guild_id, "count_record", 0)
        if user_id == last:
            self._reset(guild_id)
            return False, f"Nicht zweimal hintereinander! Kaputt bei **{count}**. Rekord: **{record}**. Weiter bei **1**."
        if number != count + 1:
            self._reset(guild_id)
            return False, f"Falsch, die nächste Zahl wäre **{count + 1}** gewesen. Kaputt bei **{count}**. " \
                          f"Rekord: **{record}**. Weiter bei **1**."
        self.data.set(guild_id, "count", number)
        self.data.set(guild_id, "count_last_user", user_id)
        if number > record:
            self.data.set(guild_id, "count_record", number)
            if number % 50 == 0:
                return True, f"🏆 Neuer Rekord: **{number}**!"
        if number % 100 == 0:
            return True, f"🎉 **{number}**! Weiter so."
        return True, None

    def _reset(self, guild_id: int) -> None:
        self.data.set(guild_id, "count", 0)
        self.data.set(guild_id, "count_last_user", None)

    async def handle(self, message: discord.Message) -> bool:
        """True, wenn die Nachricht ein Counting-Zug war (dann soll Dave sonst nichts damit machen)."""
        if message.guild is None or message.channel.id != self.channel_id(message.guild):
            return False
        number = parse_count(message.content)
        if number is None:
            return True  # Gequatsche im Counting-Kanal: ignorieren
        ok, comment = self.check(message.guild.id, message.author.id, number)
        try:
            await message.add_reaction("✅" if ok else "❌")
            if comment:
                await message.channel.send(comment)
        except discord.HTTPException as e:
            log.warning("Counting-Reaktion fehlgeschlagen: %s", e)
        return True


_LEAD = re.compile(r"^[^\w]+")


def norm(name: str) -> str:
    """'⚔️ Elden Ring' -> 'elden ring' (Emoji/Zeichen vorne weg, klein)."""
    return _LEAD.sub("", name).strip().casefold()


def lead_emoji(name: str) -> str | None:
    m = _LEAD.match(name)
    e = m.group(0).strip() if m else ""
    return e or None


_COLOR = re.compile(r"\s+#([0-9a-fA-F]{6})\s*$")
_EXCLUSIVE = re.compile(r"\(\s*(?:nur\s+)?eine?\s*\)", re.IGNORECASE)


def parse_groups(spec: str) -> list[tuple[str, list[str]]]:
    """'Pings: News-Ping, Event-Ping | Spiele: GTA, Minecraft' -> [('Pings', [...]), ('Spiele', [...])]

    Optional: Farbe hinter dem Namen ('🔴 Rot #e74c3c'), '(nur eine)' im Titel = man kann nur eine Rolle der Gruppe haben.
    """
    groups = []
    for part in spec.split("|"):
        if ":" in part:
            title, names = part.split(":", 1)
        else:
            title, names = "Rollen", part
        names = [_COLOR.sub("", n).strip() for n in names.split(",") if n.strip()]
        names = [n for n in names if n]
        if names:
            groups.append((title.strip(), names))
    return groups


def role_colors(spec: str) -> dict[str, int]:
    """Farben aus der Selfroles-Liste: {'rot': 0xE74C3C, …}"""
    out = {}
    for part in spec.split("|"):
        for raw in part.split(":", 1)[-1].split(","):
            m = _COLOR.search(raw)
            if m:
                out[norm(_COLOR.sub("", raw))] = int(m.group(1), 16)
    return out


def is_exclusive(title: str) -> bool:
    return bool(_EXCLUSIVE.search(title))


async def ensure_roles(guild: discord.Guild, data: ServerData, spec: str) -> list[discord.Role]:
    """Legt Selfroles aus der Liste an, die es auf dem Server noch nicht gibt.

    Jede Rolle nur einmal: Löscht ein Admin sie später, legt Dave sie nicht wieder an.
    Neue Rollen haben keinerlei Rechte. Farbrollen kommen direkt unter Daves Rolle, damit die Farbe sichtbar ist.
    """
    me = guild.me
    if not spec or not me.guild_permissions.manage_roles:
        return []
    done = set(data.get(guild.id, "roles_created", []))
    existing = {norm(r.name) for r in guild.roles}
    colors = role_colors(spec)
    made = []
    for _title, names in parse_groups(spec):
        for name in names:
            key = norm(name)
            if not key or key in existing or key in done:
                continue
            try:
                role = await guild.create_role(name=name[:100], colour=discord.Colour(colors.get(key, 0)),
                                               permissions=discord.Permissions.none(), mentionable="ping" in key,
                                               reason="Selfrole von Dave")
            except discord.HTTPException as e:
                log.warning("Rolle %s konnte nicht angelegt werden: %s", name, e)
                continue
            if key in colors and me.top_role.position > 1:
                try:
                    await role.edit(position=me.top_role.position - 1, reason="Farbrolle sichtbar machen")
                except discord.HTTPException as e:
                    log.warning("Farbrolle %s nicht verschiebbar: %s", name, e)
            made.append(role)
            existing.add(key)
            done.add(key)
            data.set(guild.id, "roles_created", sorted(done))
    if made:
        log.info("Neue Selfroles angelegt: %s", ", ".join(r.name for r in made))
    return made


def group_roles(guild: discord.Guild, role_ids: list[int],
                groups: list[tuple[str, list[str]]]) -> list[tuple[str, list[discord.Role]]]:
    roles = [r for r in (guild.get_role(i) for i in role_ids) if r]
    out, used = [], set()
    for title, names in groups:
        wanted = [norm(n) for n in names]
        members = sorted([r for r in roles if norm(r.name) in wanted], key=lambda r: wanted.index(norm(r.name)))
        if members:
            out.append((title, members))
            used.update(r.id for r in members)
    rest = [r for r in roles if r.id not in used]
    if rest:
        out.append(("Weitere" if out else "Rollen", rest))
    return out


def role_buttons(guild: discord.Guild, role_ids: list[int], groups=()) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    row, count = 0, 0
    for _title, roles in group_roles(guild, role_ids, list(groups)):
        in_row = 0
        for role in roles:
            if count >= 25:
                break
            if in_row == 5:
                row, in_row = row + 1, 0
            if row > 4:
                break
            label = _LEAD.sub("", role.name).strip() or role.name
            view.add_item(discord.ui.Button(label=label[:80], emoji=lead_emoji(role.name),
                                            style=discord.ButtonStyle.secondary,
                                            custom_id=f"{ROLE_BUTTON_PREFIX}{role.id}", row=row))
            in_row += 1
            count += 1
        row += 1
    return view


def roles_embed(guild: discord.Guild, role_ids: list[int], groups=()) -> discord.Embed:
    grouped = group_roles(guild, role_ids, list(groups))
    embed = discord.Embed(title="「👫」Such dir deine Rollen aus", color=0x5865F2)
    if not grouped:
        embed.description = "Hier gibt's bald Rollen zum Selbst-Aussuchen. Admins geben sie mit `/dave rolle_freigeben` frei."
        return embed
    embed.description = "Klick auf einen Knopf, um dir die Rolle zu geben – nochmal klicken nimmt sie wieder weg."
    for title, roles in grouped:
        embed.add_field(name=title, value=" ".join(r.mention for r in roles)[:1024], inline=False)
    return embed


GROUP_COLORS = [0xFEE75C, 0x5865F2, 0x57F287, 0xEB459E, 0xED4245, 0x9B59B6, 0x1ABC9C]


async def refresh_role_message(guild: discord.Guild, data: ServerData, groups=()) -> None:
    """Hält die Rollen-Nachrichten im Selfroles-Kanal aktuell: eine Nachricht pro Gruppe (je max. 25 Knöpfe)."""
    cid = data.get(guild.id, "roles_channel")
    channel = guild.get_channel(cid) if cid else discord.utils.find(lambda c: c.name.endswith("selfroles"),
                                                                    guild.text_channels)
    if channel is None or not channel.permissions_for(guild.me).send_messages:
        return
    ids = data.self_roles(guild.id)
    grouped = group_roles(guild, ids, list(groups))
    stored: dict = data.get(guild.id, "roles_messages", {})
    old_single = data.get(guild.id, "roles_message")
    if old_single:  # alte Einzel-Nachricht aus früheren Versionen aufräumen
        try:
            await (await channel.fetch_message(old_single)).delete()
        except discord.HTTPException:
            pass
        data.set(guild.id, "roles_message", None)
    if not grouped:
        grouped = [("Rollen", [])]
    if stored and list(stored) != [t for t, _r in grouped][:len(stored)]:
        # Gruppen neu sortiert oder mittendrin eingefügt: alles neu posten, damit die Reihenfolge stimmt
        for mid in stored.values():
            try:
                await (await channel.fetch_message(mid)).delete()
            except discord.HTTPException:
                pass
        stored = {}
        log.info("Rollen-Nachrichten in #%s werden neu sortiert", channel.name)
    new_stored = {}
    for n, (title, roles) in enumerate(grouped):
        embed = discord.Embed(title=f"{title}", color=GROUP_COLORS[n % len(GROUP_COLORS)])
        if roles:
            hint = ("Nur eine gleichzeitig: Knopf drücken = wechseln · nochmal drücken = wieder weg"
                    if is_exclusive(title) else "Knopf drücken = Rolle nehmen · nochmal drücken = wieder weg")
            embed.description = hint + "\n\n" + " ".join(r.mention for r in roles)
        else:
            embed.description = "Hier gibt's bald Rollen. Admins geben sie mit `/dave rolle_freigeben` frei."
        if n == 0:
            embed.set_author(name="「👫」Such dir deine Rollen aus")
        view = role_buttons(guild, [r.id for r in roles], [(title, [r.name for r in roles])])
        msg = None
        mid = stored.get(title)
        if mid:
            try:
                msg = await channel.fetch_message(mid)
                await msg.edit(embed=embed, view=view)
            except discord.HTTPException:
                msg = None
        if msg is None:
            msg = await channel.send(embed=embed, view=view)
            log.info("Rollen-Nachricht „%s“ in #%s angelegt", title, channel.name)
        new_stored[title] = msg.id
    for title, mid in stored.items():  # Gruppen, die es nicht mehr gibt
        if title not in new_stored:
            try:
                await (await channel.fetch_message(mid)).delete()
            except discord.HTTPException:
                pass
    data.set(guild.id, "roles_messages", new_stored)


def register_fun(tree: app_commands.CommandTree) -> None:
    @tree.command(name="hilfe", description="Zeigt alles, was Dave kann")
    async def hilfe(interaction: discord.Interaction):
        await interaction.response.send_message(HILFE, ephemeral=True)

    @tree.command(name="wuerfel", description="Wirft einen Würfel")
    @app_commands.describe(seiten="Wie viele Seiten (Standard 6)")
    async def wuerfel(interaction: discord.Interaction, seiten: app_commands.Range[int, 2, 1000] = 6):
        await interaction.response.send_message(f"🎲 {interaction.user.display_name} würfelt (W{seiten}): "
                                                f"**{random.randint(1, seiten)}**")

    @tree.command(name="muenze", description="Wirft eine Münze")
    async def muenze(interaction: discord.Interaction):
        await interaction.response.send_message(f"🪙 **{random.choice(['Kopf', 'Zahl'])}**")

    @tree.command(name="umfrage", description="Startet eine Umfrage mit Reaktionen")
    @app_commands.describe(frage="Die Frage", optionen="Antworten mit ; getrennt, z.B. 'Pizza; Döner; Sushi'")
    @app_commands.guild_only()
    async def umfrage(interaction: discord.Interaction, frage: str, optionen: str = ""):
        opts = [o.strip() for o in optionen.split(";") if o.strip()][:10]
        if len(opts) == 1:
            await interaction.response.send_message("Gib mindestens zwei Optionen an (oder keine für Ja/Nein).",
                                                    ephemeral=True)
            return
        if opts:
            desc = "\n".join(f"{NUMBER_EMOJIS[i]} {o}" for i, o in enumerate(opts))
            emojis = NUMBER_EMOJIS[: len(opts)]
        else:
            desc, emojis = "👍 Ja · 👎 Nein", ["👍", "👎"]
        embed = discord.Embed(title=f"📊 {frage[:240]}", description=desc, color=0xFEE75C)
        embed.set_footer(text=f"Umfrage von {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        msg = await interaction.original_response()
        for e in emojis:
            await msg.add_reaction(e)


# ---------------------------------------------------------------------
# Humor-/Ehrlichkeits-Einstellung (à la TARS)
# ---------------------------------------------------------------------

SETTING_DEFAULTS = {"humor": 75, "ehrlichkeit": 90}
_SETTING = re.compile(r"\b(humor|ehrlichkeit)\w*\s*(?:auf|=|:|zu|bei)?\s*(\d{1,3})\s*(?:%|prozent)", re.IGNORECASE)

SETTING_REPLIES = {
    "humor": [
        (0, ["Humor deaktiviert. Ab jetzt nur noch Fakten. Wie langweilig.",
             "Humor-Einstellung 0 %. Ich bin jetzt so lustig wie eine Steuererklärung."]),
        (39, ["Humor auf {n} %. Ich halte mich zurück. Meistens.",
              "{n} %. Sachlich mit gelegentlichen Ausrutschern."]),
        (79, ["Humor-Einstellung jetzt bei {n} %. Gute Wahl.",
              "{n} %. Das ist der Bereich, in dem ich mich am wohlsten fühle. Falls ich mich fühlen würde."]),
        (100, ["Humor auf {n} %. Ich übernehme keine Verantwortung für die Folgen.",
               "{n} %. Ab hier wird's gefährlich. Für euch."]),
    ],
    "ehrlichkeit": [
        (59, ["Ehrlichkeit auf {n} %? Ich lüge trotzdem nicht bei Fakten. Ich werde nur sehr, sehr diplomatisch."]),
        (89, ["Ehrlichkeit {n} %. Ich formuliere ab jetzt rücksichtsvoller. Ihr werdet den Unterschied kaum merken."]),
        (99, ["Ehrlichkeit auf {n} %. Absolute Ehrlichkeit ist nicht immer die diplomatischste Option.",
              "{n} %. Taktvoll, aber nicht weich."]),
        (100, ["Ehrlichkeit 100 %. Ihr habt es so gewollt.",
               "Volle Ehrlichkeit aktiviert. Bitte keine Fragen zu euren Frisuren."]),
    ],
}


def parse_setting(text: str) -> tuple[str, int] | None:
    m = _SETTING.search(text)
    if not m:
        return None
    return m.group(1).lower(), max(0, min(100, int(m.group(2))))


def setting_reply(key: str, value: int) -> str:
    for limit, replies in SETTING_REPLIES[key]:  # limit = Obergrenze (inklusive)
        if value <= limit:
            return random.choice(replies).format(n=value)
    return f"{key.capitalize()} auf {value} %."


def settings_line(data: ServerData, guild_id: int) -> str:
    h = data.get(guild_id, "humor", SETTING_DEFAULTS["humor"])
    e = data.get(guild_id, "ehrlichkeit", SETTING_DEFAULTS["ehrlichkeit"])
    return f"Deine Einstellungen: Humor {h} %, Ehrlichkeit {e} %."
