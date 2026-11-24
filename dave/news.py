"""Echte News per RSS/Atom in den News-Kanal. Ohne KI-Aufruf, kostet also kein Requesty-Limit.

Standard-Quellen (anpassbar per /dave news_quelle_hinzu / _weg):
PC Games Hardware (Gaming/Hardware), Golem (Technik), Spektrum (Wissenschaft/Weltraum), UploadVR (VR).
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import aiohttp
import discord

from .serverdata import ServerData

log = logging.getLogger("dave.news")

DEFAULT_FEEDS = [
    "https://www.pcgameshardware.de/feed.cfm",
    "https://rss.golem.de/rss.php?feed=RSS2.0",
    "https://www.spektrum.de/alias/rss/spektrum-de-rss-feed/996406",
    "https://www.uploadvr.com/rss/",
]
INTERVAL_SECONDS = 60 * 60   # höchstens einmal pro Stunde News
MAX_PER_FEED = 2
MAX_PER_RUN = 5
USER_AGENT = "DaveDiscordBot/1.0 (RSS-Reader)"
COLORS = [0x5865F2, 0x57F287, 0xFEE75C, 0xEB459E, 0xED4245, 0x3BA55C]

_TAGS = re.compile(r"<[^>]+>")
_NS = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}


@dataclass
class Item:
    id: str
    title: str
    link: str
    summary: str
    image: str | None
    source: str
    published: str


def clean_text(raw: str | None, limit: int = 280) -> str:
    text = html.unescape(_TAGS.sub(" ", raw or ""))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + " …"


def _t(el, path: str) -> str:
    found = el.find(path, _NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_feed(xml_text: str) -> list[Item]:
    """RSS 2.0 und Atom. Neueste zuerst (so wie die Feeds sie liefern)."""
    root = ET.fromstring(xml_text)
    items: list[Item] = []
    if root.tag.endswith("rss") or root.find("channel") is not None:
        channel = root.find("channel")
        source = _t(channel, "title") or "News"
        for it in channel.findall("item"):
            link = _t(it, "link")
            image = None
            enc = it.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image"):
                image = enc.get("url")
            media = it.find("media:content", _NS)
            if image is None and media is not None and media.get("medium", "image") == "image":
                image = media.get("url")
            pub = _t(it, "pubDate")
            try:
                pub = parsedate_to_datetime(pub).strftime("%d.%m. %H:%M") if pub else ""
            except (TypeError, ValueError):
                pub = ""
            items.append(Item(id=_t(it, "guid") or link, title=clean_text(_t(it, "title"), 250), link=link,
                              summary=clean_text(_t(it, "description")), image=image, source=clean_text(source, 60),
                              published=pub))
    else:  # Atom
        source = _t(root, "atom:title") or "News"
        for it in root.findall("atom:entry", _NS):
            link_el = it.find("atom:link[@rel='alternate']", _NS)
            if link_el is None:
                link_el = it.find("atom:link", _NS)
            link = link_el.get("href", "") if link_el is not None else ""
            items.append(Item(id=_t(it, "atom:id") or link, title=clean_text(_t(it, "atom:title"), 250), link=link,
                              summary=clean_text(_t(it, "atom:summary") or _t(it, "atom:content")), image=None,
                              source=clean_text(source, 60), published=_t(it, "atom:updated")[:16].replace("T", " ")))
    return [i for i in items if i.id and i.title and i.link.startswith("http")]


def make_embed(item: Item, color: int) -> discord.Embed:
    embed = discord.Embed(title=item.title, url=item.link, description=item.summary or None, color=color)
    embed.set_footer(text=f"{item.source}{' · ' + item.published if item.published else ''}")
    if item.image and item.image.startswith("http"):
        embed.set_image(url=item.image)
    return embed


class News:
    def __init__(self, bot: discord.Client, data: ServerData, actions):
        self.bot, self.data, self.actions = bot, data, actions
        self._task: asyncio.Task | None = None

    def feeds(self, guild_id: int) -> list[str]:
        return self.data.get(guild_id, "news_feeds", DEFAULT_FEEDS)

    def channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        cid = self.data.get(guild.id, "news_channel")
        ch = guild.get_channel(cid) if cid else discord.utils.find(lambda c: c.name.endswith("news"),
                                                                   guild.text_channels)
        if ch and ch.permissions_for(guild.me).send_messages and ch.permissions_for(guild.me).embed_links:
            return ch
        return None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def due(self, guild_id: int, now: float | None = None) -> bool:
        """Letzte News-Runde merkt sich Dave dauerhaft – so gibt's auch nach Neustarts nicht öfter News."""
        last = self.data.get(guild_id, "news_last_run", 0) or 0
        return (now or time.time()) - last >= INTERVAL_SECONDS - 60

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)
        while not self.bot.is_closed():
            for guild in list(self.bot.guilds):
                if not self.due(guild.id):
                    continue
                try:
                    self.data.set(guild.id, "news_last_run", time.time())
                    await self.run_once(guild)
                except Exception:  # noqa: BLE001 – News dürfen Dave nie lahmlegen
                    log.exception("News-Runde fehlgeschlagen")
            await asyncio.sleep(5 * 60)  # alle 5 Min. prüfen, ob die Stunde rum ist

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> list[Item]:
        async with session.get(url, headers={"User-Agent": USER_AGENT},
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return parse_feed(await resp.text())

    async def run_once(self, guild: discord.Guild) -> int:
        if self.actions.stopped(guild) or not self.data.get(guild.id, "news_on", True):
            return 0
        channel = self.channel(guild)
        if channel is None:
            return 0
        posted = 0
        async with aiohttp.ClientSession() as session:
            for n, url in enumerate(self.feeds(guild.id)):
                try:
                    items = await self.fetch(session, url)
                except Exception as e:  # noqa: BLE001
                    log.warning("Feed %s nicht lesbar: %s", url, e)
                    continue
                fresh = [i for i in items if not self.data.news_seen(guild.id, i.id)]
                first_time = not self.data.get(guild.id, f"feed_init:{url}", False)
                if first_time:  # beim ersten Mal nicht fluten: nur die neueste Meldung
                    self.data.set(guild.id, f"feed_init:{url}", True)
                    self.data.mark_news(guild.id, [i.id for i in items[1:]])
                    fresh = fresh[:1]
                for item in reversed(fresh[:MAX_PER_FEED]):  # ältere zuerst posten
                    if posted >= MAX_PER_RUN:
                        break
                    await channel.send(embed=make_embed(item, COLORS[n % len(COLORS)]))
                    self.data.set(guild.id, "last_headline", f"{item.title} ({item.source})")
                    posted += 1
                self.data.mark_news(guild.id, [i.id for i in fresh])
        if posted:
            log.info("%d News in #%s gepostet", posted, channel.name)
        return posted
