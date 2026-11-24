"""Alternative Anbindung über eine OpenAI-kompatible API (z.B. Requesty, Groq, OpenRouter).

Gleiche Schnittstelle wie Brain: reply(history, server=..., channel=...) -> str | None.
Mehrere Modelle möglich: zickt eins (Limit, Fehler, leere Antwort), kommt das nächste dran.
"""

from __future__ import annotations

import asyncio
import base64
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


def with_images(messages: list[dict], images: list[tuple[str, bytes]]) -> list[dict]:
    """Hängt Bilder (MIME-Typ, Bytes) an die letzte Nachricht an – im OpenAI-Format, das auch Gemini versteht."""
    last = messages[-1]
    parts = [{"type": "text", "text": last["content"]}]
    for mime, data in images:
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}})
    return [*messages[:-1], {**last, "content": parts}]


def request_extras(provider: "Provider", model: str) -> dict:
    """Gemini-Flash (nicht Lite) denkt sonst lange nach – für Chat reicht wenig Nachdenken."""
    if "googleapis" in provider.base_url and "flash" in model and "lite" not in model:
        return {"reasoning_effort": "low"}
    return {}


@dataclass
class Provider:
    """Ein OpenAI-kompatibler Anbieter (Requesty, Groq, Gemini, OpenRouter, lokales Ollama …)."""
    name: str
    base_url: str
    api_key: str
    models: list[str]
    client: object = None
    timeout: float = 25


COOLDOWN_LIMIT = 10 * 60   # nach "Limit erreicht" so lange überspringen
COOLDOWN_ERROR = 2 * 60    # nach Verbindungsfehler so lange überspringen
COOLDOWN_BUSY = 5 * 60     # Modell überlastet (503) oder Zeitlimit -> so lange auslassen
TEMPERATURE = 0.7          # etwas weniger Zufall: weniger wilde Nonsens-Witze
HEDGE_AFTER = 5.0          # so lange auf eine Antwort warten, bevor parallel ein zweiter Kandidat gefragt wird


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
        self.model_cooldown: dict[tuple[str, str], float] = {}
        self.hedge_after = HEDGE_AFTER
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
                    context: str | None = None, images: list[tuple[str, bytes]] | None = None) -> str | None:
        if not history:
            return None
        messages = [{"role": "system", "content": self._system(server, channel, context)}, *history]
        if images:
            answer = await self._ask(with_images(messages, images), self.max_tokens)
            if answer not in (FEHLER_ANTWORT, LIMIT_ANTWORT):
                return answer
            log.warning("Bild konnte nicht angeschaut werden, antworte ohne Bild")
            messages[-1] = {**messages[-1], "content": messages[-1]["content"]
                            + "\n(Hinweis: Das Bild dazu konntest du gerade nicht ansehen. Sag das kurz.)"}
        return await self._ask(messages, self.max_tokens)

    def _next(self, tried: set, skipped: set, busy_providers: set) -> tuple[Provider, str] | None:
        """Nächster Kandidat (Anbieter, Modell) in der eingestellten Reihenfolge."""
        now = self._clock()
        options = [(p, m) for p in self.providers for m in p.models
                   if (p.name, m) not in tried and p.name not in skipped
                   and self.cooldown.get(p.name, 0) <= now and self.model_cooldown.get((p.name, m), 0) <= now]
        if not options:
            return None
        return options[0]  # einfach der Reihe nach: die schnellen Modelle stehen vorne

    async def _try(self, provider: Provider, model: str, messages: list[dict], max_tokens: int) -> tuple[str, str]:
        started = time.monotonic()
        try:
            response = await provider.client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=TEMPERATURE,
                **request_extras(provider, model),
            )
        except openai.RateLimitError:
            log.warning("%s: Limit erreicht bei %s", provider.name, model)
            if model == provider.models[-1]:
                self.cooldown[provider.name] = self._clock() + COOLDOWN_LIMIT
                log.info("%s pausiert %d Min., nehme den nächsten Anbieter", provider.name, COOLDOWN_LIMIT // 60)
            return "limit", ""
        except openai.APIStatusError as e:
            log.error("%s: API-Fehler %s bei %s: %s", provider.name, e.status_code, model, str(e.message)[:200])
            if e.status_code in (401, 402, 403):  # Key falsch/Guthaben leer -> Anbieter länger auslassen
                self.cooldown[provider.name] = self._clock() + COOLDOWN_LIMIT
                return "skip", ""
            if e.status_code >= 500:  # überlastet -> dieses Modell ein paar Minuten auslassen
                self.model_cooldown[(provider.name, model)] = self._clock() + COOLDOWN_BUSY
            return "error", ""
        except openai.APITimeoutError:
            log.error("%s: %s antwortet nicht (Zeitlimit)", provider.name, model)
            self.model_cooldown[(provider.name, model)] = self._clock() + COOLDOWN_BUSY
            return "error", ""
        except openai.APIConnectionError:
            log.error("%s: keine Verbindung (%s)", provider.name, model)
            self.cooldown[provider.name] = self._clock() + COOLDOWN_ERROR
            return "skip", ""
        choice = response.choices[0] if response.choices else None
        text = clean(choice.message.content or "") if choice else ""
        if not text:
            log.warning("%s: leere Antwort von %s", provider.name, model)
            return "error", ""
        if choice.finish_reason == "length":
            text += " …"
        self.used[provider.name] = self.used.get(provider.name, 0) + 1
        log.info("Antwort von %s/%s in %.1f s", provider.name, model, time.monotonic() - started)
        if provider is not self.providers[0]:
            log.info("Antwort kam von Reserve %s (%s)", provider.name, model)
        return "ok", text

    async def _ask(self, messages: list[dict], max_tokens: int) -> str:
        """Fragt die Kandidaten der Reihe nach. Hängt einer länger als HEDGE_AFTER, startet parallel der nächste
        (möglichst bei einem anderen Anbieter) – die erste brauchbare Antwort gewinnt."""
        now = self._clock()
        rate_limited = any(self.cooldown.get(p.name, 0) > now for p in self.providers)
        tried: set = set()
        skipped: set = set()
        pending: dict[asyncio.Task, Provider] = {}

        def launch() -> bool:
            cand = self._next(tried, skipped, {p.name for p in pending.values()})
            if cand is None:
                return False
            provider, model = cand
            tried.add((provider.name, model))
            pending[asyncio.create_task(self._try(provider, model, messages, max_tokens))] = provider
            return True

        try:
            launch()
            while pending:
                done, _ = await asyncio.wait(pending, timeout=self.hedge_after, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if launch():
                        log.info("Antwort dauert, frage parallel noch einen anderen")
                    continue
                for task in done:
                    provider = pending.pop(task)
                    status, text = task.result()
                    if status == "ok":
                        return text
                    if status == "limit":
                        rate_limited = True
                    if status == "skip":
                        skipped.add(provider.name)
                if not pending:
                    launch()
        finally:
            for task in pending:
                task.cancel()
        return LIMIT_ANTWORT if rate_limited else FEHLER_ANTWORT
