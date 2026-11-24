import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N

import pytest

from dave import awareness as aw
from dave.longterm import MemoryStore
from dave.serverdata import ServerData
from dave.sprache import is_confused, length_hint, style_hint


@pytest.mark.parametrize("text", ["hä?", "Hä", "häh??", "was?", "???", "versteh ich nicht", "ich versteh das nicht",
                                  "kapier ich nicht", "check ich nicht", "Bahnhof", "versteh nur bahnhof",
                                  "geht's auch einfacher?", "erklär mal einfacher", "@Dave hä?", "wie bitte?"])
def test_confused(text):
    assert is_confused(text)


@pytest.mark.parametrize("text", ["was machst du heute?", "hä wieso ist GTA 6 noch nicht draußen, das ist doch "
                                  "schon ewig angekündigt und so", "ich versteh nicht warum Valorant so beliebt ist",
                                  "ok", "", "was geht"])
def test_not_confused(text):
    assert not is_confused(text)


def test_length_hint():
    assert "kurz" in length_hint("na wie gehts")
    assert "1–3" in length_hint("kannst du mir sagen welche Grafikkarte für 300 Euro gerade gut ist")
    assert "ausführlich" in length_hint("erklär mir ausführlich wie schwarze Löcher entstehen")


def test_style_roundtrip(tmp_path):
    store = MemoryStore(tmp_path, use_chroma=False)
    assert store.get_style(1, 7) == "normal" and style_hint(store, 1, 7, "Anna") is None
    store.set_style(1, 7, "einfach")
    assert store.get_style(1, 7) == "einfach" and "Anna mag es einfach" in style_hint(store, 1, 7, "Anna")
    assert store.get_style(2, 7) == "normal"
    with pytest.raises(ValueError):
        store.set_style(1, 7, "piratisch")


def test_server_notes_and_activity(tmp_path):
    t = [1_700_000_000.0]
    store = MemoryStore(tmp_path, use_chroma=False, clock=lambda: t[0])
    a = store.add_server_note(1, "summary", "Es ging um Helldivers 2.", channel_id=5, mood="lebhaft",
                              topics=["Helldivers 2", "Koop"])
    t[0] += 100
    store.add_server_note(1, "event", "Anna ist beigetreten.")
    notes = store.server_notes(1)
    assert [n.kind for n in notes] == ["event", "summary"]
    assert notes[1].topics == ["Helldivers 2", "Koop"] and notes[1].mood == "lebhaft"
    assert store.server_notes(1, kinds=("summary",), channel_id=6) == []
    assert store.stats(1).server_notes == 2
    store.delete_server_notes([a])
    assert [n.kind for n in store.server_notes(1)] == ["event"]
    for _ in range(3):
        store.count_activity(1, 5, when=t[0])
    store.count_activity(1, 6, when=t[0] - 7200)
    assert store.activity_by_channel(1, t[0] - 3600) == [(5, 3)]
    assert dict(store.activity_by_channel(1, t[0] - 86400)) == {5: 3, 6: 1}
    store.prune_activity(t[0] - 3600)
    assert store.activity_by_channel(1, 0) == [(5, 3)]


def test_parse_summaries():
    raw = "```json\n" + json.dumps({"kanaele": [
        {"kanal": "「✍」main-chat", "zusammenfassung": "Planung für Samstag.", "themen": ["Spieleabend"],
         "stimmung": "Lebhaft", "insider": ["der Toaster"]},
        {"kanal": "gibtsnicht", "zusammenfassung": "x"},
        {"kanal": "news", "zusammenfassung": "", "themen": []}]}) + "\n```"
    out = aw.parse_summaries(raw, {"「✍」main-chat": 10, "「🔊」news": 11})
    assert out == [{"channel_id": 10, "text": "Planung für Samstag.", "topics": ["Spieleabend"], "mood": "lebhaft",
                    "insider": ["der Toaster"]}]
    assert aw.parse_summaries("kaputt", {}) == []


# ---------- Awareness mit Fake-Discord ----------

class Msg(N):
    pass


class Chan:
    def __init__(self, cid, name, messages, public=True):
        self.id, self.name, self._messages, self.public = cid, name, messages, public
        self.guild = None

    def permissions_for(self, who):
        ok = self.public or who == "me"
        return N(view_channel=ok, read_message_history=True)

    def history(self, limit, after, oldest_first):
        msgs = [m for m in self._messages if m.created_at > after][:limit]

        async def gen():
            for m in msgs:
                yield m
        return gen()


class Brain:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    async def complete(self, system, user, max_tokens=0, background=False):
        self.prompts.append(user)
        return self.answers.pop(0) if self.answers else None


def msg(name, text, minutes_ago, now, bot=False):
    return Msg(author=N(display_name=name, bot=bot), content=text, clean_content=text,
               created_at=datetime.fromtimestamp(now, timezone.utc) - timedelta(minutes=minutes_ago))


