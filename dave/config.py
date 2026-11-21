"""Konfiguration aus Umgebungsvariablen (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "persoenlichkeit.md"


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "ja", "on"}


def _ids(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    return frozenset(int(p) for p in raw.replace(" ", "").split(",") if p)


@dataclass(frozen=True)
class Config:
    discord_token: str
    model: str = "claude-opus-5-5"
    effort: str = "low"
    max_tokens: int = 1500
    memory_messages: int = 20
    memory_minutes: int = 60
    dave_channel_ids: frozenset[int] = field(default_factory=frozenset)
    reply_to_bots: bool = False
    provider: str = "anthropic"          # anthropic | openai (OpenAI-kompatibel, z.B. Requesty)
    api_base: str = "https://router.requesty.ai/v1"
    api_key: str = ""
    longterm: bool = True                # Langzeitgedächtnis (SQLite + ChromaDB)
    data_dir: Path = ROOT / "data"
    learn_every: int = 15                # nach so vielen neuen Nachrichten pro Kanal lernt Dave

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(ROOT / ".env")
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise SystemExit("DISCORD_TOKEN fehlt. Trag ihn in die .env ein (siehe .env.example).")
        provider = (os.getenv("DAVE_PROVIDER", "").strip().lower() or cls.provider)
        if provider not in {"anthropic", "openai"}:
            raise SystemExit("DAVE_PROVIDER muss 'anthropic' oder 'openai' sein.")
        api_key = os.getenv("DAVE_API_KEY", "").strip()
        if provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY", "").strip():
            raise SystemExit("ANTHROPIC_API_KEY fehlt. Trag ihn in die .env ein (siehe .env.example).")
        if provider == "openai" and not api_key:
            raise SystemExit("DAVE_API_KEY fehlt (für DAVE_PROVIDER=openai). Trag ihn in die .env ein.")
        return cls(
            provider=provider,
            api_base=os.getenv("DAVE_API_BASE", "").strip() or cls.api_base,
            api_key=api_key,
            discord_token=token,
            model=os.getenv("DAVE_MODEL", "").strip() or cls.model,
            effort=os.getenv("DAVE_EFFORT", "").strip() or cls.effort,
            max_tokens=_int("DAVE_MAX_TOKENS", cls.max_tokens),
            memory_messages=_int("DAVE_MEMORY_MESSAGES", cls.memory_messages),
            memory_minutes=_int("DAVE_MEMORY_MINUTES", cls.memory_minutes),
            dave_channel_ids=_ids("DAVE_CHANNEL_IDS"),
            reply_to_bots=_bool("DAVE_REPLY_TO_BOTS", False),
            longterm=_bool("DAVE_LONGTERM", True),
            data_dir=Path(os.getenv("DAVE_DATA_DIR", "").strip() or ROOT / "data"),
            learn_every=max(5, _int("DAVE_LEARN_EVERY", cls.learn_every)),
        )


def load_personality() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()
