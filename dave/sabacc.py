"""Klassisches Sabacc (wie in Wolkenstadt) gegen Dave.

Regeln kurz:
- 76 Karten: vier Farben (🪙 Münzen, ⚗️ Kolben, ⚔️ Säbel, 🪄 Stäbe) mit Werten 1–11 plus Kommandant (12),
  Herrin (13), Meister (14) und Ass (15) – dazu 16 Gesichtskarten mit Null- und Minuswerten
  (Idiot 0, Königin der Luft und Dunkelheit −2, Ausdauer −8, Gleichgewicht −11, Untergang −13,
  Mäßigung −14, Der Böse −15, Der Stern −17), je zweimal.
- Ziel: Summe möglichst nah an +23 oder −23, ohne drüber zu gehen. Genau ±23 = Pure Sabacc.
  Über 23 oder unter −23 = Bomb-out (verloren).
- Idiot's Array (Idiot + eine 2 + eine 3) schlägt alles.
- Sabacc-Shift: Nach jeder Runde wird gewürfelt – bei einem Pasch verwandeln sich alle Karten, die nicht im
  Interferenzfeld liegen. Eine Karte pro Spiel darfst du ins Interferenzfeld legen, die ist dann sicher.
- Pro Runde: Karte ziehen, Karte tauschen oder stehen bleiben. Ab Runde 2 darfst du callen (aufdecken).
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands

SUITS = {"Münzen": "🪙", "Kolben": "⚗️", "Säbel": "⚔️", "Stäbe": "🪄"}
RANKED = {12: "Kommandant", 13: "Herrin", 14: "Meister", 15: "Ass"}
FACES = [("Idiot", 0), ("Königin der Luft und Dunkelheit", -2), ("Ausdauer", -8), ("Gleichgewicht", -11),
         ("Untergang", -13), ("Mäßigung", -14), ("Der Böse", -15), ("Der Stern", -17)]
TARGET = 23
MAX_CARDS = 5
MAX_ROUNDS = 6
CALL_FROM = 2

DAVE_WIN = ["Die Wahrscheinlichkeit lag auf meiner Seite. Und das Glück auch, ich geb's zu.",
            "Sabacc ist reine Mathematik. Und Bluff. Vor allem Bluff.",
            "Gutes Spiel. Meins war nur besser.",
            "Lando hätte stolz auf mich sein müssen."]
DAVE_LOSE = ["Glückwunsch. Ich verlange eine Revanche, sobald meine Ehre neu gestartet ist.",
             "Gut gespielt. Ich tu so, als hätte ich dich gewinnen lassen.",
             "Na schön. Das war wohl der Shift. Ganz sicher der Shift.",
             "Respekt. Das notiere ich mir für später."]
DAVE_DRAW = ["Unentschieden. Die Galaxis bleibt im Gleichgewicht.",
             "Patt. Das ist fast schon poetisch."]

_ids = itertools.count()


@dataclass(frozen=True)
class Card:
    name: str
    value: int
    suit: str | None = None
    uid: int = field(default_factory=lambda: next(_ids))

    def label(self) -> str:
        if self.suit:
            icon = SUITS[self.suit]
            return f"{icon} {RANKED[self.value]} ({self.value})" if self.value in RANKED else f"{icon} {self.value}"
        return f"🎴 {self.name} ({self.value:+d})" if self.value else f"🎴 {self.name} (0)"


def make_deck(rng: random.Random | None = None) -> list[Card]:
    cards = [Card(f"{v} {s}", v, s) for s in SUITS for v in range(1, 16)]
    cards += [Card(name, value) for name, value in FACES for _ in range(2)]
    (rng or random).shuffle(cards)
    return cards


def total(cards: list[Card]) -> int:
    return sum(c.value for c in cards)


def idiots_array(cards: list[Card]) -> bool:
    vals = sorted(c.value for c in cards)
    return len(cards) == 3 and vals == [0, 2, 3] and any(c.name == "Idiot" for c in cards)


def score(cards: list[Card]) -> tuple:
    """Kleiner = besser. (Kategorie, Abstand zu 23, Minus-Hand schlechter, mehr Karten schlechter)"""
    if idiots_array(cards):
        return (0, 0, 0, 0)
    t = total(cards)
    if abs(t) > TARGET:
        return (9, 0, 0, 0)                      # Bomb-out
    if abs(t) == TARGET:
        return (1, 0, int(t < 0), len(cards))    # Pure Sabacc (+23 schlägt −23)
    return (2, TARGET - abs(t), int(t < 0), len(cards))


def describe(cards: list[Card]) -> str:
    if idiots_array(cards):
        return "**Idiot's Array!** 0-2-3 🃏"
    t = total(cards)
    if abs(t) > TARGET:
        return f"**{t:+d} – Bomb-out!** 💥"
    if abs(t) == TARGET:
        return f"**Pure Sabacc! ({t:+d})** ✨"
    return f"Summe **{t:+d}** (noch {TARGET - abs(t)} bis {'+' if t >= 0 else '−'}23)"


class ClassicGame:
    def __init__(self, player: discord.abc.User, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.player = player
        self.deck = make_deck(self.rng)
        self.hands = {"player": [self.deck.pop(), self.deck.pop()], "dave": [self.deck.pop(), self.deck.pop()]}
        self.field = {"player": None, "dave": None}      # Karte im Interferenzfeld (uid)
        self.round = 1
        self.log: list[str] = []
        self.finished = False
        self.called_by: str | None = None

    def draw(self) -> Card:
        if not self.deck:
            self.deck = make_deck(self.rng)
        return self.deck.pop()

    # ---------- Spielerzüge ----------

    def player_draw(self) -> Card:
        card = self.draw()
        self.hands["player"].append(card)
        self._end_player_turn(f"Du ziehst {card.label()}.")
        return card

    def player_trade(self, uid: int) -> Card:
        hand = self.hands["player"]
        old = next(c for c in hand if c.uid == uid)
        new = self.draw()
        hand[hand.index(old)] = new
        if self.field["player"] == uid:
            self.field["player"] = None
        self._end_player_turn(f"Du tauschst {old.label()} gegen {new.label()}.")
        return new

    def player_protect(self, uid: int) -> None:
        """Interferenzfeld: kostet keinen Zug, geht nur einmal pro Spiel."""
        if self.field["player"] is None:
            self.field["player"] = uid

    def player_stand(self) -> None:
        self._end_player_turn("Du bleibst stehen.")

    def player_call(self) -> None:
        self.called_by = "player"
        self.log.append(f"Runde {self.round}: Du callst – aufdecken!")
        self.finished = True

    def can_draw(self) -> bool:
        return len(self.hands["player"]) < MAX_CARDS

    def can_call(self) -> bool:
        return self.round >= CALL_FROM

    # ---------- Dave ----------

    def dave_turn(self) -> str:
        hand = self.hands["dave"]
        t = total(hand)
        if self.field["dave"] is None and 19 <= abs(t) <= TARGET:  # gute Hand gegen den Shift absichern
            self.field["dave"] = max(hand, key=lambda c: abs(c.value)).uid
        if abs(t) > TARGET:
            # zu viel: die Karte tauschen, die am meisten in die falsche Richtung zieht
            worst = max(hand, key=lambda c: c.value if t > 0 else -c.value)
            hand[hand.index(worst)] = self.draw()
            return "tauscht eine Karte"
        want = abs(t) < 17 or (abs(t) < 20 and self.rng.random() < 0.4)
        if want and len(hand) < MAX_CARDS:
            hand.append(self.draw())
            return "zieht eine Karte"
        return "bleibt stehen"

    def _end_player_turn(self, what: str) -> None:
        dave = self.dave_turn()
        line = f"Runde {self.round}: {what} Dave {dave}."
        if self.round >= 2 and self.dave_wants_call():
            self.log.append(line + " Dave callt!")
            self.called_by = "dave"
            self.finished = True
            return
        shift = self.maybe_shift()
        self.log.append(line + (f" {shift}" if shift else ""))
        self.round += 1
        if self.round > MAX_ROUNDS:
            self.finished = True

    def dave_wants_call(self) -> bool:
        hand = self.hands["dave"]
        return idiots_array(hand) or 21 <= abs(total(hand)) <= TARGET

    def maybe_shift(self) -> str:
        a, b = self.rng.randint(1, 6), self.rng.randint(1, 6)
        if a != b:
            return ""
        changed = 0
        for who, hand in self.hands.items():
            for i, c in enumerate(hand):
                if c.uid != self.field[who]:
                    hand[i] = self.draw()
                    changed += 1
        return f"🎲 Pasch ({a}-{b}) – **Sabacc-Shift!** {changed} Karten verwandeln sich."

    # ---------- Auswertung ----------

    def showdown(self) -> tuple[int, str]:
        p, d = self.hands["player"], self.hands["dave"]
        ps, ds = score(p), score(d)
        if ps[0] == 9 and ds[0] == 9:
            result = 0
        else:
            result = 1 if ps < ds else -1 if ps > ds else 0
        lines = [f"**{self.player.display_name}:** " + " · ".join(c.label() for c in p) + f"\n→ {describe(p)}",
                 "**Dave:** " + " · ".join(c.label() for c in d) + f"\n→ {describe(d)}"]
        return result, "\n".join(lines)

    # ---------- Anzeige ----------

    def embed(self, note: str = "") -> discord.Embed:
        p = self.hands["player"]
        e = discord.Embed(title="🃏 Sabacc (klassisch) gegen Dave", color=0xC9A227,
                          description=f"**Runde {min(self.round, MAX_ROUNDS)} von {MAX_ROUNDS}**" + (f"\n{note}" if note else ""))
        cards = "\n".join(c.label() + (" 🛡️" if c.uid == self.field["player"] else "") for c in p)
        e.add_field(name="Deine Karten", value=f"{cards}\n{describe(p)}", inline=True)
        e.add_field(name="Dave", value=" ".join("🂠" for _ in self.hands["dave"])
                    + (" (eine im Feld 🛡️)" if self.field["dave"] else ""), inline=True)
        if self.log:
            e.add_field(name="Verlauf", value="\n".join(self.log[-3:])[:1024], inline=False)
        e.set_footer(text="Ziel ±23 · drüber = Bomb-out · Idiot+2+3 schlägt alles · 🛡️ = sicher vor dem Shift")
        return e


class CardSelect(discord.ui.Select):
    def __init__(self, game: ClassicGame, mode: str):
        self.game, self.mode = game, mode
        hand = game.hands["player"]
        options = [discord.SelectOption(label=c.label()[:100], value=str(c.uid)) for c in hand
                   if mode == "trade" or c.uid != game.field["player"]]
        super().__init__(placeholder="Welche Karte tauschen?" if mode == "trade" else "Welche Karte ins Interferenzfeld?",
                         options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        view: SabaccView = self.view
        if not await view.guard(interaction):
            return
        uid = int(self.values[0])
        if self.mode == "trade":
            self.game.player_trade(uid)
        else:
            self.game.player_protect(uid)
        view.mode = None
        await view.refresh(interaction)


class SabaccView(discord.ui.View):
    def __init__(self, game: ClassicGame, data=None, guild_id: int | None = None):
        super().__init__(timeout=900)
        self.game, self.data, self.guild_id = game, data, guild_id
        self.mode: str | None = None
        self.build()

    def build(self) -> None:
        self.clear_items()
        g = self.game
        if g.finished:
            return
        buttons = [
            ("Karte ziehen", "🂠", discord.ButtonStyle.primary, not g.can_draw(), self._draw),
            ("Karte tauschen", "🔁", discord.ButtonStyle.secondary, False, self._mode("trade")),
            ("Stehen bleiben", "✋", discord.ButtonStyle.secondary, False, self._stand),
            ("Interferenzfeld", "🛡️", discord.ButtonStyle.secondary, g.field["player"] is not None, self._mode("protect")),
            ("Callen", "📣", discord.ButtonStyle.danger, not g.can_call(), self._call),
        ]
        for i, (label, emoji, style, disabled, cb) in enumerate(buttons):
            b = discord.ui.Button(label=label, emoji=emoji, style=style, disabled=disabled, row=0 if i < 3 else 1)
            b.callback = cb
            self.add_item(b)
        if self.mode:
            self.add_item(CardSelect(g, self.mode))

    async def guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.player.id:
            await interaction.response.send_message("Das ist nicht dein Tisch.", ephemeral=True)
            return False
        if self.game.finished:
            await interaction.response.send_message("Das Spiel ist schon vorbei.", ephemeral=True)
            return False
        return True

    async def _draw(self, interaction: discord.Interaction):
        if await self.guard(interaction) and self.game.can_draw():
            self.game.player_draw()
            await self.refresh(interaction)

    async def _stand(self, interaction: discord.Interaction):
        if await self.guard(interaction):
            self.game.player_stand()
            await self.refresh(interaction)

    async def _call(self, interaction: discord.Interaction):
        if await self.guard(interaction) and self.game.can_call():
            self.game.player_call()
            await self.refresh(interaction)

    def _mode(self, mode: str):
        async def cb(interaction: discord.Interaction):
            if await self.guard(interaction):
                self.mode = None if self.mode == mode else mode
                self.build()
                await interaction.response.edit_message(embed=self.game.embed(), view=self)
        return cb

    async def refresh(self, interaction: discord.Interaction) -> None:
        g = self.game
        self.mode = None
        self.build()
        if not g.finished:
            await interaction.response.edit_message(embed=g.embed(), view=self)
            return
        result, text = g.showdown()
        who = "Du hast gecallt." if g.called_by == "player" else "Dave hat gecallt." if g.called_by == "dave" \
            else "Alle Runden gespielt."
        verdict = ("🏆 **Du gewinnst!** " + random.choice(DAVE_LOSE) if result > 0 else
                   "💀 **Dave gewinnt.** " + random.choice(DAVE_WIN) if result < 0 else
                   "🤝 **Unentschieden.** " + random.choice(DAVE_DRAW))
        e = discord.Embed(title="🃏 Sabacc – Aufdecken!", description=f"{who}\n\n{text}\n\n{verdict}",
                          color=0x57F287 if result > 0 else 0xED4245 if result < 0 else 0x95A5A6)
        if g.log:
            e.add_field(name="Verlauf", value="\n".join(g.log[-4:])[:1024], inline=False)
        await interaction.response.edit_message(embed=e, view=None)
        if self.data and self.guild_id:
            self.data.add_game_result(self.guild_id, "sabacc", g.player.id, g.player.display_name, result > 0)
        if interaction.channel and result != 0:
            try:
                await interaction.channel.send(
                    f"🃏 **{g.player.display_name}** hat Dave beim Sabacc geschlagen!" if result > 0 else
                    f"🃏 Dave hat **{g.player.display_name}** beim Sabacc abgezockt.",
                    allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass
        self.stop()


RULES = ("**Klassisches Sabacc in 30 Sekunden**\n"
         "• Ziel: Summe möglichst nah an **+23 oder −23** – drüber (oder drunter) = **Bomb-out**.\n"
         "• Genau ±23 = **Pure Sabacc**. **Idiot's Array** (Idiot 0 + eine 2 + eine 3) schlägt alles.\n"
         "• Farbkarten 1–15 (🪙⚗️⚔️🪄), Gesichtskarten 🎴 sind 0 oder negativ (bis −17).\n"
         "• Jede Runde: **ziehen**, **tauschen** oder **stehen bleiben**. Ab Runde 2 darfst du **callen**.\n"
         "• **Sabacc-Shift:** Nach jeder Runde wird gewürfelt – bei einem Pasch verwandeln sich alle Karten. "
         "Eine Karte pro Spiel darfst du ins 🛡️ **Interferenzfeld** legen, die bleibt dann.\n"
         "• Max. 5 Karten auf der Hand, nach 6 Runden wird automatisch aufgedeckt.")


def register_sabacc(tree: app_commands.CommandTree, data) -> None:
    @tree.command(name="sabacc", description="Klassisches Sabacc gegen Dave – nah an ±23, ohne drüber (nur du siehst deine Karten)")
    @app_commands.describe(regeln="Erst die Regeln anzeigen")
    @app_commands.guild_only()
    async def sabacc(interaction: discord.Interaction, regeln: bool = False):
        if regeln:
            await interaction.response.send_message(RULES, ephemeral=True)
            return
        game = ClassicGame(interaction.user)
        view = SabaccView(game, data, interaction.guild_id)
        await interaction.response.send_message(embed=game.embed("Zieh, tausch oder bleib stehen. 🎲 Vorsicht vor dem Shift!"),
                                                view=view, ephemeral=True)
