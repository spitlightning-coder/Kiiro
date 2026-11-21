from dave.extras import is_exclusive, norm, parse_groups, role_colors

SPEC = ("🔔 Benachrichtigungen: News-Ping, 🤝 Mitspieler-Ping | "
        "🎨 Namensfarbe (nur eine): 🔴 Rot #e74c3c, 🔵 Blau #3498DB | 🎮 Spiele: Baldur's Gate 3")


def test_parse_strips_colors():
    groups = parse_groups(SPEC)
    assert groups[1] == ("🎨 Namensfarbe (nur eine)", ["🔴 Rot", "🔵 Blau"])
    assert groups[2] == ("🎮 Spiele", ["Baldur's Gate 3"])


def test_role_colors():
    assert role_colors(SPEC) == {"rot": 0xE74C3C, "blau": 0x3498DB}


def test_exclusive_marker():
    assert is_exclusive("🎨 Namensfarbe (nur eine)")
    assert is_exclusive("Farbe (eine)")
    assert not is_exclusive("🎮 Spiele")
    assert norm("🤝 Mitspieler-Ping") == "mitspieler-ping"
