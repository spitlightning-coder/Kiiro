import asyncio

from dave.games import HANGMAN_WORDS, MAX_WRONG, Hangman, TicTacToe, best_move, rps_result, winner
from dave.serverdata import ServerData
from dave.wortel import ANSWERS, GRAY, GREEN, YELLOW, normalize, score, word_of


def test_hangman():
    g = Hangman("Pizza")
    assert g.masked == "＿ ＿ ＿ ＿ ＿"
    assert g.guess("z") == "hit" and g.masked == "＿ ＿ Z Z ＿"
    assert g.guess("z") == "dup"
    assert g.guess("q") == "miss" and g.wrong == ["Q"]
    assert g.guess("pizza") == "solved" and g.won
    g2 = Hangman("ab")
    for c in "cdefgh"[:MAX_WRONG]:
        g2.guess(c)
    assert g2.lost
    assert Hangman("SCHWARZES LOCH").masked.count(" ") > 10  # Leerzeichen bleiben sichtbar
    assert all(w.replace(" ", "").isalpha() and w == w.upper() for w in HANGMAN_WORDS)


def test_tictactoe_logic():
    assert winner(["X", "X", "X", "", "", "", "", "", ""]) == "X"
    assert winner(["X", "O", "X", "X", "O", "O", "O", "X", "X"]) == "draw"
    assert best_move(["O", "O", "", "X", "X", "", "", "", ""]) == 2      # gewinnen
    assert best_move(["X", "X", "", "", "O", "", "", "", ""]) == 2       # blocken

    async def view():
        class U:
            id, display_name = 1, "Anna"
        v = TicTacToe(U(), None)
        assert len(v.children) == 9 and "Anna" in v.title()
        v.to_components()
    asyncio.run(view())


def test_rps():
    assert rps_result("stein", "schere") == "win"
    assert rps_result("stein", "papier") == "lose"
    assert rps_result("papier", "papier") == "draw"


def test_wortel():
    assert all(len(w) == 5 and w.isalpha() and w.isascii() for w in ANSWERS) and len(ANSWERS) > 150
    assert word_of("2026-11-21") == word_of("2026-11-21") and word_of("2026-11-21") in ANSWERS
    assert normalize(" löwe ") == "LOEWE"
    assert score("KATZE", "KATZE") == [GREEN] * 5
    assert score("ESSEN", "NESTE") == [YELLOW, GRAY, GREEN, YELLOW, YELLOW]


def test_wordle_storage_and_streak(tmp_path):
    d = ServerData(tmp_path)
    d.wordle_set(1, 7, "2026-11-19", ["ABEND"], 1)
    d.wordle_set(1, 7, "2026-11-20", ["ABEND", "ACKER"], 2)
    d.wordle_set(1, 7, "2026-11-21", ["X"], 0)  # läuft noch
    assert d.wordle_streak(1, 7, "2026-11-21") == 2
    assert d.wordle_streak(1, 7, "2026-11-25") == 0  # gerissen
    d.wordle_set(1, 8, "2026-11-20", ["A"] * 6, -1)
    stats = d.wordle_stats(1)
    assert stats[0][0] == 7 and stats[0][1] == 2 and stats[1][:3] == (8, 0, 1)
    assert d.wordle_get(1, 7, "2026-11-21") == (["X"], 0)


def test_quotes(tmp_path):
    d = ServerData(tmp_path)
    q1 = d.add_quote(1, 7, "Anna", "Ich bin nicht faul, ich bin im Energiesparmodus.", "https://d/1", 8, 100.0)
    assert d.add_quote(1, 7, "Anna", "doppelt", "https://d/1", 8, 100.0) is None  # gleiche Nachricht
    d.add_quote(1, 9, "Ben", "Kurz", "https://d/2", 9, 200.0)
    assert d.quote_count(1) == 2
    assert d.random_quote(1, 7)["text"].startswith("Ich bin nicht faul")
    assert d.delete_quote(1, q1, 99) is False          # Fremder darf nicht
    assert d.delete_quote(1, q1, 8) is True            # wer gespeichert hat, darf
    assert d.random_quote(1, 7) is None
