import json
import random
from types import SimpleNamespace as N

from dave import minigames as mg
from dave import sabacc as sb
from dave.serverdata import ServerData

def c(v, name=None, suit="Münzen"):
    if name:
        return sb.Card(name, v)
    return sb.Card(f"{v}", v, suit)


def test_deck():
    deck = sb.make_deck(random.Random(1))
    assert len(deck) == 76
    assert sum(1 for x in deck if x.suit is None) == 16
    assert sum(x.value for x in deck if x.name == "Der Stern") == -34


def test_scoring():
    idiot = [c(0, "Idiot"), c(2), c(3, suit="Säbel")]
    pure = [c(15), c(8)]
    pure_neg = [c(-17, "Der Stern"), c(-8, "Ausdauer"), c(2)]
    close = [c(11), c(10)]
    bomb = [c(15), c(10)]
    assert sb.idiots_array(idiot) and not sb.idiots_array([c(0), c(2), c(3)])  # 0 muss der Idiot sein
    assert sb.score(idiot) < sb.score(pure) < sb.score(pure_neg) < sb.score(close) < sb.score(bomb)
    assert "Bomb-out" in sb.describe(bomb) and "Pure Sabacc" in sb.describe(pure)
    assert sb.total(pure_neg) == -23


def test_full_games_run():
    player = N(id=7, display_name="Anna")
    for seed in range(40):
        g = sb.ClassicGame(player, rng=random.Random(seed))
        g.player_protect(g.hands["player"][0].uid)
        while not g.finished:
            if g.can_draw() and abs(sb.total(g.hands["player"])) < 16:
                g.player_draw()
            elif g.can_call():
                g.player_call()
            else:
                g.player_stand()
            assert len(g.hands["player"]) <= sb.MAX_CARDS and len(g.hands["dave"]) <= sb.MAX_CARDS
        result, text = g.showdown()
        assert result in (-1, 0, 1) and "Dave" in text
    assert g.embed() is not None


def test_shift_keeps_protected_card():
    g = sb.ClassicGame(N(id=1, display_name="X"), rng=random.Random(0))
    keep = g.hands["player"][0]
    g.field["player"] = keep.uid
    other = g.hands["player"][1]

    class Pasch(random.Random):
        def randint(self, a, b):
            return 4
    g.rng = Pasch()
    msg = g.maybe_shift()
    assert "Shift" in msg and g.hands["player"][0] is keep and g.hands["player"][1] is not other


def test_trade_and_call_rules():
    g = sb.ClassicGame(N(id=1, display_name="X"), rng=random.Random(5))
    assert not g.can_call()
    old = g.hands["player"][0]
    g.player_trade(old.uid)
    assert old not in g.hands["player"] and len(g.hands["player"]) == 2
    if not g.finished:
        assert g.can_call()


def test_view_builds():
    g = sb.ClassicGame(N(id=1, display_name="X"), rng=random.Random(2))
    v = sb.SabaccView(g)
    labels = [getattr(x, "label", None) for x in v.children]
    assert labels == ["Karte ziehen", "Karte tauschen", "Stehen bleiben", "Interferenzfeld", "Callen"]
    assert v.children[4].disabled  # callen erst ab Runde 2
    v.mode = "trade"
    v.build()
    assert len(v.children) == 6


def test_minesweeper_field():
    field = mg.make_field(8, 8, 10, random.Random(3))
    flat = [v for row in field for v in row]
    assert flat.count(-1) == 10 and max(flat) <= 8
    text = mg.render_field(field, random.Random(3))
    assert text.count("||") == 2 * (64 - 1)  # ein Feld offen
    assert len(text) < 1500


def test_emoji_guessing():
    assert mg.is_right("batman", ["Batman"])
    assert mg.is_right("ist das Batmann?", ["Batman"])
    assert mg.is_right("koenig der loewen", ["Der König der Löwen"])
    assert mg.is_right("the lion king", ["Der König der Löwen", "the lion king"])
    assert not mg.is_right("superman", ["Batman"])
    assert not mg.is_right("", ["Batman"])


def test_parse_riddle():
    raw = json.dumps({"emojis": "🦈🌊🏖️", "loesung": "Der weiße Hai", "alternativen": ["Jaws"], "kategorie": "film"})
    assert mg.parse_riddle(raw) == ("🦈🌊🏖️", "Der weiße Hai", ["Jaws"], "Film")
    assert mg.parse_riddle('{"emojis": "Hai 🦈", "loesung": "Jaws"}') is None
    assert mg.parse_riddle("kaputt") is None


async def test_emoji_round_flow(tmp_path):
    data = ServerData(tmp_path)
    riddles = mg.EmojiRiddles(data, brain=None)
    rnd = await riddles.new_riddle(1, "Spiel")
    assert rnd.category == "Spiel"
    riddles.rounds[5] = rnd
    replies = []

    async def reply(text, **kw):
        replies.append(text)

    def msg(text):
        return N(channel=N(id=5), author=N(bot=False, id=9, display_name="Ben"), content=text,
                 guild=N(id=1), reply=reply)

    assert await riddles.handle(msg("keine Ahnung"))  # falsch, aber vom Spiel geschluckt
    assert replies == []
    assert await riddles.handle(msg(rnd.answer))
    assert replies and "Richtig" in replies[0]
    assert 5 not in riddles.rounds
    assert data.game_top(1, "emoji") == [("Ben", 1, 1)]
    assert not await riddles.handle(msg("noch was"))  # Runde vorbei -> normale Nachricht


def test_button_emojis_are_discord_safe():
    """Discord lehnt manche Unicode-Zeichen als Knopf-Emoji ab (z.B. 🂠 Kartenrücken) – dann startet das Spiel nicht."""
    g = sb.ClassicGame(N(id=1, display_name="X"), rng=random.Random(2))
    allowed = {"🃏", "🔁", "✋", "🛡️", "📣"}
    for item in sb.SabaccView(g).children:
        if getattr(item, "emoji", None):
            assert str(item.emoji) in allowed
