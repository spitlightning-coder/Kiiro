"""Kurzes Gedächtnis pro Kanal: die letzten N Nachrichten, die nach einer Weile verfallen.

Bewusst nur im Arbeitsspeicher. Nach einem Neustart weiß Dave nichts mehr.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class Entry:
    from_dave: bool
    author: str
    text: str
    at: float


class ChannelMemory:
    def __init__(self, max_messages: int = 20, max_age_seconds: float = 3600, clock=time.monotonic):
        self.max_messages = max_messages
        self.max_age = max_age_seconds
        self._clock = clock
        self._channels: dict[int, deque[Entry]] = {}

    def add(self, channel_id: int, author: str, text: str, *, from_dave: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        buf = self._channels.setdefault(channel_id, deque(maxlen=self.max_messages))
        buf.append(Entry(from_dave, author, text, self._clock()))

    def forget(self, channel_id: int) -> None:
        self._channels.pop(channel_id, None)

    def recent(self, channel_id: int) -> list[Entry]:
        buf = self._channels.get(channel_id)
        if not buf:
            return []
        cutoff = self._clock() - self.max_age
        while buf and buf[0].at < cutoff:
            buf.popleft()
        return list(buf)

    def as_messages(self, channel_id: int) -> list[dict]:
        """Baut eine gültige Messages-API-Historie: beginnt mit user, Rollen abwechselnd.

        Nachrichten anderer werden als "Name: Text" in user-Turns gebündelt,
        Daves eigene Nachrichten werden assistant-Turns.
        """
        messages: list[dict] = []
        for e in self.recent(channel_id):
            role = "assistant" if e.from_dave else "user"
            text = e.text if e.from_dave else f"{e.author}: {e.text}"
            if not messages and role == "assistant":
                continue  # Historie muss mit user beginnen
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n" + text
            else:
                messages.append({"role": role, "content": text})
        # Die letzte Nachricht muss von einem Menschen sein, sonst gibt es nichts zu beantworten.
        while messages and messages[-1]["role"] == "assistant":
            messages.pop()
        return messages
