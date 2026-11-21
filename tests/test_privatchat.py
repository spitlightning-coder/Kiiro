import dataclasses
from types import SimpleNamespace as N

import pytest

from dave.bot import Dave
from dave.config import Config
from dave.longterm import MemoryStore
from dave.serverdata import ServerData


class Brain:
    personality = "x"

    def __init__(self):
        self.calls = []

    async def reply(self, history, **kw):
        self.calls.append((history, kw))
        return "Moin privat!"


class Channel:
    def __init__(self):
        self.id, self.sent = 555, []

    async def send(self, text):
        self.sent.append(text)

    def typing(self):
        return _ATyping()


class _ATyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def msg(uid, text, channel):
    author = N(id=uid, bot=False, display_name="Anna")
    return N(author=author, content=text, attachments=[], reference=None, channel=channel, guild=None)


@pytest.fixture
def dave(tmp_path):
    cfg = dataclasses.replace(Config.from_env(), data_dir=tmp_path, selfroles="")
    bot = Dave(cfg, Brain(), MemoryStore(tmp_path, use_chroma=False), ServerData(tmp_path))
    guild = N(id=1, name="Ultramegabomba", get_member=lambda uid: N(display_name="Anna") if uid == 7 else None)
    bot.home_guild = lambda user: guild if user.id == 7 else None
    bot.store.add_fact(1, 7, "Anna", "mag Pizza")
    bot.store.add_fact(1, 8, "Ben", "geheimes Hobby")
    return bot


async def test_member_gets_answer_with_own_facts_only(dave):
    ch = Channel()
    await dave._on_dm(msg(7, "na Dave?", ch))
    assert ch.sent == ["Moin privat!"]
    history, kw = dave.brain.calls[0]
    assert history[-1]["content"] == "Anna: na Dave?"
    assert "Privatchat" in kw["context"] and "mag Pizza" in kw["context"]
    assert "geheimes Hobby" not in kw["context"]
    assert kw["channel"] == "Privatchat mit Anna"


async def test_stranger_is_ignored(dave):
    ch = Channel()
    await dave._on_dm(msg(99, "hi", ch))
    assert ch.sent == [] and dave.brain.calls == []


async def test_switched_off_tells_once(dave):
    dave.actions.data.set(1, "dm_on", False)
    ch = Channel()
    await dave._on_dm(msg(7, "hi", ch))
    await dave._on_dm(msg(7, "hallo??", ch))
    assert len(ch.sent) == 1 and "ausgeschaltet" in ch.sent[0]
    assert dave.brain.calls == []


async def test_rate_limit(dave):
    dave.dm_limit.per_hour = 2
    ch = Channel()
    for t in ("a", "b", "c", "d"):
        await dave._on_dm(msg(7, t, ch))
    assert len(dave.brain.calls) == 2
    assert sum("Pause" in s for s in ch.sent) == 1
