"""Alternative Anbindung über eine OpenAI-kompatible API (z.B. Requesty, Groq, OpenRouter).

Gleiche Schnittstelle wie Brain: reply(history, server=..., channel=...) -> str | None.
Mehrere Modelle möglich: zickt eins (Limit, Fehler, leere Antwort), kommt das nächste dran.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import openai

from .brain import FEHLER_ANTWORT, LIMIT_ANTWORT, WOCHENTAGE

log = logging.getLogger("dave.brain_openai")

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_DAVE_PREFIX = re.compile(r"^\s*\**Dave\**\s*:\s*\**\s*", re.IGNORECASE)


def clean(text: str) -> str:
    """Entfernt Denk-Blöcke mancher Modelle und ein vorangestelltes "Dave:"."""
    text = _THINK.sub("", text)
    if "</think>" in text.lower():  # nur schließendes Tag geliefert
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    return _DAVE_PREFIX.sub("", text.strip()).strip()


class OpenAIBrain:
    def __init__(self, personality: str, *, models: list[str], max_tokens: int,
                 base_url: str, api_key: str,
                 client: openai.AsyncOpenAI | None = None, timezone: str = "Europe/Berlin"):
        if not models:
            raise ValueError("Mindestens ein Modell nötig (DAVE_MODEL).")
        self.personality = personality
        self.models = models
        self.max_tokens = max_tokens
        self.client = client or openai.AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=60, max_retries=1)
        self.tz = ZoneInfo(timezone)

    def _system(self, server: str, channel: str, context: str | None = None) -> str:
        jetzt = datetime.now(self.tz)
        now = f"{WOCHENTAGE[jetzt.weekday()]}, {jetzt:%d.%m.%Y %H:%M}"
        text = f"{self.personality}\n\nServer: {server}\nKanal: #{channel}\nJetzt: {now}"
        return f"{text}\n\n{context}" if context else text

    async def complete(self, system: str, user: str, *, max_tokens: int = 700) -> str | None:
        """Einfacher Einzelaufruf ohne Persönlichkeit (z.B. fürs Lernen). None bei Fehler."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        answer = await self._ask(messages, max_tokens)
        return None if answer in (FEHLER_ANTWORT, LIMIT_ANTWORT) else answer

    async def reply(self, history: list[dict], *, server: str, channel: str,
                    context: str | None = None) -> str | None:
        if not history:
            return None
        messages = [{"role": "system", "content": self._system(server, channel, context)}, *history]
        return await self._ask(messages, self.max_tokens)

    async def _ask(self, messages: list[dict], max_tokens: int) -> str:
        rate_limited = False
        for model in self.models:
            try:
                response = await self.client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens,
                )
            except openai.RateLimitError:
                log.warning("Limit erreicht bei %s, versuche nächstes Modell", model)
                rate_limited = True
                continue
            except openai.APIStatusError as e:
                log.error("API-Fehler %s bei %s: %s", e.status_code, model, e.message)
                continue
            except openai.APIConnectionError:
                log.error("Keine Verbindung zur API (%s)", model)
                continue

            choice = response.choices[0] if response.choices else None
            text = clean(choice.message.content or "") if choice else ""
            if not text:
                log.warning("Leere Antwort von %s, versuche nächstes Modell", model)
                continue
            if choice.finish_reason == "length":
                text += " …"
            return text
        return LIMIT_ANTWORT if rate_limited else FEHLER_ANTWORT
