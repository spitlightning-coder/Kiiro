"""/quiz: Multiple-Choice-Quiz mit Knöpfen, Punkten und Rangliste.

Die Fragen erzeugt die KI in EINEM Aufruf pro Quiz (schont das Limit). Klappt das nicht,
nimmt Dave Fragen aus der eingebauten Sammlung.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass

import discord
from discord import app_commands

from .brain_openai import clean
from .serverdata import ServerData

log = logging.getLogger("dave.quiz")

SECONDS_PER_QUESTION = 20
LETTERS = ["🇦", "🇧", "🇨", "🇩"]
DEFAULT_TOPIC = "gemischt: Videospiele, Star Wars, Weltraum, Wissenschaft, Technik"

QUIZ_SYSTEM = """Du erstellst Quizfragen für einen deutschen Discord-Server. Antworte NUR mit JSON:
{"fragen": [{"frage": "...", "antworten": ["...", "...", "...", "..."], "richtig": 0, "info": "kurzer Fakt dazu"}]}
Regeln:
- Genau 4 Antworten pro Frage, genau eine ist richtig, "richtig" ist ihr Index (0-3).
- Nur Fakten, bei denen du dir absolut sicher bist. Keine Fangfragen, keine Meinungen, nichts Zeitkritisches.
- Mittlere Schwierigkeit, abwechslungsreich, kurze Antworten (max. 60 Zeichen).
- Deutsch, locker formuliert. "info" ist ein Satz, max. 150 Zeichen."""

# (Frage, [Antworten], Index der richtigen, Info)
BANK = [
    ("Wie heißt der Planet, auf dem Luke Skywalker aufwächst?", ["Tatooine", "Naboo", "Hoth", "Endor"], 0,
     "Tatooine hat zwei Sonnen – daher der berühmte Doppel-Sonnenuntergang."),
    ("Wer ist Lukes Vater?", ["Darth Vader", "Obi-Wan Kenobi", "Han Solo", "Imperator Palpatine"], 0,
     "„Ich bin dein Vater“ fällt in Episode V – Das Imperium schlägt zurück."),
    ("Welche Farbe hat Mace Windus Lichtschwert?", ["Lila", "Grün", "Blau", "Gelb"], 0,
     "Samuel L. Jackson wünschte sich die lila Klinge selbst."),
    ("Wie heißt Han Solos Schiff?", ["Millennium Falke", "Sklave I", "Ghost", "Tantive IV"], 0,
     "Der Falke schaffte den Kessel-Flug in weniger als zwölf Parsec."),
    ("Welcher Planet ist der größte im Sonnensystem?", ["Jupiter", "Saturn", "Neptun", "Uranus"], 0,
     "In Jupiter würden über 1.300 Erden passen."),
    ("Welcher Planet ist der Sonne am nächsten?", ["Merkur", "Venus", "Mars", "Erde"], 0,
     "Ein Merkurjahr dauert nur 88 Erdtage."),
    ("Wie lange braucht Sonnenlicht ungefähr bis zur Erde?", ["8 Minuten", "8 Sekunden", "8 Stunden", "1 Minute"], 0,
     "Genauer sind es etwa 8 Minuten und 20 Sekunden."),
    ("Was ist ein Lichtjahr?", ["Eine Entfernung", "Eine Zeitspanne", "Eine Helligkeit", "Eine Geschwindigkeit"], 0,
     "Die Strecke, die Licht in einem Jahr zurücklegt – rund 9,46 Billionen km."),
    ("Wie heißt die Galaxie, in der wir leben?", ["Milchstraße", "Andromeda", "Sombrero", "Whirlpool"], 0,
     "Die Milchstraße hat schätzungsweise 100 bis 400 Milliarden Sterne."),
    ("Welches Teleskop startete 2021 und beobachtet vor allem im Infrarot?", ["James Webb", "Hubble", "Kepler", "Spitzer"], 0,
     "Das James-Webb-Weltraumteleskop steht am Lagrange-Punkt L2."),
    ("Wer betrat 1969 als erster Mensch den Mond?", ["Neil Armstrong", "Buzz Aldrin", "Juri Gagarin", "Michael Collins"], 0,
     "Apollo 11 landete am 20. Juli 1969."),
    ("Welches chemische Symbol hat Gold?", ["Au", "Ag", "Go", "Gd"], 0, "Au kommt vom lateinischen „aurum“."),
    ("Wie viele Knochen hat ein erwachsener Mensch ungefähr?", ["206", "106", "306", "256"], 0,
     "Babys haben mehr Knochen – einige wachsen später zusammen."),
    ("Bei wie viel Grad Celsius kocht Wasser auf Meereshöhe?", ["100", "90", "110", "120"], 0,
     "Auf hohen Bergen kocht Wasser schon früher, weil der Luftdruck geringer ist."),
    ("Welches Gas atmen Pflanzen bei der Fotosynthese ein?", ["Kohlendioxid", "Sauerstoff", "Stickstoff", "Helium"], 0,
     "Daraus machen sie mit Licht Zucker und geben Sauerstoff ab."),
    ("Wofür steht „CPU“?", ["Central Processing Unit", "Computer Power Unit", "Core Processing Utility",
                            "Central Program Uplink"], 0, "Die CPU ist das Rechenwerk des Computers."),
    ("Wie viele Bits hat ein Byte?", ["8", "4", "16", "10"], 0, "Ein Byte kann 256 verschiedene Werte darstellen."),
    ("Welche Firma entwickelt Windows?", ["Microsoft", "Apple", "Google", "IBM"], 0,
     "Windows 1.0 erschien im November 1985."),
    ("In welchem Spiel baut man mit Blöcken und kämpft nachts gegen Creeper?", ["Minecraft", "Terraria", "Roblox", "Fortnite"], 0,
     "Minecraft ist eines der meistverkauften Spiele aller Zeiten."),
    ("Wie heißt die fiktive Stadt in GTA V?", ["Los Santos", "Vice City", "Liberty City", "San Fierro"], 0,
     "Los Santos ist an Los Angeles angelehnt."),
    ("Welches Studio entwickelte Elden Ring?", ["FromSoftware", "CD Projekt Red", "Bethesda", "Capcom"], 0,
     "An der Welt von Elden Ring wirkte auch George R. R. Martin mit."),
    ("Wie heißt der Held der „Legend of Zelda“-Reihe?", ["Link", "Zelda", "Ganon", "Epona"], 0,
     "Viele denken, er heiße Zelda – das ist aber die Prinzessin."),
    ("Welche Farbe hat Pac-Mans Geist Blinky?", ["Rot", "Pink", "Orange", "Türkis"], 0,
     "Blinky jagt Pac-Man am direktesten."),
    ("Wie heißt der Klempner, der Prinzessin Peach rettet?", ["Mario", "Luigi", "Wario", "Toad"], 0,
     "Mario hieß in seinem ersten Auftritt in Donkey Kong noch „Jumpman“."),
    ("In welchem Spiel sind die „Impostor“ unter der Crew?", ["Among Us", "Fall Guys", "Phasmophobia", "Valorant"], 0,
     "Among Us erschien schon 2018, wurde aber erst 2020 riesig."),
    ("Welches Valve-Spiel war ein großer VR-Meilenstein von 2020?", ["Half-Life: Alyx", "Portal 2", "Left 4 Dead",
                                                                     "Counter-Strike 2"], 0,
     "Half-Life: Alyx spielt zwischen Half-Life 1 und 2."),
    ("Was misst man in Hertz?", ["Frequenz", "Spannung", "Leistung", "Widerstand"], 0,
     "Ein 144-Hz-Monitor zeigt bis zu 144 Bilder pro Sekunde."),
    ("Welcher Planet wird „Roter Planet“ genannt?", ["Mars", "Venus", "Jupiter", "Merkur"], 0,
     "Die rote Farbe kommt vom Eisenoxid – also Rost – im Boden."),
    ("Wie heißt der Droide, der mit C-3PO unterwegs ist?", ["R2-D2", "BB-8", "K-2SO", "IG-88"], 0,
     "R2-D2 kommt in jedem Film der Skywalker-Saga vor."),
    ("Welches Element hat die Ordnungszahl 1?", ["Wasserstoff", "Helium", "Sauerstoff", "Kohlenstoff"], 0,
     "Wasserstoff ist das häufigste Element im Universum."),
]


@dataclass
class Question:
    text: str
    answers: list[str]
    correct: int
    info: str = ""

    def shuffled(self) -> "Question":
        order = list(range(4))
        random.shuffle(order)
        return Question(self.text, [self.answers[i] for i in order], order.index(self.correct), self.info)


def parse_questions(raw: str | None, limit: int) -> list[Question]:
    if not raw:
        return []
    text = clean(raw)
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1]) if start >= 0 else {}
    except json.JSONDecodeError:
        return []
    out = []
    for q in (data.get("fragen") if isinstance(data, dict) else None) or []:
        if not isinstance(q, dict):
            continue
        answers = q.get("antworten")
        idx = q.get("richtig")
        if (isinstance(q.get("frage"), str) and isinstance(answers, list) and len(answers) == 4
                and all(isinstance(a, str) and a.strip() for a in answers) and isinstance(idx, int) and 0 <= idx < 4
                and len({a.strip().casefold() for a in answers}) == 4):
            out.append(Question(q["frage"].strip()[:250], [a.strip()[:80] for a in answers], idx,
                                str(q.get("info") or "").strip()[:200]))
    return out[:limit]


def bank_questions(n: int) -> list[Question]:
    return [Question(*q) for q in random.sample(BANK, min(n, len(BANK)))]


class QuestionView(discord.ui.View):
    def __init__(self, question: Question):
        super().__init__(timeout=SECONDS_PER_QUESTION)
        self.question = question
        self.answers: dict[int, tuple[int, float, str]] = {}  # user_id -> (index, zeit, name)
        for i, answer in enumerate(question.answers):
            button = discord.ui.Button(label=answer[:80], emoji=LETTERS[i], style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(i)
            self.add_item(button)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id in self.answers:
                await interaction.response.send_message("Du hast schon geantwortet. 😉", ephemeral=True)
                return
            self.answers[interaction.user.id] = (index, time.monotonic(), interaction.user.display_name)
            await interaction.response.send_message(f"Antwort {LETTERS[index]} gespeichert.", ephemeral=True)
        return callback

    def reveal(self) -> None:
        for i, item in enumerate(self.children):
            item.disabled = True
            item.style = discord.ButtonStyle.success if i == self.question.correct else discord.ButtonStyle.secondary


class Quiz:
    def __init__(self, data: ServerData, brain, actions):
        self.data, self.brain, self.actions = data, brain, actions
        self.running: set[int] = set()

    async def questions(self, topic: str, n: int) -> tuple[list[Question], bool]:
        raw = None
        try:
            raw = await self.brain.complete(QUIZ_SYSTEM, f"Thema: {topic}\nAnzahl Fragen: {n}", max_tokens=1800)
        except Exception:  # noqa: BLE001
            log.exception("Quizfragen konnten nicht erzeugt werden")
        qs = parse_questions(raw, n)
        if len(qs) >= max(1, n // 2):
            return qs, True
        log.info("KI-Fragen unbrauchbar (%d gültig), nehme eingebaute Fragen", len(qs))
        return bank_questions(n), False

    async def run(self, interaction: discord.Interaction, topic: str, n: int) -> None:
        channel = interaction.channel
        if channel.id in self.running:
            await interaction.response.send_message("Hier läuft schon ein Quiz.", ephemeral=True)
            return
        if self.actions and self.actions.stopped(interaction.guild):
            await interaction.response.send_message("Ich bin gerade per Notbremse gestoppt.", ephemeral=True)
            return
        self.running.add(channel.id)
        try:
            await interaction.response.send_message(
                f"🧠 **Quiz!** {n} Fragen zum Thema *{topic}*. Ihr habt je {SECONDS_PER_QUESTION} Sekunden. "
                "Ich denk mir kurz die Fragen aus …")
            qs, from_ai = await self.questions(topic, n)
            if not from_ai and topic != DEFAULT_TOPIC:
                await channel.send("Mir sind zu dem Thema gerade keine guten Fragen eingefallen, "
                                   "ich nehm welche aus meiner Sammlung.")
            totals: dict[int, list] = {}  # user_id -> [punkte, richtig, name]
            for number, q in enumerate(qs, 1):
                q = q.shuffled()
                embed = discord.Embed(title=f"Frage {number}/{len(qs)}", description=f"**{q.text}**", color=0x5865F2)
                embed.set_footer(text=f"{SECONDS_PER_QUESTION} Sekunden – Knopf drücken!")
                view = QuestionView(q)
                msg = await channel.send(embed=embed, view=view)
                await view.wait()
                view.reveal()
                right = sorted((t, uid, name) for uid, (idx, t, name) in view.answers.items() if idx == q.correct)
                for uid, (idx, _t, name) in view.answers.items():
                    ok = idx == q.correct
                    pts = (10 + (5 if right and right[0][1] == uid else 0)) if ok else 0
                    entry = totals.setdefault(uid, [0, 0, name])
                    entry[0] += pts
                    entry[1] += int(ok)
                    self.data.add_quiz_result(interaction.guild_id, uid, name, pts, ok)
                result = f"✅ Richtig: **{LETTERS[q.correct]} {q.answers[q.correct]}**"
                if right:
                    result += f"\n⚡ Am schnellsten: **{right[0][2]}** (+5 Bonus)"
                    others = [n for _t, _u, n in right[1:]]
                    if others:
                        result += f"\nAuch richtig: {', '.join(others)}"
                elif view.answers:
                    result += "\nDiesmal lag niemand richtig."
                else:
                    result += "\nKeiner hat geantwortet. 🦗"
                if q.info:
                    result += f"\n💡 {q.info}"
                embed.description = f"**{q.text}**\n\n{result}"
                embed.set_footer(text="Zeit um!")
                await msg.edit(embed=embed, view=view)
                await asyncio.sleep(4)
            if totals:
                ranking = sorted(totals.values(), key=lambda e: (-e[0], -e[1]))
                medals = ["🥇", "🥈", "🥉"]
                lines = [f"{medals[i] if i < 3 else '•'} **{name}** – {pts} Punkte ({ok}/{len(qs)} richtig)"
                         for i, (pts, ok, name) in enumerate(ranking[:10])]
                end = discord.Embed(title="🏁 Quiz vorbei!", description="\n".join(lines), color=0xFEE75C)
                end.set_footer(text="Gesamtrangliste: /quiz_rangliste")
                await channel.send(embed=end)
            else:
                await channel.send("Quiz vorbei – keiner hat mitgemacht. Nächstes Mal! 😄")
        finally:
            self.running.discard(channel.id)


def register_quiz(tree: app_commands.CommandTree, quiz: Quiz) -> None:
    @tree.command(name="quiz", description="Startet ein Quiz mit Knöpfen und Punkten")
    @app_commands.describe(thema="z.B. 'Star Wars', 'Weltraum', 'GTA' (leer = gemischt)",
                           fragen="Wie viele Fragen (1-10, Standard 5)")
    @app_commands.guild_only()
    async def quiz_cmd(interaction: discord.Interaction, thema: str = "",
                       fragen: app_commands.Range[int, 1, 10] = 5):
        await quiz.run(interaction, (thema.strip() or DEFAULT_TOPIC)[:100], fragen)

    @tree.command(name="quiz_rangliste", description="Die besten Quiz-Spieler auf dem Server")
    @app_commands.guild_only()
    async def rangliste(interaction: discord.Interaction):
        top = quiz.data.quiz_top(interaction.guild_id)
        if not top:
            await interaction.response.send_message("Noch keine Quiz-Punkte. Starte eins mit /quiz!", ephemeral=True)
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'{i + 1}.'} **{name}** – {pts} Punkte · {ok}/{answered} richtig"
                 for i, (name, pts, ok, answered) in enumerate(top)]
        await interaction.response.send_message(embed=discord.Embed(title="🧠 Quiz-Rangliste",
                                                                    description="\n".join(lines), color=0xFEE75C),
                                                allowed_mentions=discord.AllowedMentions.none())
