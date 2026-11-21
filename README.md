# Dave

Ein KI-Bot für Discord, angetrieben von Claude. Dave antwortet, wenn man ihn erwähnt (`@Dave`), wenn man auf eine seiner Nachrichten antwortet, und auf jede Nachricht in eigens dafür festgelegten Kanälen. Er merkt sich die letzten Nachrichten pro Kanal, damit er dem Gespräch folgen kann.

## Einrichtung

1. **Discord-Bot anlegen** im [Developer Portal](https://discord.com/developers/applications): New Application → Bot → Token kopieren.
   Unter *Bot → Privileged Gateway Intents* **Message Content Intent** einschalten, sonst sieht Dave keine Nachrichtentexte.
2. **Bot einladen**: OAuth2 → URL Generator → Scope `bot`, Rechte *View Channels*, *Send Messages*, *Read Message History*. Den Link öffnen und Server wählen.
3. **Anthropic API-Key** holen: [console.anthropic.com](https://console.anthropic.com) → API Keys.
4. **Installieren und starten** (Python 3.10+):

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env             # dann Tokens eintragen
   python -m dave
   ```

## Einstellungen (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `DISCORD_TOKEN` | – | Bot-Token (Pflicht) |
| `ANTHROPIC_API_KEY` | – | Claude API-Key (Pflicht) |
| `DAVE_CHANNEL_IDS` | leer | Kanäle, in denen Dave auf alles antwortet, kommagetrennt |
| `DAVE_MODEL` | `claude-opus-5-5` | Claude-Modell. `claude-haiku-5-5` ist deutlich billiger |
| `DAVE_EFFORT` | `low` | Wie gründlich er nachdenkt: `low`, `medium`, `high` |
| `DAVE_MEMORY_MESSAGES` | `20` | Letzte Nachrichten pro Kanal im Gedächtnis |
| `DAVE_MEMORY_MINUTES` | `60` | Danach vergisst er den Verlauf |
| `DAVE_MAX_TOKENS` | `1500` | Maximale Antwortlänge |
| `DAVE_REPLY_TO_BOTS` | `false` | Auf andere Bots reagieren |

### Ohne Claude-Guthaben: OpenAI-kompatible Anbieter (z.B. Requesty)

```
DAVE_PROVIDER=openai
DAVE_API_BASE=https://router.requesty.ai/v1   # Standard, kann weg
DAVE_API_KEY=<dein-requesty-key>
DAVE_MODEL=google/gemma-4-31b-it,nvidia/nemotron-3-super-120b-a12b
```

Mehrere Modelle kommagetrennt: Ist eins am Limit oder liefert Murks, springt das nächste ein. `DAVE_EFFORT` gilt hier nicht.

## Langzeitgedächtnis

Dave merkt sich Dinge über Wochen, gespeichert in `data/` (bleibt auch nach einem Neustart erhalten):

- **SQLite** (`data/dave.sqlite3`): feste Fakten über Leute und den Server, dazu Daves eigene Entwicklung (Meinungen, Insider-Witze, Spitznamen).
- **ChromaDB** (`data/chroma/`): kurze Zusammenfassungen von Gesprächen, die er nach Bedeutung wiederfindet. Das Embedding-Modell läuft lokal (~80 MB, wird beim ersten Start geladen).
- **Lernen:** Nach `DAVE_LEARN_EVERY` (Standard 15) neuen Nachrichten in einem Kanal, an dem Dave beteiligt war, zieht ein Modell im Hintergrund Merkenswertes heraus (1 API-Anfrage). Heikles wie Gesundheit, Politik oder Adressen wird bewusst nicht gespeichert.
- **Von Hand:** „Dave, merk dir: …“ im Chat oder `/merken`.
- **Kontrolle:** `/was_weisst_du` zeigt dir, was er über dich weiß, `/vergiss_mich` löscht alles über dich.

Abschalten mit `DAVE_LONGTERM=false`.

## Server-Aktionen (Phase 1)

Der Code entscheidet, nicht das Sprachmodell. Jede Aktion läuft durch eine Prüfkette (Notbremse, Freigabe, Rechte, Rollen-Hierarchie, Limits) und wird in `#dave-log` (falls vorhanden) und in `logs/dave.log` protokolliert.

- **Erinnerungen:** „Dave, erinner mich in 10 Minuten an den Kuchen“ oder `/erinnerung`. Versteht „in 2 Stunden“, „um 18:30“, „morgen um 9“, „am 24.12. um 18 Uhr“. `/erinnerungen` zeigt offene, `/erinnerung_loeschen` löscht. Maximal 15 offene pro Person.
- **Selbstwahl-Rollen:** Admins geben Rollen mit `/dave rolle_freigeben` frei, alle anderen wählen per `/rollen`. Rollen mit Admin- oder Moderationsrechten vergibt Dave nie. Dave braucht dafür „Rollen verwalten“, und seine Rolle muss über den vergebenen Rollen stehen.
- **Begrüßung:** `/dave begruessung an:True kanal:#…` – Dave begrüßt neue Mitglieder kurz und persönlich.
- **Notbremse:** `/dave stopp` hält Dave sofort komplett an, `/dave weiter` löst sie. `/dave status` zeigt alle Einstellungen.

## Autostart (Windows)

- `autostart_an.bat` – Dave startet ab jetzt beim Windows-Login unsichtbar im Hintergrund (und sofort). Stürzt er ab, startet er nach 30 s neu.
- `dave_stoppen.bat` – beendet ihn. `autostart_aus.bat` – nimmt ihn wieder aus dem Autostart.
- Dave läuft nie doppelt: Eine zweite Instanz beendet sich sofort von selbst.

## Persönlichkeit

Steht in [`prompts/persoenlichkeit.md`](prompts/persoenlichkeit.md). Einfach bearbeiten und Dave neu starten.

## Wie es funktioniert

- `dave/bot.py` – Discord-Anbindung: entscheidet, ob Dave angesprochen ist, zeigt "schreibt…", teilt lange Antworten auf.
- `dave/memory.py` – Kurzzeitgedächtnis pro Kanal (nur im RAM, weg nach Neustart).
- `dave/brain.py` – Claude-Aufruf mit Persönlichkeit und Kanal-Verlauf.
- `dave/brain_openai.py` – dasselbe für OpenAI-kompatible Anbieter wie Requesty.
- `dave/longterm.py` – Langzeitgedächtnis (SQLite + ChromaDB).
- `dave/learning.py` – Lernen aus Gesprächen und Gedächtnis-Teil für den Prompt.
- `dave/actions.py` – Server-Aktionen, Prüfkette, Log, Notbremse.
- `dave/serverdata.py` – Einstellungen und Erinnerungen (SQLite).
- `dave/zeit.py` – versteht deutsche Zeitangaben.
- `dave/config.py` – liest die `.env`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
