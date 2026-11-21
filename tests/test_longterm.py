import pytest

from dave.longterm import MAX_SELF_NOTES, MemoryStore


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 1
        return self.t


class WordEmbedding:
    """Winzige Test-Embedding-Funktion: Wort-Vorkommen über ein festes Vokabular."""

    VOCAB = ["pizza", "star", "wars", "vr", "katze", "auto", "musik", "spiel"]

    def __init__(self):
        pass

    def __call__(self, input):
        out = []
        for text in input:
            words = text.lower().split()
            vec = [float(sum(w.startswith(v) for w in words)) + 0.01 for v in self.VOCAB]
            out.append(vec)
        return out

    @staticmethod
    def name():
        return "test-words"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return WordEmbedding()

    def is_legacy(self):
        return False

    def embed_query(self, input):
        return self(input)


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path, use_chroma=False, clock=Clock())
    yield s
    s.close()


def test_facts_per_user_and_dedupe(store):
    store.add_fact(1, 10, "Anna", "Anna mag Pizza")
    store.add_fact(1, 10, "Anna", "anna mag pizza.")  # gleicher Fakt, anders geschrieben
    store.add_fact(1, 11, "Ben", "Ben spielt VR")
    store.add_fact(2, 10, "Anna", "Anna auf anderem Server")
    assert [f.fact for f in store.facts_about(1, 10)] == ["Anna mag Pizza"]
    assert [f.fact for f in store.facts_about(1, 11)] == ["Ben spielt VR"]
    assert [f.fact for f in store.facts_about(2, 10)] == ["Anna auf anderem Server"]


def test_server_facts_separate(store):
    store.add_fact(1, None, "Server", "Freitags ist Spieleabend")
    store.add_fact(1, 10, "Anna", "Anna mag Pizza")
    assert [f.fact for f in store.server_facts(1)] == ["Freitags ist Spieleabend"]


def test_self_notes_capped_newest_first(store):
    for i in range(MAX_SELF_NOTES + 5):
        store.add_self_note(1, f"Notiz {i}")
    notes = store.self_notes(1, limit=100)
    assert len(notes) == MAX_SELF_NOTES
    assert notes[0] == f"Notiz {MAX_SELF_NOTES + 4}"


def test_forget_user_only_hits_that_user(store):
    store.add_fact(1, 10, "Anna", "Anna mag Pizza")
    store.add_fact(1, 11, "Ben", "Ben spielt VR")
    facts, memories = store.forget_user(1, 10)
    assert (facts, memories) == (1, 0)
    assert store.facts_about(1, 10) == []
    assert len(store.facts_about(1, 11)) == 1


def test_persists_across_restart(tmp_path):
    s = MemoryStore(tmp_path, use_chroma=False)
    s.add_fact(1, 10, "Anna", "Anna mag Pizza")
    s.close()
    s2 = MemoryStore(tmp_path, use_chroma=False)
    assert [f.fact for f in s2.facts_about(1, 10)] == ["Anna mag Pizza"]
    s2.close()


def test_chroma_recall_and_forget(tmp_path):
    pytest.importorskip("chromadb")
    s = MemoryStore(tmp_path, embedding_function=WordEmbedding())
    assert s.collection is not None
    s.add_memory(1, 5, "Anna und Ben haben über Pizza geredet", [10, 11])
    s.add_memory(1, 5, "Star Wars Marathon geplant", [11])
    s.add_memory(2, 6, "Pizza auf anderem Server", [10])
    hits = s.recall(1, "wer mag pizza", k=1)
    assert hits == ["Anna und Ben haben über Pizza geredet"]
    assert s.memory_count(1, 11) == 2
    facts, memories = s.forget_user(1, 11)
    assert memories == 2
    assert s.memory_count(1) == 0
    assert s.recall(1, "pizza", k=3, max_distance=2) == []
    assert s.recall(2, "pizza", k=3) == ["Pizza auf anderem Server"]  # anderer Server bleibt
    s.close()
