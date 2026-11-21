"""Mini-Spiele: Galgenmännchen, Tic-Tac-Toe (gegen Dave oder jemand anderen), Schere-Stein-Papier."""

from __future__ import annotations

import random
import time

import discord
from discord import app_commands

# ---------------------------------------------------------------------
# Galgenmännchen
# ---------------------------------------------------------------------

HANGMAN_WORDS = [
    "RAUMSCHIFF", "LICHTSCHWERT", "GALAXIE", "ASTRONAUT", "SUPERNOVA", "KOMET", "SATELLIT", "RAKETE",
    "TELESKOP", "MILCHSTRASSE", "PLANET", "ASTEROID", "MONDLANDUNG", "SCHWARZES LOCH", "NEBEL",
    "KONSOLE", "CONTROLLER", "TASTATUR", "BILDSCHIRM", "GRAFIKKARTE", "PROZESSOR", "HEADSET", "JOYSTICK",
    "SPEICHERSTAND", "ENDGEGNER", "BOSSKAMPF", "LEVELAUFSTIEG", "MULTIPLAYER", "SPEEDRUN", "KOOPERATIV",
    "DRACHE", "RITTER", "SCHATZKARTE", "PIRATENSCHIFF", "ZAUBERER", "ROBOTER", "ZEITREISE", "DINOSAURIER",
    "VULKAN", "GEWITTER", "REGENBOGEN", "POLARLICHT", "WASSERFALL", "DSCHUNGEL", "WUESTE", "GLETSCHER",
    "PIZZA", "DOENER", "SCHOKOLADE", "POPCORN", "GUMMIBAERCHEN", "SPAGHETTI", "PFANNKUCHEN",
    "KAENGURU", "PINGUIN", "ELEFANT", "KROKODIL", "SCHILDKROETE", "EICHHOERNCHEN", "TINTENFISCH",
    "WISSENSCHAFT", "EXPERIMENT", "MAGNET", "ELEKTRIZITAET", "SCHWERKRAFT", "QUANTEN", "LABOR",
]
GALLOWS = [
    "```\n  +---+\n      |\n      |\n      |\n     ===```",
    "```\n  +---+\n  O   |\n      |\n      |\n     ===```",
    "```\n  +---+\n  O   |\n  |   |\n      |\n     ===```",
    "```\n  +---+\n  O   |\n /|   |\n      |\n     ===```",
    "```\n  +---+\n  O   |\n /|\\  |\n      |\n     ===```",
    "```\n  +---+\n  O   |\n /|\\  |\n /    |\n     ===```",
    "```\n  +---+\n  O   |\n /|\\  |\n / \\  |\n     ===```",
]
MAX_WRONG = len(GALLOWS) - 1
HANGMAN_TIMEOUT = 15 * 60


class Hangman:
    def __init__(self, word: str):
        self.word = word.upper()
        self.guessed: set[str] = set()
        self.wrong: list[str] = []
        self.started = self.last = time.time()
        self.message: discord.Message | None = None
        self.helpers: dict[int, str] = {}

    @property
    def masked(self) -> str:
        return " ".join(c if (c in self.guessed or not c.isalpha()) else "＿" for c in self.word)

    @property
    def won(self) -> bool:
        return all(c in self.guessed or not c.isalpha() for c in self.word)

    @property
    def lost(self) -> bool:
        return len(self.wrong) >= MAX_WRONG

    def guess(self, text: str) -> str:
        """Gibt 'hit', 'miss', 'dup', 'solved', 'wrongword' zurück."""
        self.last = time.time()
        text = text.strip().upper()
        if len(text) == 1:
            if text in self.guessed or text in self.wrong:
                return "dup"
            if text in self.word:
                self.guessed.add(text)
                return "solved" if self.won else "hit"
            self.wrong.append(text)
            return "miss"
        if text == self.word:
            self.guessed.update(c for c in self.word if c.isalpha())
            return "solved"
        self.wrong.append("✗")
        return "wrongword"

    def embed(self, status: str = "") -> discord.Embed:
        color = 0x57F287 if self.won else 0xED4245 if self.lost else 0x5865F2
        e = discord.Embed(title="🪢 Galgenmännchen", color=color)
        e.description = f"{GALLOWS[min(len(self.wrong), MAX_WRONG)]}\n**{self.masked}**"
        e.add_field(name="Falsch", value=" ".join(self.wrong) or "–", inline=True)
        e.add_field(name="Leben", value="❤️" * (MAX_WRONG - len(self.wrong)) or "💀", inline=True)
        e.set_footer(text=status or "Schreib einen Buchstaben oder das ganze Wort in den Chat!")
        return e


