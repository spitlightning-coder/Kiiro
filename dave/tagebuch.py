"""/tagebuch: Dave erzählt, was er auf dem Server über sich gelernt hat und wie er sich entwickelt.

Zeigt nur Daves eigene Notizen und Zahlen – keine Fakten über einzelne Leute (die gibt's privat per /was_weisst_du).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from .longterm import MemoryStore, Stats

log = logging.getLogger("dave.tagebuch")

COOLDOWN = 60
TZ = ZoneInfo("Europe/Berlin")

DIARY_TASK = """Schreib einen kurzen Tagebucheintrag (3-5 Sätze, Ich-Form, Deutsch) als Dave über deine Zeit auf
dem Discord-Server "{server}". Grundlage sind nur diese Notizen über dich selbst, was auf dem Server los war, und die Zahlen unten.
Erfinde keine Erlebnisse und nenne keine Fakten über einzelne Leute. Bleib in deinem Ton (trocken, TARS-Humor).
Keine Überschrift, kein Datum, keine Aufzählung.

Deine Notizen über dich:
{notes}

Was zuletzt auf dem Server los war (nur Zusammenfassungen):
{server_notes}

Zahlen: {numbers}"""


def numbers_line(stats: Stats, now: float | None = None) -> str:
    parts = [f"{stats.facts} Fakten über {stats.people} Leute",
             f"{stats.server_facts + stats.server_notes} über den Server",
             f"{stats.memories} Erinnerungen", f"{stats.self_notes} Notizen über mich"]
    if stats.since:
        days = int(((now or time.time()) - stats.since) // 86400)
        parts.append(f"seit {datetime.fromtimestamp(stats.since, TZ):%d.%m.%Y}"
                     + (f" ({days} Tage)" if days >= 1 else " (seit heute)"))
    return " · ".join(parts)


def build_embed(server: str, entry: str | None, notes: list[str], stats: Stats,
                server_lines: list[str] = ()) -> discord.Embed:
    if not notes and not stats.facts and not stats.memories and not server_lines:
        desc = "Noch leer. Ich bin hier noch ein unbeschriebenes Blatt – redet mehr mit mir, dann steht hier bald was."
    else:
        desc = entry or "Heute keine Lust auf Prosa. Hier die nackten Notizen:"
    embed = discord.Embed(title="📓 Daves Tagebuch", description=desc[:4000], color=0x8B6F47)
    if notes:
        lines = "\n".join(f"• {n}" for n in notes[:10])
        embed.add_field(name="Was ich über mich gelernt hab", value=lines[:1024], inline=False)
    if server_lines:
        lines = "\n".join(f"• {n}" for n in server_lines[:6])
        embed.add_field(name="Was auf dem Server los war", value=lines[:1024], inline=False)
    embed.set_footer(text=f"{server} · {numbers_line(stats)}")
    return embed


def register_tagebuch(tree: app_commands.CommandTree, store: MemoryStore, brain, get_awareness=None) -> None:
    last: dict[int, float] = {}

    @tree.command(name="tagebuch", description="Dave erzählt, was er hier über sich gelernt hat und wie er sich entwickelt")
    @app_commands.describe(privat="Nur für dich sichtbar")
    @app_commands.guild_only()
    async def tagebuch(interaction: discord.Interaction, privat: bool = False):
        gid = interaction.guild_id
        now = time.monotonic()
        if now - last.get(gid, -COOLDOWN) < COOLDOWN:
            await interaction.response.send_message("Ich hab grad erst ins Tagebuch geschrieben. Eine Minute Pause.",
                                                    ephemeral=True)
            return
        last[gid] = now
        await interaction.response.defer(ephemeral=privat, thinking=True)
        notes = store.self_notes(gid, limit=12)
        stats = store.stats(gid)
        awareness = get_awareness() if get_awareness else None
        server_lines = [n.text for n in awareness.recent_for_diary(gid)] if awareness else []
        entry = None
        if notes or server_lines:
            system = (getattr(brain, "personality", "") + "\n\n" if getattr(brain, "personality", "") else "")
            task = DIARY_TASK.format(server=interaction.guild.name,
                                     notes="\n".join(f"- {n}" for n in notes) or "- (noch keine)",
                                     server_notes="\n".join(f"- {n}" for n in server_lines) or "- (noch nichts)",
                                     numbers=numbers_line(stats))
            try:
                entry = await brain.complete(system + "Du schreibst gerade in dein Tagebuch.", task, max_tokens=400)
            except Exception:  # noqa: BLE001
                log.exception("Tagebucheintrag fehlgeschlagen")
        await interaction.followup.send(embed=build_embed(interaction.guild.name, entry, notes, stats, server_lines),
                                        ephemeral=privat)
