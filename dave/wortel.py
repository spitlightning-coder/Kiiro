"""Tägliches Worträtsel (wie Wordle): ein deutsches 5-Buchstaben-Wort pro Tag, gleich für alle, 6 Versuche."""

from __future__ import annotations

import random
from datetime import datetime

import discord
from discord import app_commands

from .actions import TZ
from .serverdata import ServerData

LENGTH, TRIES = 5, 6
GREEN, YELLOW, GRAY = "🟩", "🟨", "⬛"

WORDS = """
ABEND ACKER ADLER AFFEN AKTIE ALARM ALBUM ANGEL APFEL ARENA ATLAS BAGEL BALKON BANDE BASIS BAUER BEERE BIRNE BLATT
BLICK BLUME BODEN BOGEN BOOTE BRAND BRIEF BROTE BRUST BUDEN BUNKER CHAOS DACHS DAMEN DAMPF DATEN DAUER DECKE DEICH
DISCO DRAHT DRAMA DUENE DURST EBENE ECHSE EIMER ENGEL ENKEL ERBSE ERNTE ESSIG EULEN FABEL FADEN FAHNE FALKE FARBE
FASER FEDER FEIER FELGE FERNE FIBEL FISCH FLECK FLOSS FLUSS FOLIE FORUM FRAGE FUCHS GABEL GARDE GEIST GELEE GENIE
GIEBEL GLANZ GLOCKE GNADE GRUFT GURKE HAFEN HAGEL HALLE HECKE HEFTE HERDE HIRSE HITZE HONIG HOTEL HUMOR
HUSTEN INSEL JACKE JAGD JOKER KABEL KAKAO KAMEL KANAL KANNE KARTE KASSE KATZE KERZE KETTE KEULE KIRSCHE KLANG KLEID
KNOPF KOFFER KOHLE KOMET KONTO KRAFT KRANZ KREIS KRONE KUGEL LAGER LAMPE LANZE LASER LEBEN LEDER LEHRE LESER LICHT
LIEBE LINSE LISTE LOEWE LOTTO LUNGE MAGIE MAPPE MARKT MASKE MAUER MENGE MESSE MIETE MILCH MODUS MONAT MOTOR
MUSIK NACHT NADEL NEBEL NICHT NOTIZ NUDEL OLIVE ORDEN PAKET PALME PANDA PAUSE PERLE PFERD PILOT PINIE PISTE PIXEL
PIZZA PLATZ POKAL PREIS PUDEL PUPPE QUARZ RABEN RADAR RAKETE RASEN RATTE REGAL REGEN REISE RINDE RITTER ROBBE ROMAN
RUDER SAITE SALAT SALBE SAMEN SATZ SCHAF SEIFE SENSE SERIE SOCKE SONNE SPASS SPIEL SPORT STADT STAHL STAND STERN
STIER STOCK STROM STUHL SUMPF TAFEL TANTE TASSE TEICH TEPPICH TIGER TINTE TISCH TITEL TORTE TRAUM TREUE TULPE
UFERN UNTER VILLA VOGEL WAAGE WACHS WAFFE WAGEN WALZE WANNE WATTE WELLE WESPE WETTE WIESE WOLKE WUNDE ZANGE ZEBRA
ZEILE ZELLE ZWERG
"""
ANSWERS = sorted({w for w in WORDS.split() if len(w) == LENGTH and w.isalpha() and w.isascii()})


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def word_of(day: str) -> str:
    return random.Random(f"dave-wortel-{day}").choice(ANSWERS)


