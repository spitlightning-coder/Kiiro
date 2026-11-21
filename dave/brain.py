"""Die Claude-Anbindung: aus Kanal-Verlauf + Persönlichkeit eine Antwort machen."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

log = logging.getLogger("dave.brain")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Modelle, die serverseitige Fallbacks bei Ablehnungen unterstützen.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}

FEHLER_ANTWORT = "Mein Hirn hängt gerade kurz. Versuch's gleich nochmal."
LIMIT_ANTWORT = "Ich werde gerade mit Nachrichten überrannt. Gib mir eine Minute."
ABLEHNUNG_ANTWORT = "Dazu sag ich lieber nichts."

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


class Brain:
    def __init__(self, personality: str, *, model: str, effort: str, max_tokens: int,
                 client: anthropic.AsyncAnthropic | None = None, timezone: str = "Europe/Berlin"):
        self.personality = personality
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.client = client or anthropic.AsyncAnthropic()
        self.tz = ZoneInfo(timezone)

    def _system(self, server: str, channel: str) -> list[dict]:
        jetzt = datetime.now(self.tz)
        now = f"{WOCHENTAGE[jetzt.weekday()]}, {jetzt:%d.%m.%Y %H:%M}"
        return [
            # Fester Teil zuerst, damit er gecacht werden kann.
            {"type": "text", "text": self.personality, "cache_control": {"type": "ephemeral"}},
            # Wechselnder Teil danach.
            {"type": "text", "text": f"Server: {server}\nKanal: #{channel}\nJetzt: {now}"},
        ]

    async def reply(self, history: list[dict], *, server: str, channel: str) -> str | None:
        if not history:
            return None
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if self.model in FALLBACK_MODELS else {}
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self._system(server, channel),
                messages=history,
                output_config={"effort": self.effort},
                **extra,
            )
        except anthropic.RateLimitError:
            log.warning("Rate limit von der Claude API")
            return LIMIT_ANTWORT
        except anthropic.APIStatusError as e:
            log.error("Claude API Fehler %s: %s", e.status_code, e.message)
            return FEHLER_ANTWORT
        except anthropic.APIConnectionError:
            log.error("Keine Verbindung zur Claude API")
            return FEHLER_ANTWORT

        if response.stop_reason == "refusal":
            return ABLEHNUNG_ANTWORT

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "max_tokens" and text:
            text += " …"
        return text or None
