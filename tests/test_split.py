from dave.bot import split_message


def test_short_stays_whole():
    assert split_message("hallo") == ["hallo"]


def test_long_splits_under_limit():
    text = ("wort " * 1000).strip()
    parts = split_message(text, limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert " ".join(parts) == text


def test_prefers_paragraphs():
    text = "a" * 60 + "\n\n" + "b" * 60
    assert split_message(text, limit=100) == ["a" * 60, "b" * 60]


def test_no_spaces_hard_cut():
    parts = split_message("x" * 250, limit=100)
    assert [len(p) for p in parts] == [100, 100, 50]