def normalize(text: str) -> str:
    return (text.strip().upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE").replace("ß", "SS"))


def score(guess: str, answer: str) -> list[str]:
    """Farben wie bei Wordle, doppelte Buchstaben korrekt behandelt."""
    result = [GRAY] * LENGTH
    rest: dict[str, int] = {}
    for i, (g, a) in enumerate(zip(guess, answer)):
        if g == a:
            result[i] = GREEN
        else:
            rest[a] = rest.get(a, 0) + 1
    for i, g in enumerate(guess):
        if result[i] != GREEN and rest.get(g, 0) > 0:
            result[i] = YELLOW
            rest[g] -= 1
    return result


def board(guesses: list[str], answer: str, letters: bool = True) -> str:
    rows = []
    for g in guesses:
        colors = "".join(score(g, answer))
        rows.append(f"{colors}  `{' '.join(g)}`" if letters else colors)
    return "\n".join(rows) or "_noch nichts geraten_"


def register_wortel(tree: app_commands.CommandTree, data: ServerData) -> None:
    @tree.command(name="wort", description="Das tägliche Worträtsel: zeigt deinen Stand und die Regeln")
    @app_commands.guild_only()
    async def wort(interaction: discord.Interaction):
        day = today()
        guesses, result = data.wordle_get(interaction.guild_id, interaction.user.id, day)
        answer = word_of(day)
        e = discord.Embed(title=f"🔤 Worträtsel vom {datetime.now(TZ):%d.%m.}", color=0x57F287,
                          description=board(guesses, answer))
        if result == 0:
            e.add_field(name="So geht's", value=f"Rate das deutsche Wort mit {LENGTH} Buchstaben mit `/raten`. "
                        f"{GREEN} richtig · {YELLOW} kommt vor, andere Stelle · {GRAY} kommt nicht vor. "
                        f"Du hast {TRIES - len(guesses)} Versuche übrig. Umlaute als AE/OE/UE.", inline=False)
        else:
            e.add_field(name="Heute erledigt", value="Morgen gibt's ein neues Wort! Rangliste: `/wort_rangliste`",
                        inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @tree.command(name="raten", description="Rate das Wort des Tages")
    @app_commands.describe(wort=f"Ein Wort mit {LENGTH} Buchstaben")
    @app_commands.guild_only()
    async def raten(interaction: discord.Interaction, wort: str):
        day, guess = today(), normalize(wort)
        guesses, result = data.wordle_get(interaction.guild_id, interaction.user.id, day)
        answer = word_of(day)
        if result != 0:
            await interaction.response.send_message("Du hast heute schon fertig gespielt. Morgen gibt's ein neues Wort!",
                                                    ephemeral=True)
            return
        if len(guess) != LENGTH or not guess.isalpha() or not guess.isascii():
            await interaction.response.send_message(f"Bitte genau {LENGTH} Buchstaben (Umlaute als AE/OE/UE).",
                                                    ephemeral=True)
            return
        if guess in guesses:
            await interaction.response.send_message("Das hast du schon geraten.", ephemeral=True)
            return
        guesses.append(guess)
        if guess == answer:
            result = len(guesses)
        elif len(guesses) >= TRIES:
            result = -1
        data.wordle_set(interaction.guild_id, interaction.user.id, day, guesses, result)
        e = discord.Embed(title="🔤 Worträtsel", description=board(guesses, answer), color=0x57F287)
        if result > 0:
            e.add_field(name="🎉 Geschafft!", value=f"In {result}/{TRIES} Versuchen. Serie: "
                        f"{data.wordle_streak(interaction.guild_id, interaction.user.id, day)} 🔥", inline=False)
        elif result < 0:
            e.add_field(name="💀 Leider nicht", value=f"Das Wort war **{answer}**.", inline=False)
        else:
            e.set_footer(text=f"Noch {TRIES - len(guesses)} Versuche")
        await interaction.response.send_message(embed=e, ephemeral=True)
        if result != 0:  # Ergebnis öffentlich teilen – nur Farben, ohne Buchstaben
            share = f"🔤 **{interaction.user.display_name}** hat das Worträtsel " + \
                    (f"in **{result}/{TRIES}** geschafft!" if result > 0 else "heute nicht geknackt.") + \
                    f"\n{board(guesses, answer, letters=False)}\nSelbst spielen: `/wort`"
            await interaction.followup.send(share, ephemeral=False, allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="wort_rangliste", description="Die besten Worträtsel-Spieler")
    @app_commands.guild_only()
    async def rangliste(interaction: discord.Interaction):
        stats = data.wordle_stats(interaction.guild_id)
        if not stats:
            await interaction.response.send_message("Noch niemand hat das Worträtsel gespielt. Los geht's mit `/wort`!",
                                                    ephemeral=True)
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, solved, played, avg) in enumerate(stats):
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else f"<@{uid}>"
            lines.append(f"{medals[i] if i < 3 else f'{i + 1}.'} **{name}** – {solved}/{played} gelöst"
                         f"{f', Ø {avg:.1f} Versuche' if avg else ''}")
        await interaction.response.send_message(embed=discord.Embed(title="🔤 Worträtsel-Rangliste",
                                                                    description="\n".join(lines), color=0x57F287),
                                                allowed_mentions=discord.AllowedMentions.none())
