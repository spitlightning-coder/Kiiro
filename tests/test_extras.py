from dave.extras import HILFE, Counting, parse_count
from dave.serverdata import ServerData


def test_parse_count():
    assert parse_count("12") == 12
    assert parse_count(" 13 haha") == 13
    assert parse_count("hallo 14") is None
    assert parse_count("") is None


def test_counting_rules(tmp_path):
    c = Counting(ServerData(tmp_path))
    assert c.check(1, 10, 1) == (True, None)
    assert c.check(1, 11, 2) == (True, None)
    ok, msg = c.check(1, 11, 3)  # gleiche Person zweimal
    assert not ok and "zweimal" in msg and "**2**" in msg
    assert c.check(1, 10, 1)[0]  # nach Reset wieder bei 1
    ok, msg = c.check(1, 11, 5)  # falsche Zahl
    assert not ok and "**2**" in msg
    assert c.data.get(1, "count_record") == 2


def test_counting_milestones(tmp_path):
    c = Counting(ServerData(tmp_path))
    for n in range(1, 50):
        c.check(1, n % 2, n)
    ok, msg = c.check(1, 9, 50)
    assert ok and "Rekord" in msg


def test_hilfe_mentions_everything():
    for word in ["/erinnerung", "/rollen", "/umfrage", "counting", "news", "/vergiss_mich"]:
        assert word in HILFE


def test_groups_and_norm():
    from types import SimpleNamespace

    from dave.extras import group_roles, lead_emoji, norm, parse_groups
    assert norm("⚔️ Elden Ring") == "elden ring" and norm("🎬 Filme & Serien") == "filme & serien"
    assert lead_emoji("⚔️ Elden Ring") == "⚔️" and lead_emoji("GTA") is None
    groups = parse_groups("Pings: News-Ping | Spiele: GTA, Elden Ring")
    assert groups == [("Pings", ["News-Ping"]), ("Spiele", ["GTA", "Elden Ring"])]
    roles = {1: SimpleNamespace(id=1, name="⚔️ Elden Ring"), 2: SimpleNamespace(id=2, name="🔔 News-Ping"),
             3: SimpleNamespace(id=3, name="🚗 GTA"), 4: SimpleNamespace(id=4, name="Sonstiges")}
    guild = SimpleNamespace(get_role=roles.get)
    out = group_roles(guild, [1, 2, 3, 4], groups)
    assert [(t, [r.id for r in rs]) for t, rs in out] == [("Pings", [2]), ("Spiele", [3, 1]), ("Weitere", [4])]


def test_tars_settings(tmp_path):
    from dave.extras import parse_setting, setting_reply, settings_line
    assert parse_setting("Dave, Humor auf 60 %") == ("humor", 60)
    assert parse_setting("ehrlichkeit 100 prozent bitte") == ("ehrlichkeit", 100)
    assert parse_setting("Humor auf 250%") == ("humor", 100)
    assert parse_setting("ich mag deinen Humor") is None
    assert "60" in setting_reply("humor", 60) or "Humor" in setting_reply("humor", 60)
    d = ServerData(tmp_path)
    assert settings_line(d, 1) == "Deine Einstellungen: Humor 75 %, Ehrlichkeit 90 %."
    d.set(1, "humor", 30)
    assert "Humor 30 %" in settings_line(d, 1)


def test_tars_reply_tiers():
    from dave.extras import SETTING_REPLIES, setting_reply
    assert setting_reply("humor", 0) in SETTING_REPLIES["humor"][0][1]
    assert setting_reply("ehrlichkeit", 100) in SETTING_REPLIES["ehrlichkeit"][-1][1]
    assert "{n}" not in setting_reply("humor", 55)
