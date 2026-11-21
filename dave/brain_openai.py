"""Alternative Anbindung über eine OpenAI-kompatible API (z.B. Requesty, Groq, OpenRouter).

Gleiche Schnittstelle wie Brain: reply(history, server=..., channel=...) -> str | None.
Mehrere Modelle möglich: zickt eins (Limit, Fehler, leere Antwort), kommt das nächste dran.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
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


@dataclass
class Provider:
    """Ein OpenAI-kompatibler Anbieter (Requesty, Groq, Gemini, OpenRouter, lokales Ollama …)."""
    name: str
    base_url: str
    api_key: str
    models: list[str]
    client: object = None
    timeout: float = 60


COOLDOWN_LIMIT = 10 * 60   # nach "Limit erreicht" so lange überspringen
COOLDOWN_ERROR = 2 * 60    # nach Verbindungsfehler so lange überspringen


class OpenAIBrain:
    def __init__(self, personality: str, *, models: list[str], max_tokens: int,
                 base_url: str, api_key: str,
                 client: openai.AsyncOpenAI | None = None, timezone: str = "Europe/Berlin",
                 fallbacks: list[Provider] | None = None, clock=time.monotonic, name: str = "haupt"):
        if not models:
            raise ValueError("Mindestens ein Modell nötig (DAVE_MODEL).")
        self.personality = personality
        self.models = models
        self.max_tokens = max_tokens
        self.tz = ZoneInfo(timezone)
        self._clock = clock
        self.providers = [Provider(name, base_url, api_key, models, client)] + list(fallbacks or [])
        for p in self.providers:
            if p.client is None:
                p.client = openai.AsyncOpenAI(base_url=p.base_url, api_key=p.api_key or "none",
                                              timeout=p.timeout, max_retries=0)
        self.cooldown: dict[str, float] = {}
        self.used: dict[str, int] = {p.name: 0 for p in self.providers}

    @property
    def client(self):
        return self.providers[0].client

    def status(self) -> str:
        now = self._clock()
        parts = []
        for p in self.providers:
            wait = self.cooldown.get(p.name, 0) - now
            state = f"Pause {int(wait // 60) + 1} Min." if wait > 0 else "bereit"
            parts.append(f"{p.name} ({state}, {self.used.get(p.name, 0)}× seit Start)")
        return " → ".join(parts)

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
        for provider in self.providers:
            if self.cooldown.get(provider.name, 0) > self._clock():
                rate_limited = True
                continue
            for model in provider.models:
                try:
                    response = await provider.client.chat.completions.create(
                        model=model, messages=messages, max_tokens=max_tokens,
                    )
                except openai.RateLimitError:
                    log.warning("%s: Limit erreicht bei %s", provider.name, model)
                    rate_limited = True
                    if model == provider.models[-1]:
                        self.cooldown[provider.name] = self._clock() + COOLDOWN_LIMIT
                        log.info("%s pausiert %d Min., nehme den nächsten Anbieter", provider.name, COOLDOWN_LIMIT // 60)
                    continue
                except openai.APIStatusError as e:
                    log.error("%s: API-Fehler %s bei %s: %s", provider.name, e.status_code, model, e.message)
                    if e.status_code in (401, 402, 403):  # Key falsch/Guthaben leer -> Anbieter länger auslassen
                        self.cooldown[provider.name] = self._clock() + COOLDOWN_LIMIT
                        break
                    continue
                except (openai.APIConnectionError, openai.APITimeoutError):
                    log.error("%s: keine Verbindung (%s)", provider.name, model)
                    self.cooldown[provider.name] = self._clock() + COOLDOWN_ERROR
                    break

                choice = response.choices[0] if response.choices else None
                text = clean(choice.message.content or "") if choice else ""
                if not text:
                    log.warning("%s: leere Antwort von %s", provider.name, model)
                    continue
                if choice.finish_reason == "length":
                    text += " …"
                self.used[provider.name] = self.used.get(provider.name, 0) + 1
                if provider is not self.providers[0]:
                    log.info("Antwort kam von Reserve %s (%s)", provider.name, model)
                return text
        return LIMIT_ANTWORT if rate_limited else FEHLER_ANTWORT
