from datetime import date

from dave.daily import QOTD_FALLBACK, days_until, is_birthday, parse_birthday
from dave.guard import SpamDetector
from dave.serverdata import ServerData


def test_parse_birthday():
    assert parse_birthday("24.12.") == (24, 12)
    assert parse_birthday("3.7") == (3, 7)
    assert parse_birthday("24.12.2005") == (24, 12)  # Jahr wird ignoriert
    assert parse_birthday("29.2.") == (29, 2)
    assert parse_birthday("31.2.") is None and parse_birthday("morgen") is None


def test_birthday_logic():
    assert is_birthday(24, 12, date(2026, 12, 24))
    assert is_birthday(29, 2, date(2027, 2, 28))       # kein Schaltjahr -> am 28.2.
    assert not is_birthday(29, 2, date(2028, 2, 28))   # Schaltjahr -> am 29.2.
    assert days_until(24, 12, date(2026, 11, 21)) == 33
    assert days_until(1, 1, date(2026, 12, 31)) == 1


def test_birthday_storage(tmp_path):
    d = ServerData(tmp_path)
    d.set_birthday(1, 7, 24, 12)
    d.set_birthday(1, 7, 25, 12)  # überschreibt
    assert d.birthdays(1) == [(7, 25, 12)]
    assert d.delete_birthday(1, 7) and d.birthdays(1) == []


def test_qotd_fallback():
    assert len(QOTD_FALLBACK) >= 25 and all(q.endswith("?") for q in QOTD_FALLBACK)


def test_starboard_storage(tmp_path):
    d = ServerData(tmp_path)
    assert d.starboard_get(1, 5) is None
    d.starboard_set(1, 5, 99)
    assert d.starboard_get(1, 5) == 99


def test_spam_detector():
    t = [0.0]
    s = SpamDetector(clock=lambda: t[0])
    assert s.check(1, 7, 10, "Hallo zusammen", 0, False, 400) is None
    assert s.check(1, 8, 10, "FREE NITRO here https://dlscord-gift.ru/x", 0, False, 400) == "sieht nach Scam/Phishing aus"
    assert s.check(1, 9, 10, "Schau mal https://discord.gift/abc", 0, False, 400) is None  # echter Discord-Link
    assert s.check(1, 9, 10, "Spiel? https://steamcommunity.com/id/x", 0, False, 400) is None
    for i in range(2):
        assert s.check(1, 11, 10 + i, "kauft meinen kram", 0, False, 400) is None
    assert "mehrfach" in s.check(1, 11, 12, "kauft meinen kram", 0, False, 400)
    assert s.check(1, 11, 12, "kauft meinen kram", 0, False, 400) is None  # schon gemeldet -> 2 Min. Ruhe
    assert "Massen" in s.check(1, 12, 10, "hey", 7, False, 400)
    assert "neuer Account" in s.check(1, 13, 10, "guck https://x.y", 0, False, 1)
    t[0] = 5
    for i in range(5):
        s.check(1, 14, 10, f"msg {i}", 0, False, 400)
    assert "viele Nachrichten" in s.check(1, 14, 10, "msg 6", 0, False, 400)
