"""Holt Daves Gedächtnis-Sicherungen vom ZAP-Server auf den PC (Ordner backups/). Aufruf über zap_backup_holen.bat."""

import json
import posixpath
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / "server" / "zap_sftp.json").read_text(encoding="utf-8"))


def main() -> None:
    local = ROOT / "backups"
    local.mkdir(exist_ok=True)
    transport = paramiko.Transport((cfg["host"], cfg["port"]))
    transport.connect(username=cfg["user"], password=cfg["password"])
    sftp = paramiko.SFTPClient.from_transport(transport)
    remote = posixpath.join(sftp.normalize("."), "data", "backups")
    try:
        names = sorted(n for n in sftp.listdir(remote) if n.startswith("dave-backup-") and n.endswith(".zip"))
    except FileNotFoundError:
        names = []
    new = 0
    for name in names:
        if not (local / name).exists():
            sftp.get(posixpath.join(remote, name), str(local / name))
            new += 1
    sftp.close()
    transport.close()
    print(f"  {len(names)} Sicherungen auf dem Server, {new} neu geholt -> Ordner backups")


if __name__ == "__main__":
    main()
