import json

from dave.learning import Learner, Line, build_context, parse_extraction, parse_merk_dir
from dave.longterm import MemoryStore


def test_parse_merk_dir():
    assert parse_merk_dir("Dave, merk dir: ich spiele am liebsten Star Citizen") == "ich spiele am liebsten Star Citizen"
    assert parse_merk_dir("@Dave merk dir bitte dass ich Pizza mag!") == "ich Pizza mag"
    assert parse_merk_dir("Merk dir mal: Freitag ist Spieleabend.") == "Freitag ist Spieleabend"
    assert parse_merk_dir("was geht dave") is None
    assert parse_merk_dir("merkwürdig") is None


def test_parse_extraction_filters_unknown_ids_and_garbage():
    raw = "<think>hmm</think>Hier:\n" + json.dumps({
        "fakten": [{"id": "10", "name": "Anna", "fakt": "Anna mag Pizza"},
                   {"id": "999", "name": "Fremd", "fakt": "erfunden"},
                   {"id": "abc", "fakt": "kaputt"}, "quatsch"],
        "server": ["Freitags Spieleabend", 3],
        "erinnerung": "Es ging um Pizza.",
        "dave_selbst": ["Dave findet Ananas auf Pizza okay", "b", "c"],
    })
    r = parse_extraction(raw, {10, 11})
    assert r.facts == [(10, "Anna", "Anna mag Pizza")]
    assert r.server == ["Freitags Spieleabend"]
    assert r.memory == "Es ging um Pizza."
    assert r.self_notes == ["Dave findet Ananas auf Pizza okay", "b"]


def test_parse_extraction_bad_input():
    assert parse_extraction(None, {1}).empty
    assert parse_extraction("kein json", {1}).empty
    assert parse_extraction("{kaputt", {1}).empty
    assert parse_extraction("[1, 2]", {1}).empty


class FakeBrain:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def complete(self, system, user, *, max_tokens=700):
        self.calls.append(user)
        return self.answer


def test_learner_triggers_only_when_dave_involved(tmp_path):
    store = MemoryStore(tmp_path, use_chroma=False)
    learner = Learner(store, FakeBrain("{}"), learn_every=5)
    for i in range(6):
        assert learner.note(1, Line(10, "Anna", f"nachricht {i}")) is False  # Dave nicht beteiligt
    assert learner.note(1, Line(10, "Anna", "@Dave hi"), addressed=True) is True
    assert len(learner.take(1)) == 7
    # Dave-Kanal lernt auch ohne Ansprache
    learner2 = Learner(store, FakeBrain("{}"), learn_every=5, always_channels=frozenset({2}))
    results = [learner2.note(2, Line(10, "Anna", "x")) for _ in range(5)]
    assert results == [False, False, False, False, True]
    store.close()


async def test_learner_stores_results(tmp_path):
    store = MemoryStore(tmp_path, use_chroma=False)
    answer = json.dumps({"fakten": [{"id": "10", "name": "Anna", "fakt": "Anna baut ein VR-Spiel"}],
                         "server": ["Der Server heißt intern Bombe"], "erinnerung": "",
                         "dave_selbst": ["Anna nennt Dave 'Blechkopf'"]})
    brain = FakeBrain(answer)
    learner = Learner(store, brain, learn_every=5)
    lines = [Line(10, "Anna", "ich bau grad ein VR-Spiel"), Line(None, "Dave", "Nice!", from_dave=True)]
    await learner.learn(1, 5, lines)
    assert "Anna [10]: ich bau grad ein VR-Spiel" in brain.calls[0]
    assert "Dave: Nice!" in brain.calls[0]
    assert [f.fact for f in store.facts_about(1, 10)] == ["Anna baut ein VR-Spiel"]
    assert [f.fact for f in store.server_facts(1)] == ["Der Server heißt intern Bombe"]
    assert store.self_notes(1) == ["Anna nennt Dave 'Blechkopf'"]
    store.close()


def test_build_context(tmp_path):
    store = MemoryStore(tmp_path, use_chroma=False)
    assert build_context(store, 1, [10], {10: "Anna"}, []) is None
    store.add_fact(1, 10, "Anna", "Anna mag Pizza")
    store.add_self_note(1, "Dave hat einen Running Gag über Toaster")
    ctx = build_context(store, 1, [10, 11], {10: "Anna"}, ["Letzte Woche ging es um VR"])
    assert "Anna: Anna mag Pizza" in ctx
    assert "Letzte Woche ging es um VR" in ctx
    assert "Toaster" in ctx
    assert "Grundregeln" in ctx
    store.close()