@pytest.fixture
def world(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    clock = [now]
    store = MemoryStore(tmp_path, use_chroma=False, clock=lambda: clock[0])
    data = ServerData(tmp_path)
    monkeypatch.setattr(aw.discord, "TextChannel", Chan)
    main = Chan(10, "「✍」main-chat", [msg("Anna", f"Nachricht {i}", 30 - i, now) for i in range(6)]
                + [msg("Bot", "beep", 1, now, bot=True)])
    quiet = Chan(11, "「🤳」bilder-videos", [msg("Ben", "hi", 5, now)])
    secret = Chan(12, "「🏛」ratssitzung", [msg("Chef", f"geheim {i}", 10, now) for i in range(9)], public=False)
    guild = N(id=1, default_role="everyone", me="me", text_channels=[main, quiet, secret],
              get_channel=lambda cid: {10: main, 11: quiet, 12: secret}.get(cid))
    for c in (main, quiet, secret):
        c.guild = guild
    return N(store=store, data=data, guild=guild, main=main, quiet=quiet, secret=secret, clock=clock, now=now)


def make_aw(world, *answers):
    return aw.Awareness(N(), world.store, world.data, Brain(*answers), clock=lambda: world.clock[0])


async def test_run_once_summarizes_only_public_busy_channels(world):
    answer = json.dumps({"kanaele": [{"kanal": "「✍」main-chat", "zusammenfassung": "Alle reden über Helldivers.",
                                      "themen": ["Helldivers 2"], "stimmung": "lebhaft", "insider": []}]})
    a = make_aw(world, answer)
    assert [c.id for c in a.observed(world.guild)] == [10, 11]
    assert await a.run_once(world.guild) == 1
    prompt = a.brain.prompts[0]
    assert "main-chat" in prompt and "geheim" not in prompt and "beep" not in prompt and "bilder" not in prompt
    notes = world.store.server_notes(1)
    assert len(notes) == 1 and notes[0].channel_id == 10 and notes[0].mood == "lebhaft"
    assert world.data.get(1, "aware_last:10") is not None
    assert world.data.get(1, "aware_last:11") is None  # zu wenig los -> wartet
    # zweiter Lauf: nichts Neues
    assert await a.run_once(world.guild) == 0


async def test_excluded_channel_and_mood(world):
    a = make_aw(world, json.dumps({"kanaele": [{"kanal": "「✍」main-chat", "zusammenfassung": "Streit über Regeln.",
                                                "themen": ["Regeln"], "stimmung": "angespannt"}]}))
    await a.run_once(world.guild)
    assert a.is_tense(1, 10)
    world.clock[0] += aw.MOOD_VALID + 1
    assert not a.is_tense(1, 10)  # alte Einschätzung zählt nicht mehr
    a.set_observed(1, 10, False)
    assert [c.id for c in a.observed(world.guild)] == [11]
    a.count(N(guild=world.guild, author=N(bot=False), channel=world.main))
    assert world.store.activity_by_channel(1, 0) == []


async def test_context_and_situation(world):
    s = world.store
    s.add_server_note(1, "summary", "Spieleabend am Samstag geplant.", channel_id=10, mood="entspannt",
                      topics=["Spieleabend", "Helldivers 2"])
    s.add_server_note(1, "event", "Anna ist dem Server beigetreten.")
    s.count_activity(1, 10, when=world.now)
    a = make_aw(world)
    ctx = await a.context(world.guild, 10, "wann zocken wir")
    assert "Spieleabend am Samstag" in ctx and "entspannt" in ctx and "Anna ist dem Server" in ctx
    assert await a.context(world.guild, 11, "x") is not None  # Ereignisse gelten überall
    sit = a.situation(world.guild)
    assert sit["topics"][:2] == ["Spieleabend", "Helldivers 2"] and sit["active"] == [(10, 1)]
    assert sit["moods"] == {10: "entspannt"} and len(sit["events"]) == 1
    embed = aw.situation_embed(world.guild, sit)
    assert "Spieleabend" in embed.fields[0].value


async def test_compact_to_weekly(world):
    s = world.store
    old = world.now - aw.COMPACT_AFTER - 3 * 86400
    for i in range(3):
        s.add_server_note(1, "summary", f"Tag {i}: Minecraft-Bau.", channel_id=10, topics=["Minecraft"],
                          created_at=old + i * 3600)
    s.add_server_note(1, "summary", "Heute: Quiz.", channel_id=10)
    a = make_aw(world, json.dumps({"zusammenfassung": "Die Woche stand im Zeichen von Minecraft.",
                                   "themen": ["Minecraft"]}))
    assert await a.compact(world.guild) == 1
    kinds = sorted(n.kind for n in s.server_notes(1))
    assert kinds == ["summary", "weekly"]
    weekly = s.server_notes(1, kinds=("weekly",))[0]
    assert weekly.text.startswith("Woche ") and "main-chat" in weekly.text
    assert await a.compact(world.guild) == 0  # nur einmal pro Tag


def test_note_event_without_loop(world):
    a = make_aw(world)
    a.note_event(1, "Neues Event: Spieleabend")
    assert world.store.server_notes(1)[0].text == "Neues Event: Spieleabend"


def test_peak_hours():
    base = int(datetime(2026, 11, 20, 20, 0, tzinfo=aw.TZ).timestamp() // 3600)
    assert aw.peak_hours([(base, 50), (base + 1, 10), (base - 10, 5)], top=2) == [20, 21]
