from datetime import datetime
from zoneinfo import ZoneInfo

from dave.zeit import describe, parse_reminder, parse_when

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 21, 19, 30, tzinfo=TZ)  # Samstag


def test_relative():
    when, rest = parse_when("in 10 Minuten an den Kuchen", NOW)
    assert when == datetime(2026, 11, 21, 19, 40, tzinfo=TZ)
    assert rest.strip() == "an den Kuchen"
    assert parse_when("in 2 Stunden", NOW)[0].hour == 21
    assert parse_when("in einer Stunde", NOW)[0].hour == 20
    assert parse_when("in 3 Tagen", NOW)[0].day == 24
    assert parse_when("in 5 blubb", NOW) is None


def test_clock_today_or_tomorrow():
    assert parse_when("um 20:15", NOW)[0] == datetime(2026, 11, 21, 20, 15, tzinfo=TZ)
    assert parse_when("um 18 Uhr", NOW)[0] == datetime(2026, 11, 22, 18, 0, tzinfo=TZ)  # schon vorbei -> morgen
    assert parse_when("um 25:00", NOW) is None


def test_days_and_dates():
    assert parse_when("morgen um 9", NOW)[0] == datetime(2026, 11, 22, 9, 0, tzinfo=TZ)
    assert parse_when("morgen", NOW)[0] == datetime(2026, 11, 22, 9, 0, tzinfo=TZ)
    assert parse_when("übermorgen 14:30", NOW)[0] == datetime(2026, 11, 23, 14, 30, tzinfo=TZ)
    assert parse_when("am 24.12. um 18 Uhr", NOW)[0] == datetime(2026, 12, 24, 18, 0, tzinfo=TZ)
    assert parse_when("am 1.3. um 10", NOW)[0] == datetime(2027, 3, 1, 10, 0, tzinfo=TZ)  # vorbei -> nächstes Jahr
    assert parse_when("am 31.2.", NOW) is None


def test_parse_reminder_from_chat():
    when, what = parse_reminder("@Dave erinner mich in 10 Minuten an den Kuchen!", NOW)
    assert when.minute == 40 and what == "den Kuchen"
    when, what = parse_reminder("Dave, erinnere mich morgen um 8 daran, dass ich Müll rausbringe", NOW)
    assert when == datetime(2026, 11, 22, 8, 0, tzinfo=TZ) and what == "ich Müll rausbringe"
    assert parse_reminder("erinnerst du dich an gestern?", NOW) is None
    assert parse_reminder("erinner mich irgendwann an X", NOW) is None


def test_describe():
    assert describe(datetime(2026, 11, 21, 19, 40, tzinfo=TZ), NOW) == "in 10 Minuten"
    assert describe(datetime(2026, 11, 21, 22, 0, tzinfo=TZ), NOW) == "heute um 22:00"
    assert describe(datetime(2026, 11, 22, 9, 0, tzinfo=TZ), NOW) == "morgen um 09:00"
    assert describe(datetime(2026, 12, 24, 18, 0, tzinfo=TZ), NOW) == "am 24.12.2026 um 18:00"
