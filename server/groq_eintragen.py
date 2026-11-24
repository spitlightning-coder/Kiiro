"""Trägt einen Groq-Key aus der Umgebungsvariable GROQ_KEY als schnelle Reserve in die .env ein.

Reihenfolge danach: Gemini (Haupt) -> Groq (Reserve 1) -> Requesty (Reserve 2).
Der Key wird nie ausgegeben, nur die letzten 4 Zeichen zur Kontrolle.
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMENT = "# Reserven der Reihe nach (Groq = schnell, Requesty = langsam, aber da)"
GROQ_MODELS = "openai/gpt-oss-120b,llama-3.3-70b-versatile,openai/gpt-oss-20b,llama-3.1-8b-instant"


def main() -> int:
    key = (os.environ.get("GROQ_KEY") or "").strip().strip('"').strip()
    if not re.fullmatch(r"gsk_[A-Za-z0-9]{20,}", key):
        print("  In der Zwischenablage ist kein Groq-Key (der beginnt mit gsk_). Erst bei Groq kopieren!")
        return 1
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines()
    fallbacks: dict[int, dict[str, str]] = {}
    rest = []
    for line in lines:
        m = re.match(r"DAVE_FALLBACK(\d)_(NAME|KEY|MODELS|URL)=(.*)$", line)
        if m:
            fallbacks.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
        else:
            rest.append(line)
    others = [fb for _i, fb in sorted(fallbacks.items()) if fb.get("NAME", "").strip().lower() != "groq"]
    ordered = [{"NAME": "groq", "KEY": key, "MODELS": GROQ_MODELS}] + others
    while rest and not rest[-1].strip():
        rest.pop()
    rest = [line for line in rest if line != COMMENT]
    while rest and not rest[-1].strip():
        rest.pop()
    out = rest + ["", COMMENT]
    for i, fb in enumerate(ordered, start=1):
        for field in ("NAME", "KEY", "MODELS", "URL"):
            if fb.get(field):
                out.append(f"DAVE_FALLBACK{i}_{field}={fb[field]}")
    env.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"  Groq-Key ...{key[-4:]} eingetragen. Reihenfolge: Gemini -> "
          + " -> ".join(fb["NAME"] for fb in ordered))
    return 0


if __name__ == "__main__":
    sys.exit(main())
