"""Weitere Mini-Spiele: Minesweeper (Discord-Spoiler) und Emoji-Rätsel (wer's zuerst errät)."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import random
import re
import time

import discord
from discord import app_commands

from .brain_openai import clean

log = logging.getLogger("dave.minigames")

# ---------------------------------------------------------------------
# Minesweeper
# ---------------------------------------------------------------------

LEVELS = {"leicht": (6, 6, 5), "mittel": (8, 8, 10), "schwer": (9, 9, 15)}
NUMBERS = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
MINE = "💣"


def make_field(rows: int, cols: int, mines: int, rng: random.Random | None = None) -> list[list[int]]:
    """-1 = Mine, sonst Anzahl der Minen ringsum."""
    rng = rng or random
    cells = [(r, c) for r in range(rows) for c in range(cols)]
    mine_set = set(rng.sample(cells, mines))
    field = [[0] * cols for _ in range(rows)]
    for r, c in cells:
        if (r, c) in mine_set:
            field[r][c] = -1
            continue
        field[r][c] = sum((r + dr, c + dc) in mine_set for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc)
    return field


def render_field(field: list[list[int]], rng: random.Random | None = None) -> str:
    """Alles hinter Spoilern – bis auf ein sicheres Startfeld (am liebsten eine 0)."""
    rng = rng or random
    zeros = [(r, c) for r, row in enumerate(field) for c, v in enumerate(row) if v == 0]
    safe = [(r, c) for r, row in enumerate(field) for c, v in enumerate(row) if v >= 0]
    start = rng.choice(zeros or safe)
    lines = []
    for r, row in enumerate(field):
        cells = []
        for c, v in enumerate(row):
            emoji = MINE if v < 0 else NUMBERS[v]
            cells.append(emoji if (r, c) == start else f"||{emoji}||")
        lines.append("".join(cells))
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Emoji-Rätsel
# ---------------------------------------------------------------------

BUILTIN = [
    ("🦇👨🌃", "Batman", ["the batman", "the dark knight"], "Film"),
    ("🧙‍♂️💍🌋", "Der Herr der Ringe", ["herr der ringe", "lord of the rings"], "Film"),
    ("🦁👑", "Der König der Löwen", ["könig der löwen", "the lion king", "lion king"], "Film"),
    ("🚢🧊💔", "Titanic", [], "Film"),
    ("🦖🏝️🧬", "Jurassic Park", ["jurassic world"], "Film"),
    ("👽📞🏠", "E.T.", ["et", "e.t. der außerirdische"], "Film"),
    ("🕷️🧑", "Spider-Man", ["spiderman"], "Film"),
    ("🧊👸❄️", "Die Eiskönigin", ["frozen", "eiskönigin"], "Film"),
    ("🤖🌱🚀", "WALL-E", ["wall e", "walle"], "Film"),
    ("🐠🔍", "Findet Nemo", ["nemo"], "Film"),
    ("👻🔫🚫", "Ghostbusters", [], "Film"),
    ("🧑‍🚀🌽🕳️⏳", "Interstellar", [], "Film"),
    ("💊🕶️🐇", "Matrix", ["the matrix"], "Film"),
    ("🏴‍☠️💀🌊", "Fluch der Karibik", ["pirates of the caribbean", "pirates of the carribean"], "Film"),
    ("🧸🤠🚀", "Toy Story", [], "Film"),
    ("⚡👓🧙", "Harry Potter", [], "Film"),
    ("🐀👨‍🍳🇫🇷", "Ratatouille", [], "Film"),
    ("🔨⚡🌩️", "Thor", [], "Film"),
    ("⛏️🧱🐷", "Minecraft", [], "Spiel"),
    ("🚗💰🔫🌴", "GTA", ["grand theft auto", "gta 5", "gta v", "gta 6"], "Spiel"),
    ("🍄👨‍🔧👸", "Super Mario", ["mario", "super mario bros"], "Spiel"),
    ("⚡🐭🔴⚪", "Pokémon", ["pokemon"], "Spiel"),
    ("🗡️🛡️🧝‍♂️🔺", "Zelda", ["the legend of zelda", "legend of zelda"], "Spiel"),
    ("🏝️🪂🔫🏗️", "Fortnite", [], "Spiel"),
    ("🚀⚽🚗", "Rocket League", [], "Spiel"),
    ("👨‍🚀🔪🚨🛸", "Among Us", [], "Spiel"),
    ("🦔💨💍", "Sonic", ["sonic the hedgehog"], "Spiel"),
    ("👻🟡🍒", "Pac-Man", ["pacman", "pac man"], "Spiel"),
    ("🟦🟨🟥⬇️", "Tetris", [], "Spiel"),
    ("💀🧟🌆🔦", "Dying Light", [], "Spiel"),
    ("🐉⚔️💍🌳", "Elden Ring", [], "Spiel"),
    ("⛏️🍺🪨🐛", "Deep Rock Galactic", [], "Spiel"),
    ("🧪👨‍🏫💵🚐", "Breaking Bad", [], "Serie"),
    ("🐉👑❄️🗡️", "Game of Thrones", ["got"], "Serie"),
    ("🚲🔦👾🙃", "Stranger Things", [], "Serie"),
    ("🧟‍♂️🚶‍♂️🔫", "The Walking Dead", ["walking dead"], "Serie"),
    ("💰🎭🏦🔴", "Haus des Geldes", ["money heist", "la casa de papel"], "Serie"),
    ("🦑🎮💰", "Squid Game", [], "Serie"),
    ("☕🛋️👫👫👫", "Friends", [], "Serie"),
    ("🟡👨‍👩‍👧‍👦🍩", "Die Simpsons", ["simpsons", "the simpsons"], "Serie"),
    ("🪖⭐⚔️🌌", "Star Wars", [], "Film"),
    ("🪐🏜️🐛", "Dune", ["der wüstenplanet"], "Film"),
]

KATEGORIEN = {"gemischt": None, "filme": "Film", "spiele": "Spiel", "serien": "Serie"}
EMOJI_SYSTEM = """Du erfindest ein Emoji-Rätsel für einen deutschen Discord-Server.
Antworte NUR mit JSON: {"emojis": "<3 bis 6 Emojis>", "loesung": "<Titel>", "alternativen": ["<andere gängige Schreibweise>"],
"kategorie": "<Film|Spiel|Serie>"}
Regeln: sehr bekannter Titel, den viele Jugendliche/junge Erwachsene kennen. Nur Emojis, keine Buchstaben, Zahlen oder Flaggen,
die den Titel direkt verraten. Deutscher Titel als Lösung, englischen Originaltitel als Alternative."""

GUESS_TIME = 90
HINT_AFTER = 45


def norm(text: str) -> str:
    text = text.lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\b(der|die|das|the)\b", " ", text)
    return " ".join(text.split())


def is_right(guess: str, answers: list[str]) -> bool:
    g = norm(guess)
    if not g:
        return False
    for a in answers:
        a = norm(a)
        if not a:
            continue
        if g == a or difflib.SequenceMatcher(None, g, a).ratio() >= 0.85:
            return True
        if len(a) >= 5 and a in g and len(g) <= len(a) + 15:  # „ist das batman?“
            return True
    return False


def parse_riddle(raw: str | None) -> tuple[str, str, list[str], str] | None:
    if not raw:
        return None
    raw = clean(raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    emojis = str(data.get("emojis", "")).strip()
    answer = str(data.get("loesung", "")).strip()
    if not emojis or not (2 <= len(answer) <= 40) or re.search(r"[A-Za-z0-9]", emojis):
        return None
    alts = [str(a).strip() for a in data.get("alternativen") or [] if str(a).strip()][:4]
    cat = str(data.get("kategorie", "")).strip().capitalize() or "Rätsel"
    return emojis, answer, alts, cat


class EmojiRound:
    def __init__(self, emojis: str, answer: str, alts: list[str], category: str):
        self.emojis, self.answer, self.alts, self.category = emojis, answer, alts, category
        self.started = time.time()
        self.done = False
        self.task: asyncio.Task | None = None


class EmojiRiddles:
    def __init__(self, data, brain):
        self.data, self.brain = data, brain
        self.rounds: dict[int, EmojiRound] = {}

    def recent(self, guild_id: int) -> list[str]:
        return self.data.get(guild_id, "emoji_recent", [])

    def remember(self, guild_id: int, answer: str) -> None:
        self.data.set(guild_id, "emoji_recent", (self.recent(guild_id) + [answer])[-40:])

    async def new_riddle(self, guild_id: int, category: str | None) -> EmojiRound:
        recent = {norm(a) for a in self.recent(guild_id)}
        if self.brain is not None and random.random() < 0.5:  # mal selbst ausgedacht, mal aus der Liste
            wish = f"Kategorie: {category}" if category else "Kategorie: frei (Film, Spiel oder Serie)"
            try:
                raw = await self.brain.complete(EMOJI_SYSTEM, f"{wish}\nNicht verwenden: {', '.join(self.recent(guild_id)[-15:])}",
                                                max_tokens=200)
            except Exception:  # noqa: BLE001
                raw = None
            parsed = parse_riddle(raw)
            if parsed and norm(parsed[1]) not in recent and (not category or parsed[3] == category):
                return EmojiRound(*parsed)
        pool = [b for b in BUILTIN if (not category or b[3] == category) and norm(b[1]) not in recent]
        if not pool:
            pool = [b for b in BUILTIN if not category or b[3] == category]
        return EmojiRound(*random.choice(pool))

    def embed(self, rnd: EmojiRound, hint: str = "") -> discord.Embed:
        e = discord.Embed(title=f"🧩 Emoji-Rätsel · {rnd.category}", description=f"# {rnd.emojis}", color=0xF1C40F)
        e.set_footer(text=f"Einfach die Lösung in den Chat schreiben – {GUESS_TIME} Sekunden, erster gewinnt!"
                          + (f" · Tipp: {hint}" if hint else ""))
        return e

    async def run_timer(self, channel: discord.abc.Messageable, guild_id: int, rnd: EmojiRound) -> None:
        await asyncio.sleep(HINT_AFTER)
        if rnd.done:
            return
        words = rnd.answer.split()
        hint = " ".join(w[0] + "·" * (len(w) - 1) for w in words)
        try:
            await channel.send(f"💡 Tipp: **{hint}** ({len(words)} Wort{'e' if len(words) > 1 else ''})")
        except discord.HTTPException:
            pass
        await asyncio.sleep(GUESS_TIME - HINT_AFTER)
        if rnd.done:
            return
        rnd.done = True
        self.rounds.pop(getattr(channel, "id", 0), None)
        try:
            await channel.send(f"⌛ Zeit um! Gesucht war **{rnd.answer}**. Nächste Runde mit `/emoji_raetsel`.")
        except discord.HTTPException:
            pass

    async def handle(self, message: discord.Message) -> bool:
        """Rateversuch? True = Nachricht gehört zum Spiel (Dave antwortet dann nicht extra)."""
        rnd = self.rounds.get(message.channel.id)
        if rnd is None or rnd.done or message.author.bot:
            return False
        if not is_right(message.content or "", [rnd.answer, *rnd.alts]):
            return True  # falsch geraten – Dave quatscht nicht dazwischen
        rnd.done = True
        self.rounds.pop(message.channel.id, None)
        if rnd.task:
            rnd.task.cancel()
        secs = int(time.time() - rnd.started)
        self.data.add_game_result(message.guild.id, "emoji", message.author.id, message.author.display_name, True)
        await message.reply(f"✅ Richtig! **{rnd.answer}** – in {secs} Sekunden. Punkt für {message.author.display_name}!",
                            mention_author=False)
        return True


def top_embed(title: str, rows: list[tuple[str, int, int]], unit: str) -> discord.Embed:
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'{i + 1}.'} **{name}** – {wins} {unit}" + (f" ({played} Spiele)" if played != wins else "")
             for i, (name, wins, played) in enumerate(rows)]
    return discord.Embed(title=title, description="\n".join(lines) or "Noch niemand. Sei der Erste!", color=0xF1C40F)


def register_minigames(tree: app_commands.CommandTree, data, riddles: EmojiRiddles) -> None:
    @tree.command(name="minesweeper", description="Minesweeper mit Spoilern: Felder aufdecken und auf keine 💣 klicken")
    @app_commands.describe(schwierigkeit="leicht (6×6), mittel (8×8) oder schwer (9×9)")
    @app_commands.choices(schwierigkeit=[app_commands.Choice(name=f"{k} ({r}×{c}, {m} Minen)", value=k)
                                         for k, (r, c, m) in LEVELS.items()])
    async def minesweeper(interaction: discord.Interaction, schwierigkeit: app_commands.Choice[str] | None = None):
        level = schwierigkeit.value if schwierigkeit else "mittel"
        rows, cols, mines = LEVELS[level]
        field = render_field(make_field(rows, cols, mines))
        await interaction.response.send_message(
            f"💣 **Minesweeper** ({level}, {mines} Minen) – klick auf die Felder zum Aufdecken. "
            f"Die Zahl sagt, wie viele Minen drumherum liegen. Das offene Feld ist sicher.\n{field}")

    @tree.command(name="emoji_raetsel", description="Dave postet Emojis – wer den Film/das Spiel zuerst errät, gewinnt")
    @app_commands.describe(kategorie="Worum soll's gehen?")
    @app_commands.choices(kategorie=[app_commands.Choice(name=k, value=k) for k in KATEGORIEN])
    @app_commands.guild_only()
    async def emoji_raetsel(interaction: discord.Interaction, kategorie: app_commands.Choice[str] | None = None):
        if interaction.channel_id in riddles.rounds:
            await interaction.response.send_message("Hier läuft schon ein Emoji-Rätsel – rate mit!", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        rnd = await riddles.new_riddle(interaction.guild_id, KATEGORIEN.get(kategorie.value if kategorie else "gemischt"))
        riddles.rounds[interaction.channel_id] = rnd
        riddles.remember(interaction.guild_id, rnd.answer)
        await interaction.followup.send(embed=riddles.embed(rnd))
        rnd.task = asyncio.create_task(riddles.run_timer(interaction.channel, interaction.guild_id, rnd))

    @tree.command(name="rangliste", description="Ranglisten der Mini-Spiele")
    @app_commands.describe(spiel="Welches Spiel?")
    @app_commands.choices(spiel=[app_commands.Choice(name="Sabacc", value="sabacc"),
                                 app_commands.Choice(name="Emoji-Rätsel", value="emoji")])
    @app_commands.guild_only()
    async def rangliste(interaction: discord.Interaction, spiel: app_commands.Choice[str]):
        rows = data.game_top(interaction.guild_id, spiel.value)
        title = "🃏 Sabacc-Rangliste" if spiel.value == "sabacc" else "🧩 Emoji-Rätsel-Rangliste"
        await interaction.response.send_message(embed=top_embed(title, rows, "Siege" if spiel.value == "sabacc"
                                                                else "Punkte"))
