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

## Persönlichkeit

Steht in [`prompts/persoenlichkeit.md`](prompts/persoenlichkeit.md). Einfach bearbeiten und Dave neu starten.

## Wie es funktioniert

- `dave/bot.py` – Discord-Anbindung: entscheidet, ob Dave angesprochen ist, zeigt "schreibt…", teilt lange Antworten auf.
- `dave/memory.py` – Kurzzeitgedächtnis pro Kanal (nur im RAM, weg nach Neustart).
- `dave/brain.py` – Claude-Aufruf mit Persönlichkeit und Kanal-Verlauf.
- `dave/config.py` – liest die `.env`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
