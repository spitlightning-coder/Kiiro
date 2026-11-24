"""Verständlich bleiben: Sprachstil pro Person, Antwortlänge passend zur Frage, „hä?“ erkennen."""

from __future__ import annotations

import re

import discord
from discord import app_commands

from .longterm import STYLES, MemoryStore

EINFACH = ("## Sprache für {name}\n"
           "{name} mag es einfach. Erkläre wie für jemanden, der keine Nerd-Begriffe kennt: kurze Sätze, "
           "Alltagswörter, Fachbegriffe nur mit kurzer Erklärung in Klammern, höchstens ein kleiner Witz.")

REPHRASE = ("## Nicht verstanden\n"
            "{name} hat deine letzte Antwort nicht verstanden. Sag dasselbe noch mal, aber viel einfacher: "
            "2–3 kurze Sätze, Alltagswörter, kein Fachjargon, kein Witz. Keine Entschuldigungs-Arie, "
            "fang direkt mit der einfacheren Erklärung an.\nDeine letzte Antwort war: „{last}“")

FINAL_CHECK = ("## Kurz-Check vor dem Antworten\n"
               "Gehst du direkt auf das ein, was gerade gesagt wurde? Alltagssprache wie ein Kumpel im Chat? "
               "Lass Roboter-Technik-Sprech weg (Kohlenstoff-Irgendwas, Sensoren, Prozessoren, Schaltkreise, "
               "Protokolle, Algorithmen, Nullen und Einsen, Systemdiagnose) – auch wenn du früher im Verlauf so "
               "geredet hast. Ergibt deine Antwort Sinn und passt sie zum Gesagten? Lieber normal und klar als ein "
               "schräger Witz – höchstens einer, und nur wenn er wirklich passt.")

SWITCH_INFO = "\n-# Ich red ab jetzt einfacher mit dir. Zurück geht's mit `/stil normal`."

_CONFUSED = re.compile(
    r"^\s*(?:@?\S+\s+)?(?:"
    r"h(?:ä|ae|e)+h?\??|was\?+|wie bitte\??|hä\s*was\??|\?{2,}|"
    r"(?:ich\s+)?(?:versteh|verstehe|kapier|kapiere|check|checke|raff|raffe)(?:\s+ich)?\s+(?:das\s+|es\s+)?(?:nicht|nich|net|ned|nix|gar\s+nicht)|"
    r"(?:ich\s+)?(?:versteh|verstehe|kapier|kapiere|check|checke|raff|raffe)\s+(?:nur\s+)?bahnhof|"
    r"bahnhof|zu\s+kompliziert|geht(?:'s|s)?\s+(?:das\s+)?(?:auch\s+)?einfacher|erklär(?:'s|s)?\s+(?:mal\s+)?einfacher|"
    r"in\s+einfach(?:er)?|auf\s+deutsch\s+bitte|nochmal\s+(?:auf\s+)?(?:deutsch|einfach)"
    r")[\s?!.]*$",
    re.IGNORECASE)


def is_confused(text: str) -> bool:
    """„hä?“, „versteh ich nicht“, „???“, „geht's auch einfacher?“ – kurze Nachfragen, keine echten Fragen."""
    text = text.strip()
    return bool(text) and len(text) <= 60 and bool(_CONFUSED.match(text))


def length_hint(text: str) -> str:
    words = len(text.split())
    lower = text.lower()
    if any(w in lower for w in ("ausführlich", "erklär mal genau", "im detail", "genauer", "schritt für schritt")) \
            or words >= 60:
        return "Länge: Die Person will es genauer wissen – eine ausführlichere Antwort ist okay, aber gut gegliedert."
    if words <= 6:
        return "Länge: Die Nachricht ist kurz – antworte auch kurz, 1–2 Sätze."
    return "Länge: Antworte in 1–3 Sätzen."


def style_hint(store: MemoryStore | None, guild_id: int, user_id: int, name: str) -> str | None:
    if store and store.get_style(guild_id, user_id) == "einfach":
        return EINFACH.format(name=name)
    return None


def register_stil(tree: app_commands.CommandTree, store: MemoryStore) -> None:
    @tree.command(name="stil", description="Wie Dave mit dir redet: normal oder einfach (ohne Nerd-Begriffe)")
    @app_commands.describe(stil="normal = wie immer, einfach = kurze Sätze und Alltagswörter")
    @app_commands.choices(stil=[app_commands.Choice(name=s, value=s) for s in STYLES])
    @app_commands.guild_only()
    async def stil(interaction: discord.Interaction, stil: app_commands.Choice[str]):
        store.set_style(interaction.guild_id, interaction.user.id, stil.value)
        text = ("Alles klar, ich red mit dir ab jetzt einfach und ohne Fachchinesisch."
                if stil.value == "einfach" else "Okay, zurück zum normalen Dave.")
        await interaction.response.send_message(text, ephemeral=True)
