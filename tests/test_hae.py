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
        self.calls.append(kw)
        return "Einfach gesagt: Das Spiel ist groß."


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Channel:
    id = 42
    name = "main-chat"

    def __init__(self):
        self.sent = []

    def typing(self):
        return Typing()

    async def send(self, text, **kw):
        self.sent.append(text)

    def permissions_for(self, _who):
        return N(view_channel=False, read_message_history=False, send_messages=True)


def make_msg(dave, text, channel, guild):
    replies = []

    async def reply(content, **kw):
        replies.append(content)

    m = N(author=N(id=7, bot=False, display_name="Anna", guild_permissions=N(administrator=False),
                   roles=[], joined_at=None, mention="@Anna"),
          content=text, clean_content=text, guild=guild, channel=channel, mentions=[], reference=None,
          attachments=[], poll=None, reply=reply, id=1, created_at=None, role_mentions=[], mention_everyone=False,
          embeds=[], stickers=[])
    m.replies = replies
    return m


@pytest.fixture
def setup(tmp_path):
    cfg = dataclasses.replace(Config.from_env(), data_dir=tmp_path, selfroles="", dave_channel_ids=frozenset())
    bot = Dave(cfg, Brain(), MemoryStore(tmp_path, use_chroma=False), ServerData(tmp_path))
    bot.antispam = None
    guild = N(id=1, name="Ultramegabomba", default_role="everyone", me="me", text_channels=[])
    return bot, guild, Channel()


async def test_hae_right_after_dave_explains_simpler_and_switches_style(setup):
    bot, guild, ch = setup
    bot.memory.add(ch.id, "Anna", "wie groß ist die map?")
    bot.memory.add(ch.id, "Dave", "Die Kartenfläche umfasst ca. 88 km² mit prozeduraler Biom-Generierung.",
                   from_dave=True)
    msg = make_msg(bot, "hä?", ch, guild)
    await bot.on_message(msg)
    assert msg.replies and msg.replies[0].startswith("Einfach gesagt")
    assert "/stil normal" in msg.replies[0]
    assert "nicht verstanden" in bot.brain.calls[0]["context"]
    assert "prozeduraler" in bot.brain.calls[0]["context"]
    assert bot.store.get_style(1, 7) == "einfach"
    # zweites "hä?" -> wieder einfacher, aber kein erneuter Hinweis
    msg2 = make_msg(bot, "versteh ich nicht", ch, guild)
    await bot.on_message(msg2)
    assert msg2.replies and "/stil normal" not in msg2.replies[0]
    assert "Anna mag es einfach" in bot.brain.calls[1]["context"]


async def test_hae_without_dave_is_ignored(setup):
    bot, guild, ch = setup
    bot.memory.add(ch.id, "Ben", "ich hab grad nen Toaster gekauft")
    msg = make_msg(bot, "hä?", ch, guild)
    await bot.on_message(msg)
    assert not msg.replies and not bot.brain.calls


async def test_seed_memory_after_restart(setup):
    bot, guild, ch = setup
    bot._connection.user = None
    older = [N(author=N(id=99, bot=True, display_name="Dave"), clean_content="Moin, was geht?", content="", guild=guild),
             N(author=N(id=5, bot=False, display_name="Ben"), clean_content="Wir spielen Helldivers", content="",
               guild=guild)]  # neueste zuerst, wie Discord es liefert

    def history(limit, before, after):
        async def gen():
            for m in older:
                yield m
        return gen()

    ch.history = history
    bot.config = dataclasses.replace(bot.config, dave_channel_ids=frozenset({ch.id}))
    msg = make_msg(bot, "ah ja sehr toll", ch, guild)
    await bot.on_message(msg)
    sent = bot.brain.calls[0]
    texts = [e.text for e in bot.memory.recent(ch.id)]
    assert texts[:2] == ["Wir spielen Helldivers", "ah ja sehr toll"]  # fremde Bots fliegen raus
    assert sent and msg.replies


async def test_busy_message_only_once_then_hourglass(setup):
    from dave.brain import LIMIT_ANTWORT

    bot, guild, ch = setup

    async def limited(history, **kw):
        return LIMIT_ANTWORT
    bot.brain.reply = limited
    bot.config = dataclasses.replace(bot.config, dave_channel_ids=frozenset({ch.id}))
    bot.memory.add(ch.id, "Anna", "hi")
    reactions = []
    m1, m2 = make_msg(bot, "wie viele kanäle gibts?", ch, guild), make_msg(bot, "okay", ch, guild)

    async def react(e):
        reactions.append(e)
    m2.add_reaction = react
    await bot.on_message(m1)
    await bot.on_message(m2)
    assert m1.replies == [LIMIT_ANTWORT] and m2.replies == [] and reactions == ["⏳"]
    assert all(not e.from_dave for e in bot.memory.recent(ch.id))  # Meldung nicht im Gedächtnis


def test_server_info_lists_only_public_channels(setup):
    bot, _guild, _ch = setup

    def chan(name, public):
        return N(name=name, id=len(name), permissions_for=lambda who: N(view_channel=public))
    guild = N(name="Ultramegabomba", member_count=23, default_role="everyone",
              text_channels=[chan("main-chat", True), chan("ratssitzung", False)],
              voice_channels=[chan("Gaming", True)])
    info = bot.server_info(guild)
    assert "23 Mitglieder" in info and "main-chat = <#9>" in info and "ratssitzung" not in info
    assert "Textkanäle (1)" in info and "Sprachkanäle (1)" in info
