import sqlite3
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import openai

from dave import backup as bk
from dave.bot import image_attachments
from dave.brain_openai import with_images
from dave.longterm import MemoryStore, Stats
from dave.tagebuch import build_embed, numbers_line

from .test_brain_openai import HIST, err, make, resp


def _data(tmp_path):
    d = tmp_path / "data"
    (d / "chroma").mkdir(parents=True)
    db = sqlite3.connect(d / "server.sqlite3")
    db.execute("CREATE TABLE t (x)")
    db.execute("INSERT INTO t VALUES (42)")
    db.commit()
    db.close()
    (d / "chroma" / "index.bin").write_bytes(b"hnsw")
    (d / "models").mkdir()
    (d / "models" / "big.onnx").write_bytes(b"x" * 100)
    return d


def test_backup_contains_data_but_not_models(tmp_path):
    d = _data(tmp_path)
    path = bk.make_backup(d)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert {"server.sqlite3", "chroma/index.bin"} <= names
        assert not any(n.startswith(("models/", "backups/")) for n in names)
        zf.extract("server.sqlite3", tmp_path / "out")
    assert sqlite3.connect(tmp_path / "out" / "server.sqlite3").execute("SELECT x FROM t").fetchone() == (42,)


def test_backup_keeps_only_newest(tmp_path):
    d = _data(tmp_path)
    start = datetime(2026, 1, 1, 4, 0)
    for i in range(10):
        bk.make_backup(d, keep=7, now=start + timedelta(days=i))
    names = [p.name for p in bk.list_backups(d)]
    assert len(names) == 7
    assert names[0].startswith("dave-backup-2026-01-04") and names[-1].startswith("dave-backup-2026-01-10")
    assert not list(bk.backup_dir(d).glob("*.part"))


def test_newest_age(tmp_path):
    d = _data(tmp_path)
    assert bk.newest_age(d) is None
    p = bk.make_backup(d)
    assert bk.newest_age(d, clock=lambda: p.stat().st_mtime + 50) == 50


def test_with_images_builds_parts():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "Anna: was ist das?"}]
    out = with_images(msgs, [("image/png", b"\x89PNG")])
    assert msgs[-1]["content"] == "Anna: was ist das?"  # Original unverändert
    parts = out[-1]["content"]
    assert parts[0] == {"type": "text", "text": "Anna: was ist das?"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_reply_with_image_sends_parts():
    b, fake = make(resp("Ein Pinguin."))
    assert await b.reply(HIST, server="S", channel="c", images=[("image/jpeg", b"abc")]) == "Ein Pinguin."
    assert fake.calls[0]["messages"][-1]["content"][1]["type"] == "image_url"


async def test_reply_with_image_falls_back_to_text():
    b, fake = make(err(openai.BadRequestError, 400), err(openai.BadRequestError, 400),
                   resp("Bild seh ich grad nicht."))
    answer = await b.reply(HIST, server="S", channel="c", images=[("image/jpeg", b"abc")])
    assert answer == "Bild seh ich grad nicht."
    last = fake.calls[-1]["messages"][-1]["content"]
    assert isinstance(last, str) and "nicht ansehen" in last


def _att(name, ctype, size=1000):
    return SimpleNamespace(filename=name, content_type=ctype, size=size)


def test_image_attachments_filters(monkeypatch):
    import dave.bot as botmod

    class Msg(SimpleNamespace):
        pass

    monkeypatch.setattr(botmod.discord, "Message", Msg)
    replied = Msg(attachments=[_att("b.webp", None)], reference=None)
    msg = Msg(attachments=[_att("a.png", "image/png"), _att("x.txt", "text/plain"),
                           _att("huge.jpg", "image/jpeg", size=20_000_000), _att("c.gif", "image/gif")],
              reference=SimpleNamespace(resolved=replied))
    assert [(a.filename, m) for a, m in image_attachments(msg)] == [
        ("a.png", "image/png"), ("huge.jpg", "image/jpeg"), ("b.webp", "image/webp")]
    assert [a.filename for a, _ in image_attachments(msg, include_reply=False)] == ["a.png", "huge.jpg"]


def test_stats_and_numbers(tmp_path):
    store = MemoryStore(tmp_path / "d", use_chroma=False, clock=lambda: 1_700_000_000.0)
    store.add_fact(1, 10, "Anna", "mag Pizza")
    store.add_fact(1, 10, "Anna", "spielt VR")
    store.add_fact(1, 11, "Ben", "baut PCs")
    store.add_fact(1, None, "Server", "Hier wird viel gezockt")
    store.add_self_note(1, "Wird 'Blechkopf' genannt")
    st = store.stats(1)
    assert (st.facts, st.people, st.server_facts, st.self_notes, st.since) == (3, 2, 1, 1, 1_700_000_000.0)
    line = numbers_line(st, now=1_700_000_000.0 + 3 * 86400)
    assert "3 Fakten über 2 Leute" in line and "(3 Tage)" in line
    assert store.stats(2).since is None


def test_embed_never_shows_people_facts():
    st = Stats(5, 2, 0, 3, 1, None)
    e = build_embed("Ultramegabomba", "Lieber Tagebuch …", ["Wird 'Blechkopf' genannt"], st)
    assert e.description == "Lieber Tagebuch …"
    assert "Blechkopf" in e.fields[0].value
    empty = build_embed("S", None, [], Stats(0, 0, 0, 0, 0, None))
    assert "unbeschriebenes Blatt" in empty.description


def test_resized_url_for_big_screenshots():
    from dave.bot import resized_url

    big = SimpleNamespace(width=3840, height=2160, size=9_000_000,
                          proxy_url="https://media.discordapp.net/attachments/1/2/image.png?ex=a&hm=b")
    assert resized_url(big) == ("https://media.discordapp.net/attachments/1/2/image.png?ex=a&hm=b"
                                "&width=1600&height=900&format=webp")
    small = SimpleNamespace(width=800, height=600, size=200_000, proxy_url="https://m/x.png")
    assert resized_url(small) is None


async def test_load_images_uses_proxy_for_big_ones():
    from dave.bot import load_images

    class Http:
        def __init__(self):
            self.urls = []

        async def get_from_cdn(self, url):
            self.urls.append(url)
            return b"webp"

    async def read():
        return b"original"

    big = SimpleNamespace(width=4000, height=3000, size=12_000_000, proxy_url="https://m/x.png", filename="x.png",
                          read=read)
    small = SimpleNamespace(width=500, height=500, size=100_000, proxy_url="https://m/y.png", filename="y.png",
                            read=read)
    http = Http()
    assert await load_images([(big, "image/png"), (small, "image/png")], http) == [
        ("image/webp", b"webp"), ("image/png", b"original")]
    assert http.urls == ["https://m/x.png?width=1600&height=1200&format=webp"]