class Hangmen:
    """Läuft pro Kanal höchstens ein Galgenmännchen."""

    def __init__(self):
        self.games: dict[int, Hangman] = {}

    async def handle(self, message: discord.Message) -> bool:
        game = self.games.get(message.channel.id)
        if game is None:
            return False
        if time.time() - game.last > HANGMAN_TIMEOUT:
            self.games.pop(message.channel.id, None)
            return False
        text = message.content.strip()
        if not text or len(text) > 30 or not all(c.isalpha() or c == " " for c in text):
            return False  # normales Gequatsche ignorieren
        if len(text) > 1 and len(text) != len(game.word):
            return False
        result = game.guess(text)
        name = message.author.display_name
        status = {"hit": f"✅ {name}: „{text.upper()}“ ist drin!", "miss": f"❌ {name}: „{text.upper()}“ ist nicht drin.",
                  "dup": f"🔁 „{text.upper()}“ hatten wir schon.", "wrongword": f"❌ {name}: „{text.upper()}“ ist es nicht."
                  }.get(result, "")
        if result in ("hit", "solved"):
            game.helpers[message.author.id] = name
        try:
            await message.add_reaction({"hit": "✅", "solved": "🎉", "dup": "🔁"}.get(result, "❌"))
        except discord.HTTPException:
            pass
        if game.won:
            self.games.pop(message.channel.id, None)
            status = f"🎉 Gelöst von {name}! Das Wort war **{game.word}**."
        elif game.lost:
            self.games.pop(message.channel.id, None)
            status = f"💀 Verloren! Das Wort war **{game.word}**."
        if game.message:
            try:
                await game.message.edit(embed=game.embed(status))
            except discord.HTTPException:
                pass
        if game.won or game.lost:
            await message.channel.send(status)
        return True


# ---------------------------------------------------------------------
# Tic-Tac-Toe
# ---------------------------------------------------------------------

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def winner(board: list[str]) -> str | None:
    for a, b, c in LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None


def best_move(board: list[str], me: str = "O", other: str = "X") -> int:
    """Minimax – perfektes Spiel."""
    def score(b: list[str], turn: str, depth: int) -> int:
        w = winner(b)
        if w == me:
            return 10 - depth
        if w == other:
            return depth - 10
        if w == "draw":
            return 0
        results = []
        for i in range(9):
            if not b[i]:
                b[i] = turn
                results.append(score(b, other if turn == me else me, depth + 1))
                b[i] = ""
        return max(results) if turn == me else min(results)

    moves = [i for i in range(9) if not board[i]]
    best, best_score = moves[0], -999
    for i in moves:
        board[i] = me
        s = score(board, other, 1)
        board[i] = ""
        if s > best_score:
            best, best_score = i, s
    return best


def dave_move(board: list[str], skill: float = 0.75) -> int:
    """Meist perfekt, manchmal ein Fehler – sonst wär's ja langweilig."""
    free = [i for i in range(9) if not board[i]]
    return best_move(board) if random.random() < skill else random.choice(free)


