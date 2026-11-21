"""Deutsche Zeitangaben verstehen: "in 10 Minuten", "um 18:30", "morgen um 9", "am 24.12. um 18 Uhr"."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_UNITS = {
    "sek": 1, "sekunde": 1, "sekunden": 1, "s": 1,
    "min": 60, "minute": 60, "minuten": 60, "m": 60,
    "h": 3600, "std": 3600, "stunde": 3600, "stunden": 3600,
    "tag": 86400, "tage": 86400, "tagen": 86400, "d": 86400,
    "woche": 604800, "wochen": 604800,
}
_WORDS = {"einer": 1, "einem": 1, "ein": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
          "zehn": 10, "zwanzig": 20, "dreißig": 30, "halben": 0.5, "halbe": 0.5}

_IN = re.compile(r"^in\s+(?P<n>\d+(?:[.,]\d+)?|" + "|".join(_WORDS) + r")\s*(?P<u>[a-zäöü]+)\b", re.I)
_CLOCK = r"(?P<h>\d{1,2})(?:[:.](?P<mi>\d{2}))?\s*(?:uhr)?"
_DAY = re.compile(r"^(?:(?P<rel>heute|morgen|übermorgen)|am\s+(?P<d>\d{1,2})\.(?P<mo>\d{1,2})\.(?P<y>\d{2,4})?)"
                  r"(?:\s*(?:um|gegen)?\s*" + _CLOCK + r")?", re.I)
_AT = re.compile(r"^(?:um|gegen)\s+" + _CLOCK, re.I)


def parse_when(text: str, now: datetime) -> tuple[datetime, str] | None:
    """Sucht am Anfang von `text` eine Zeitangabe. Gibt (Zeitpunkt, Rest-Text) zurück oder None."""
    t = text.strip()
    m = _IN.match(t)
    if m:
        raw, unit = m.group("n").lower(), m.group("u").lower()
        n = _WORDS.get(raw) or float(raw.replace(",", "."))
        secs = _UNITS.get(unit)
        if secs is None:
            return None
        return now + timedelta(seconds=n * secs), t[m.end():]

    m = _DAY.match(t)
    if m:
        hour = int(m.group("h")) if m.group("h") else 9
        minute = int(m.group("mi") or 0)
        if hour > 23 or minute > 59:
            return None
        if m.group("rel"):
            add = {"heute": 0, "morgen": 1, "übermorgen": 2}[m.group("rel").lower()]
            day = (now + timedelta(days=add)).date()
        else:
            year = int(m.group("y")) if m.group("y") else now.year
            if year < 100:
                year += 2000
            try:
                day = datetime(year, int(m.group("mo")), int(m.group("d"))).date()
            except ValueError:
                return None
            if not m.group("y") and day < now.date():
                day = day.replace(year=year + 1)
        when = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=hour, minute=minute)
        return when, t[m.end():]

    m = _AT.match(t)
    if m:
        hour, minute = int(m.group("h")), int(m.group("mi") or 0)
        if hour > 23 or minute > 59:
            return None
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when, t[m.end():]
    return None


_ERINNER = re.compile(r"\berinnere?\s+mich\s+(?P<rest>.+)", re.I | re.S)
_FILLER = re.compile(r"^\s*(?:,|bitte|nochmal|daran|an|dass|,\s*dass)\s*", re.I)


def parse_reminder(text: str, now: datetime) -> tuple[datetime, str] | None:
    """'Dave, erinner mich in 10 Minuten an den Kuchen' -> (Zeitpunkt, 'den Kuchen')."""
    m = _ERINNER.search(text)
    if not m:
        return None
    rest = m.group("rest").strip()
    found = parse_when(rest, now)
    if not found:
        return None
    when, what = found
    for _ in range(3):
        what = _FILLER.sub("", what)
    what = what.strip().rstrip(".!?")
    return (when, what) if what else None


def describe(when: datetime, now: datetime) -> str:
    delta = when - now
    if delta < timedelta(hours=1):
        mins = max(1, round(delta.total_seconds() / 60))
        return f"in {mins} Minute{'n' if mins != 1 else ''}"
    if when.date() == now.date():
        return f"heute um {when:%H:%M}"
    if when.date() == (now + timedelta(days=1)).date():
        return f"morgen um {when:%H:%M}"
    return f"am {when:%d.%m.%Y um %H:%M}"
