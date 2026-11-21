from dave.news import clean_text, make_embed, parse_feed

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title><![CDATA[Spektrum.de RSS-Feed]]></title>
<item><title><![CDATA[Supernova in flagranti erwischt]]></title><link>https://example.org/a</link>
<description><![CDATA[Die <b>Supernova</b> wurde &amp; so weiter.]]></description>
<pubDate>Thu, 19 Nov 2026 16:00:00 +0100</pubDate><guid isPermaLink="false">sdwv-1</guid>
<media:content url="https://example.org/a.jpg" medium="image"/></item>
<item><title>Zweite</title><link>https://example.org/b</link><guid>g2</guid>
<enclosure url="https://example.org/b.jpg" type="image/jpeg"/></item>
<item><title>Ohne Link</title></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>heise online</title>
<entry><title>Neue GPU</title><link rel="alternate" href="https://example.org/gpu"/><id>tag:1</id>
<summary>Schnell &lt;i&gt;und&lt;/i&gt; teuer</summary><updated>2026-11-21T10:00:00+01:00</updated></entry>
</feed>"""


def test_parse_rss():
    items = parse_feed(RSS)
    assert [i.title for i in items] == ["Supernova in flagranti erwischt", "Zweite"]  # ohne Link fliegt raus
    a, b = items
    assert a.id == "sdwv-1" and a.link == "https://example.org/a"
    assert a.summary == "Die Supernova wurde & so weiter."
    assert a.image == "https://example.org/a.jpg" and b.image == "https://example.org/b.jpg"
    assert a.source == "Spektrum.de RSS-Feed" and a.published == "19.11. 16:00"


def test_parse_atom():
    (item,) = parse_feed(ATOM)
    assert item.title == "Neue GPU" and item.link == "https://example.org/gpu" and item.id == "tag:1"
    assert item.summary == "Schnell und teuer" and item.source == "heise online"


def test_clean_text_cuts_at_word():
    text = clean_text("wort " * 100, limit=30)
    assert len(text) <= 30 and text.endswith("…")


def test_embed():
    e = make_embed(parse_feed(RSS)[0], 0x123456)
    assert e.title.startswith("Supernova") and e.url == "https://example.org/a"
    assert "Spektrum" in e.footer.text and e.image.url == "https://example.org/a.jpg"
