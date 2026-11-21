import asyncio
import time

from dave.community import event_embed, event_view
from dave.serverdata import ServerData


def test_event_storage_and_rsvp(tmp_path):
    d = ServerData(tmp_path)
    start = time.time() + 3600
    eid = d.add_event(1, 5, 7, "GTA-Abend", "Heist!", start, 99)
    d.rsvp(eid, 7, "ja")
    d.rsvp(eid, 8, "vielleicht")
    d.rsvp(eid, 9, "nein")
    d.rsvp(eid, 9, None)  # zurückgezogen
    assert d.rsvps(eid) == {"ja": [7], "vielleicht": [8], "nein": []}
    assert d.due_events(time.time()) == []                      # noch zu früh
    assert [e["id"] for e in d.due_events(start - 800)] == [eid]  # 15-Min-Erinnerung fällig
    d.set_event_stage(eid, 1)
    assert d.due_events(start - 800) == []
    assert [e["id"] for e in d.due_events(start + 1)] == [eid]    # Start fällig
    d.set_event_stage(eid, 2)
    assert d.due_events(start + 999) == []


def test_event_embed_and_view(tmp_path):
    d = ServerData(tmp_path)
    eid = d.add_event(1, 5, 7, "GTA-Abend", "", time.time() + 3600, 99)
    d.rsvp(eid, 7, "ja")
    e = event_embed(d.get_event(eid), d.rsvps(eid))
    assert e.title == "📅 GTA-Abend"
    names = [f.name for f in e.fields]
    assert "✅ Bin dabei (1)" in names and "🤔 Vielleicht (0)" in names
    assert any("<@&99>" in f.value for f in e.fields)

    async def make():
        v = event_view(eid)
        assert [c.custom_id for c in v.children] == [f"dave:event:{eid}:ja", f"dave:event:{eid}:vielleicht",
                                                     f"dave:event:{eid}:nein"]
        v.to_components()
    asyncio.run(make())
