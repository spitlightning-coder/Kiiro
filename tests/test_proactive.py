import time
from datetime import datetime
from types import SimpleNamespace

from dave.actions import TZ
from dave.proactive import PER_DAY, Proactive, clean_post, in_quiet_hours
from dave.serverdata import ServerData


class FakeActions:
    def __init__(self, stopped=False):
        self._stopped = stopped

    def stopped(self, guild):
        return self._stopped


def setup(tmp_path, mode="an", stopped=False):
    data = ServerData(tmp_path)
    data.set(1, "proactive_mode", mode)
    p = Proactive(None, data, None, FakeActions(stopped))
    guild = SimpleNamespace(id=1)
    channel = SimpleNamespace(id=5, name="main-chat")
    return p, guild, channel


def test_quiet_hours():
    assert in_quiet_hours(datetime(2026, 11, 21, 23, 30, tzinfo=TZ))
    assert in_quiet_hours(datetime(2026, 11, 21, 7, 59, tzinfo=TZ))
    assert not in_quiet_hours(datetime(2026, 11, 21, 15, 0, tzinfo=TZ))


def test_clean_post():
    assert clean_post("Mein Hirn hängt gerade kurz.") is None
    assert clean_post('"Hey @everyone, Pizza?"') == "Hey everyone, Pizza?"
    assert len(clean_post("x " * 400)) <= 452


def test_limits(tmp_path, monkeypatch):
    import dave.proactive as pro
    monkeypatch.setattr(pro, "in_quiet_hours", lambda now: False)
    p, g, c = setup(tmp_path)
    now = time.time()
    assert p.can_post(g, c, now) is None
    p.note_activity(5)
    assert p.can_post(g, c, time.time()) == "es wird gerade geschrieben"
    p.last_activity[5] = now - 3600
    p.data.set(1, f"proactive_last:{c.id}", now - 600)
    assert p.can_post(g, c, now) == "Kanal-Pause"
    p.data.set(1, f"proactive_last:{c.id}", 0)
    p.data.set(1, "proactive_day", p._today())
    p.data.set(1, "proactive_count", PER_DAY)
    assert p.can_post(g, c, now) == "Tageslimit"
    assert p.can_post(g, c, now, force=True) is None


def test_off_and_brake(tmp_path, monkeypatch):
    import dave.proactive as pro
    monkeypatch.setattr(pro, "in_quiet_hours", lambda now: False)
    p, g, c = setup(tmp_path, mode="aus")
    assert p.can_post(g, c) == "aus"
    p2, g2, c2 = setup(tmp_path / "b", stopped=True)
    assert p2.can_post(g2, c2, force=True) == "Notbremse"
