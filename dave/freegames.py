"""Gratis-Spiele-Alarm: Epic Games Store (offizielle Store-Schnittstelle) + GamerPower (Steam, GOG, …).

Postet neue Gratis-Spiele (zum Behalten, keine Free-to-Play-Titel) in den Kanal mit „gratis“ im Namen
(sonst in den News-Kanal) und pingt die News-Ping-Rolle. Kein KI-Aufruf.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp
import discord

from .extras import norm
from .news import clean_text
from .serverdata import ServerData

log = logging.getLogger("dave.freegames")

EPIC_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=de&country=DE&allowCountries=DE"
GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways?platform=pc&type=game"
GOOD_STORES = ("steam", "gog", "epic games store", "ubisoft", "ea app", "origin", "battle.net", "amazon")
INTERVAL_SECONDS = 60 * 60
USER_AGENT = "DaveDiscordBot/1.0"
STORE_COLORS = {"Epic Games": 0x2A2A2A, "Steam": 0x1B2838, "GOG": 0x86328A}


@dataclass
class FreeGame:
    key: str          # stabile ID für "schon gepostet"
    title: str
    url: str
    store: str
    description: str
    image: str | None
    worth: str | None
    ends: str | None


def _end_text(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return dt.astimezone().strftime("%d.%m. %H:%M")


def parse_epic(data: dict) -> list[FreeGame]:
    """Nur Spiele, die gerade zu 100 % reduziert sind und normalerweise Geld kosten."""
    out = []
    elements = (((data or {}).get("data") or {}).get("Catalog") or {}).get("searchStore", {}).get("elements") or []
    for el in elements:
        price = ((el.get("price") or {}).get("totalPrice") or {})
        if price.get("discountPrice") != 0 or not price.get("originalPrice"):
            continue  # nicht gratis oder sowieso Free-to-Play
        promos = (el.get("promotions") or {}).get("promotionalOffers") or []
        offers = [o for p in promos for o in (p.get("promotionalOffers") or [])]
        if not offers:
            continue
        slug = next((m.get("pageSlug") for m in ((el.get("catalogNs") or {}).get("mappings") or [])
                     if m.get("pageType") == "productHome" and m.get("pageSlug")), None) \
            or (el.get("productSlug") or "").split("/")[0] or None
        if not slug:
            continue
        images = {i.get("type"): i.get("url") for i in (el.get("keyImages") or [])}
        image = images.get("OfferImageWide") or images.get("DieselStoreFrontWide") or images.get("Thumbnail")
        original = price.get("originalPrice", 0) / 100
        out.append(FreeGame(key=f"epic:{el.get('id')}", title=el.get("title", "?"),
                            url=f"https://store.epicgames.com/de/p/{slug}", store="Epic Games",
                            description=clean_text(el.get("description"), 250), image=image,
                            worth=f"{original:.2f} €".replace(".", ","), ends=_end_text(offers[0].get("endDate"))))
    return out


def parse_gamerpower(items: list) -> list[FreeGame]:
    out = []
    for it in items if isinstance(items, list) else []:
        if (it.get("type") or "").lower() != "game" or (it.get("status") or "Active").lower() != "active":
            continue
        platforms = (it.get("platforms") or "").lower()
        store = next((s for s in GOOD_STORES if s in platforms), None)
        if store is None:
            continue  # itch.io & Co. lassen wir weg, da ist zu viel Kleinkram dabei
        title = (it.get("title") or "").replace(" Giveaway", "").replace(" (Steam)", "").replace(" (GOG)", "").strip()
        worth = it.get("worth") if it.get("worth") not in (None, "N/A") else None
        ends = None if it.get("end_date") in (None, "N/A") else _end_text(it.get("end_date"))
        out.append(FreeGame(key=f"gp:{it.get('id')}", title=title, url=it.get("open_giveaway_url") or it.get("gamerpower_url"),
                            store={"epic games store": "Epic Games", "steam": "Steam", "gog": "GOG"}.get(store, store.title()),
                            description=clean_text(it.get("description"), 250), image=it.get("image") or it.get("thumbnail"),
                            worth=worth, ends=ends))
    return [g for g in out if g.url]


def merge(*lists: list[FreeGame]) -> list[FreeGame]:
    """Doppelte Titel (Epic-Spiele stehen auch bei GamerPower) nur einmal."""
    seen, out = set(), []
    for games in lists:
        for g in games:
            if norm(g.title) not in seen:
                seen.add(norm(g.title))
                out.append(g)
    return out


def make_embed(game: FreeGame) -> discord.Embed:
    embed = discord.Embed(title=f"🎁 {game.title}", url=game.url, description=game.description or None,
                          color=STORE_COLORS.get(game.store, 0x57F287))
    embed.add_field(name="Shop", value=game.store, inline=True)
    if game.worth:
        embed.add_field(name="Normalpreis", value=f"~~{game.worth}~~ **gratis**", inline=True)
    if game.ends:
        embed.add_field(name="Gratis bis", value=game.ends, inline=True)
    if game.image and game.image.startswith("http"):
        embed.set_image(url=game.image)
    embed.set_footer(text="Zum Behalten – einfach im Shop „kaufen“ für 0 €")
    return embed


class FreeGames:
    def __init__(self, bot: discord.Client, data: ServerData, actions, news):
        self.bot, self.data, self.actions, self.news = bot, data, actions, news
        self._task: asyncio.Task | None = None

    def channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        cid = self.data.get(guild.id, "freegames_channel")
        ch = guild.get_channel(cid) if cid else discord.utils.find(
            lambda c: "gratis" in c.name or "free-games" in c.name, guild.text_channels)
        if ch is None and self.news:
            ch = self.news.channel(guild)
        if ch and ch.permissions_for(guild.me).send_messages and ch.permissions_for(guild.me).embed_links:
            return ch
        return None

    def ping_role(self, guild: discord.Guild) -> discord.Role | None:
        return discord.utils.find(lambda r: norm(r.name) in ("news-ping", "gratis-spiele"), guild.roles)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)
        while not self.bot.is_closed():
            try:
                games = await self.fetch_all()
                for guild in list(self.bot.guilds):
                    await self.post_new(guild, games)
            except Exception:  # noqa: BLE001
                log.exception("Gratis-Spiele-Runde fehlgeschlagen")
            await asyncio.sleep(INTERVAL_SECONDS)

    async def fetch_all(self) -> list[FreeGame]:
        epic, gp = [], []
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            try:
                async with session.get(EPIC_URL, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    epic = parse_epic(await r.json(content_type=None))
            except Exception as e:  # noqa: BLE001
                log.warning("Epic nicht erreichbar: %s", e)
            try:
                async with session.get(GAMERPOWER_URL, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    gp = parse_gamerpower(await r.json(content_type=None))
            except Exception as e:  # noqa: BLE001
                log.warning("GamerPower nicht erreichbar: %s", e)
        log.info("Gratis-Spiele gefunden: %d bei Epic, %d bei GamerPower", len(epic), len(gp))
        return merge(epic, gp)

    async def post_new(self, guild: discord.Guild, games: list[FreeGame]) -> int:
        if self.actions.stopped(guild) or not self.data.get(guild.id, "freegames_on", True):
            return 0
        channel = self.channel(guild)
        if channel is None:
            return 0
        fresh = [g for g in games if not self.data.news_seen(guild.id, g.key)][:6]
        if not fresh:
            return 0
        role = self.ping_role(guild)
        intro = f"{role.mention} " if role else ""
        intro += "🎁 **Gratis-Spiele!**" if len(fresh) > 1 else "🎁 **Gratis-Spiel!**"
        await channel.send(intro, embeds=[make_embed(g) for g in fresh[:10]],
                           allowed_mentions=discord.AllowedMentions(roles=[role] if role else [], everyone=False))
        self.data.mark_news(guild.id, [g.key for g in games])
        log.info("%d Gratis-Spiele in #%s gepostet", len(fresh), channel.name)
        return len(fresh)