class TicTacToe(discord.ui.View):
    def __init__(self, x_player: discord.abc.User, o_player: discord.abc.User | None):
        super().__init__(timeout=300)
        self.board = [""] * 9
        self.x, self.o = x_player, o_player  # o None = Dave
        self.turn = "X"
        for i in range(9):
            b = discord.ui.Button(label="​", style=discord.ButtonStyle.secondary, row=i // 3)
            b.callback = self._click(i)
            self.add_item(b)

    def title(self) -> str:
        o_name = self.o.display_name if self.o else "Dave"
        w = winner(self.board)
        if w == "draw":
            return f"🤝 Unentschieden zwischen {self.x.display_name} und {o_name}!"
        if w:
            return f"🏆 {self.x.display_name if w == 'X' else o_name} gewinnt!"
        current = self.x.display_name if self.turn == "X" else o_name
        return f"❌ {self.x.display_name} gegen ⭕ {o_name} – {current} ist dran"

    def _refresh(self) -> None:
        done = winner(self.board) is not None
        for i, item in enumerate(self.children):
            mark = self.board[i]
            item.label = {"X": "❌", "O": "⭕"}.get(mark, "​")
            item.style = discord.ButtonStyle.danger if mark == "X" else \
                discord.ButtonStyle.primary if mark == "O" else discord.ButtonStyle.secondary
            item.disabled = bool(mark) or done

    def _click(self, i: int):
        async def callback(interaction: discord.Interaction):
            player = self.x if self.turn == "X" else self.o
            if player is None or interaction.user.id != player.id:
                await interaction.response.send_message("Du bist gerade nicht dran (oder spielst nicht mit).",
                                                        ephemeral=True)
                return
            if self.board[i] or winner(self.board):
                await interaction.response.defer()
                return
            self.board[i] = self.turn
            self.turn = "O" if self.turn == "X" else "X"
            if self.o is None and not winner(self.board):
                self.board[dave_move(self.board)] = "O"
                self.turn = "X"
            self._refresh()
            if winner(self.board):
                self.stop()
            await interaction.response.edit_message(content=self.title(), view=self)
        return callback


# ---------------------------------------------------------------------
# Schere-Stein-Papier
# ---------------------------------------------------------------------

RPS = {"stein": "🪨", "papier": "📄", "schere": "✂️"}
BEATS = {"stein": "schere", "papier": "stein", "schere": "papier"}
RPS_SPRUECHE = {"win": ["Glückwunsch, das war's für mich. 😤", "Okay, okay. Revanche?", "Pures Glück, ich sag's nur."],
                "lose": ["Haha, wieder mal ich. 😎", "Zu leicht.", "Ich hab deine Gedanken gelesen."],
                "draw": ["Gleichstand – Gedankenübertragung?", "Unentschieden. Nochmal?", "Great minds think alike."]}


def rps_result(player: str, dave: str) -> str:
    if player == dave:
        return "draw"
    return "win" if BEATS[player] == dave else "lose"


class RPSView(discord.ui.View):
    def __init__(self, player: discord.abc.User):
        super().__init__(timeout=60)
        self.player = player
        for key, emoji in RPS.items():
            b = discord.ui.Button(label=key.capitalize(), emoji=emoji, style=discord.ButtonStyle.secondary)
            b.callback = self._pick(key)
            self.add_item(b)

    def _pick(self, key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.player.id:
                await interaction.response.send_message("Starte dein eigenes Spiel mit /ssp. 😉", ephemeral=True)
                return
            dave = random.choice(list(RPS))
            result = rps_result(key, dave)
            for item in self.children:
                item.disabled = True
            text = (f"{self.player.display_name}: {RPS[key]}  vs.  Dave: {RPS[dave]}\n"
                    f"**{ {'win': 'Du gewinnst!', 'lose': 'Dave gewinnt!', 'draw': 'Unentschieden!'}[result] }** "
                    f"{random.choice(RPS_SPRUECHE[result])}")
            self.stop()
            await interaction.response.edit_message(content=text, view=self)
        return callback


def register_games(tree: app_commands.CommandTree, hangmen: Hangmen) -> None:
    @tree.command(name="galgen", description="Galgenmännchen – alle im Kanal raten mit")
    @app_commands.guild_only()
    async def galgen(interaction: discord.Interaction):
        if interaction.channel_id in hangmen.games:
            await interaction.response.send_message("Hier läuft schon ein Galgenmännchen!", ephemeral=True)
            return
        game = Hangman(random.choice(HANGMAN_WORDS))
        hangmen.games[interaction.channel_id] = game
        await interaction.response.send_message(embed=game.embed())
        game.message = await interaction.original_response()

    @tree.command(name="tictactoe", description="Tic-Tac-Toe gegen Dave oder jemand anderen")
    @app_commands.describe(gegner="Gegen wen? Leer = gegen Dave")
    @app_commands.guild_only()
    async def tictactoe(interaction: discord.Interaction, gegner: discord.Member | None = None):
        if gegner and (gegner.bot or gegner.id == interaction.user.id):
            gegner = None
        view = TicTacToe(interaction.user, gegner)
        await interaction.response.send_message(view.title(), view=view,
                                                allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="ssp", description="Schere, Stein, Papier gegen Dave")
    async def ssp(interaction: discord.Interaction):
        await interaction.response.send_message("Schere, Stein oder Papier? Wähl!", view=RPSView(interaction.user))
