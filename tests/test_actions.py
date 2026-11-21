from types import SimpleNamespace

import discord

from dave.actions import RateLimit, role_problem
from dave.serverdata import ServerData


class FakeRole:
    def __init__(self, rid, name, position, perms=None, managed=False, default=False):
        self.id, self.name, self.position, self.managed = rid, name, position, managed
        self.permissions = discord.Permissions(**(perms or {}))
        self._default = default

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position


def me(position=10, manage_roles=True):
    return SimpleNamespace(top_role=FakeRole(0, "Dave", position),
                           guild_permissions=discord.Permissions(manage_roles=manage_roles))


def test_role_chain():
    gamer = FakeRole(1, "Gamer", 3)
    assert role_problem(gamer, me(), [1]) is None
    assert "nicht zur Selbstwahl" in role_problem(gamer, me(), [])
    assert "Admin" in role_problem(FakeRole(2, "Mod", 3, {"kick_members": True}), me(), [2])
    assert "Admin" in role_problem(FakeRole(3, "Boss", 3, {"administrator": True}), me(), [3])
    assert "über meiner" in role_problem(FakeRole(4, "Hoch", 12), me(), [4])
    assert "Rollen verwalten" in role_problem(gamer, me(manage_roles=False), [1])
    assert "von Hand" in role_problem(FakeRole(5, "Bot", 2, managed=True), me(), [5])


def test_rate_limit():
    t = [0.0]
    rl = RateLimit(2, clock=lambda: t[0])
    assert rl.allow(("g", 1)) and rl.allow(("g", 1))
    assert not rl.allow(("g", 1))
    assert rl.allow(("g", 2))  # andere Person
    t[0] = 3601
    assert rl.allow(("g", 1))


def test_serverdata_settings_and_reminders(tmp_path):
    d = ServerData(tmp_path, clock=lambda: 100.0)
    assert d.stopped(1) is False
    d.set(1, "stopped", True)
    assert d.stopped(1) and not d.stopped(2)
    d.set_self_role(1, 55, True)
    d.set_self_role(1, 66, True)
    d.set_self_role(1, 55, False)
    assert d.self_roles(1) == [66]
    rid = d.add_reminder(1, 9, 7, 150.0, "Kuchen")
    d.add_reminder(1, 9, 7, 500.0, "später")
    assert [r.text for r in d.due_reminders(now=200.0)] == ["Kuchen"]
    d.finish_reminder(rid)
    assert d.due_reminders(now=200.0) == []
    assert [r.text for r in d.open_reminders(1, 7)] == ["später"]
    assert d.cancel_reminder(1, 8, d.open_reminders(1, 7)[0].id) is False  # fremde Erinnerung
    assert d.cancel_reminder(1, 7, d.open_reminders(1, 7)[0].id) is True
    d.close()
    d2 = ServerData(tmp_path)
    assert d2.stopped(1) and d2.self_roles(1) == [66]  # bleibt nach Neustart
    d2.close()
