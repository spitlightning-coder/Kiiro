"""Lädt .env und das Gedächtnis (data/) per SFTP auf den ZAP-Hosting-Server. Aufruf über zap_umzug.bat."""

import json
import posixpath
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / "server" / "zap_sftp.json").read_text(encoding="utf-8"))


def mkdirs(sftp, path):
    parts = [p for p in path.split("/") if p]
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def main(with_data: bool) -> None:
    transport = paramiko.Transport((cfg["host"], cfg["port"]))
    transport.connect(username=cfg["user"], password=cfg["password"])
    sftp = paramiko.SFTPClient.from_transport(transport)
    base = sftp.normalize(".")
    sftp.put(str(ROOT / ".env"), posixpath.join(base, ".env"))
    print("  .env hochgeladen")
    if with_data:
        count = 0
        for f in (ROOT / "data").rglob("*"):
            rel = f.relative_to(ROOT).as_posix()
            target = posixpath.join(base, rel)
            if f.is_dir():
                mkdirs(sftp, target)
            else:
                mkdirs(sftp, posixpath.dirname(target))
                sftp.put(str(f), target)
                count += 1
        print(f"  Gedaechtnis hochgeladen ({count} Dateien)")
    sftp.close()
    transport.close()


if __name__ == "__main__":
    main(with_data="--ohne-daten" not in sys.argv)
