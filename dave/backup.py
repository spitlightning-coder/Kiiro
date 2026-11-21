"""Sicherungen von Daves Gedächtnis: einmal am Tag ein ZIP von data/ (ohne Modell-Ordner), die letzten 7 bleiben.

SQLite-Dateien werden über die Backup-Funktion von SQLite kopiert, damit die Kopie auch dann heil ist,
wenn Dave gerade schreibt. Mit /dave backup bekommt ein Admin die aktuelle Sicherung als Datei.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("dave.backup")

KEEP = 7
EVERY = 24 * 3600
EXCLUDE = {"models", "backups"}      # Modell lädt sich neu, Sicherungen nicht in Sicherungen packen
SKIP_SUFFIXES = ("-wal", "-shm", "-journal")
PREFIX = "dave-backup-"
TZ = ZoneInfo("Europe/Berlin")


def backup_dir(data_dir: Path | str) -> Path:
    return Path(data_dir) / "backups"


def list_backups(data_dir: Path | str) -> list[Path]:
    """Alle Sicherungen, älteste zuerst."""
    folder = backup_dir(data_dir)
    return sorted(folder.glob(f"{PREFIX}*.zip")) if folder.exists() else []


def make_backup(data_dir: Path | str, *, keep: int = KEEP, now: datetime | None = None) -> Path:
    data_dir = Path(data_dir)
    dest = backup_dir(data_dir)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(TZ)).strftime("%Y-%m-%d_%H%M%S")
    target = dest / f"{PREFIX}{stamp}.zip"
    partial = dest / f"{PREFIX}{stamp}.part"
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(data_dir.rglob("*")):
            rel = f.relative_to(data_dir)
            if rel.parts[0] in EXCLUDE or not f.is_file() or f.name.endswith(SKIP_SUFFIXES):
                continue
            if f.suffix == ".sqlite3":
                copy = Path(tmp) / rel.as_posix().replace("/", "__")
                src, dst = sqlite3.connect(f), sqlite3.connect(copy)
                try:
                    src.backup(dst)
                finally:
                    src.close()
                    dst.close()
                zf.write(copy, rel.as_posix())
            else:
                zf.write(f, rel.as_posix())
    partial.replace(target)
    for old in list_backups(data_dir)[:-keep]:
        old.unlink(missing_ok=True)
    return target


def newest_age(data_dir: Path | str, clock=time.time) -> float | None:
    backups = list_backups(data_dir)
    return clock() - backups[-1].stat().st_mtime if backups else None


class Backups:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def make(self) -> Path:
        path = await asyncio.to_thread(make_backup, self.data_dir)
        log.info("Gedächtnis gesichert: %s (%d KB)", path.name, path.stat().st_size // 1024)
        return path

    async def _loop(self) -> None:
        await asyncio.sleep(120)  # erst mal in Ruhe hochfahren
        while True:
            try:
                age = newest_age(self.data_dir)
                if age is None or age >= EVERY - 600:
                    await self.make()
            except Exception:  # noqa: BLE001 – Sicherung darf Dave nie lahmlegen
                log.exception("Sicherung fehlgeschlagen")
            await asyncio.sleep(3600)


UPLOAD_LIMIT = 9_500_000  # Discord nimmt von Bots bis ~10 MB


def register_backup(tree, backups: Backups) -> None:
    import discord

    admin = next((c for c in tree.get_commands() if c.name == "dave"), None)
    if admin is None:
        return

    @admin.command(name="backup", description="Sichert Daves Gedächtnis jetzt und schickt dir die Datei (nur du siehst sie)")
    async def backup(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Das dürfen nur Admins.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            path = await backups.make()
        except Exception:  # noqa: BLE001
            log.exception("Sicherung per Befehl fehlgeschlagen")
            await interaction.followup.send("Sicherung ist schiefgegangen, Details stehen im Log.", ephemeral=True)
            return
        count = len(list_backups(backups.data_dir))
        text = (f"💾 Gesichert: `{path.name}` ({path.stat().st_size // 1024} KB). "
                f"Auf dem Server liegen jetzt {count} Sicherungen (eine pro Tag, die letzten {KEEP}).")
        if path.stat().st_size <= UPLOAD_LIMIT:
            await interaction.followup.send(text + "\nHeb die Datei gut auf, da steht drin, was ich über euch weiß.",
                                            file=discord.File(path), ephemeral=True)
        else:
            await interaction.followup.send(text + "\nZu groß für Discord – hol sie mit zap_backup_holen.bat.",
                                            ephemeral=True)
