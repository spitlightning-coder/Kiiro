from dave.memory import ChannelMemory


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_merges_users_and_alternates():
    m = ChannelMemory()
    m.add(1, "Anna", "Hi")
    m.add(1, "Ben", "@Dave was geht")
    m.add(1, "Dave", "Alles gut", from_dave=True)
    m.add(1, "Anna", "cool")
    assert m.as_messages(1) == [
        {"role": "user", "content": "Anna: Hi\nBen: @Dave was geht"},
        {"role": "assistant", "content": "Alles gut"},
        {"role": "user", "content": "Anna: cool"},
    ]


def test_drops_leading_and_trailing_dave():
    m = ChannelMemory(max_messages=2)
    m.add(1, "Anna", "eins")
    m.add(1, "Dave", "a", from_dave=True)
    m.add(1, "Ben", "zwei")  # "eins" fällt raus -> Verlauf beginnt mit Dave
    assert m.as_messages(1) == [{"role": "user", "content": "Ben: zwei"}]
    m.add(1, "Dave", "b", from_dave=True)  # letzte Nachricht von Dave wird abgeschnitten
    assert m.as_messages(1) == [{"role": "user", "content": "Ben: zwei"}]


def test_channels_are_separate_and_expire():
    clock = Clock()
    m = ChannelMemory(max_age_seconds=60, clock=clock)
    m.add(1, "Anna", "kanal eins")
    m.add(2, "Ben", "kanal zwei")
    assert m.as_messages(2) == [{"role": "user", "content": "Ben: kanal zwei"}]
    clock.t = 61
    m.add(1, "Anna", "neu")
    assert m.as_messages(1) == [{"role": "user", "content": "Anna: neu"}]
    assert m.as_messages(2) == []


def test_ignores_empty():
    m = ChannelMemory()
    m.add(1, "Anna", "   ")
    assert m.as_messages(1) == []
