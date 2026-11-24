"""Reserve-Anbieter aus der .env lesen und ein lokales Ollama automatisch finden."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from .brain_openai import Provider

log = logging.getLogger("dave.providers")

# Bekannte kostenlose Anbieter: Name -> OpenAI-kompatible Adresse
KNOWN = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openrouter": "https://openrouter.ai/api/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
}
USER_AGENT = "Dave-Discord-Bot/1.0 (+https://github.com/spitlightning-coder/Kiiro)"
OLLAMA_PREFERRED = ("qwen3", "llama3", "gemma3", "gemma", "mistral", "qwen2.5", "phi")


def fallbacks_from_env(env=os.environ) -> list[Provider]:
    """DAVE_FALLBACK1_NAME=groq, DAVE_FALLBACK1_KEY=…, DAVE_FALLBACK1_MODELS=a,b (URL optional bei bekannten Namen)."""
    out = []
    for i in range(1, 10):
        name = env.get(f"DAVE_FALLBACK{i}_NAME", "").strip().lower()
        models = [m.strip() for m in env.get(f"DAVE_FALLBACK{i}_MODELS", "").split(",") if m.strip()]
        key = env.get(f"DAVE_FALLBACK{i}_KEY", "").strip()
        url = env.get(f"DAVE_FALLBACK{i}_URL", "").strip() or KNOWN.get(name, "")
        if not name:
            continue
        if not (url and models and key):
            log.warning("Reserve %d (%s) unvollständig: braucht Adresse, Key und Modelle", i, name)
            continue
        out.append(Provider(name, url, key, models))
    return out


def find_ollama(url: str = "http://127.0.0.1:11434", wanted: str = "") -> Provider | None:
    """Fragt ein lokal laufendes Ollama nach seinen Modellen. Kein Ollama -> None (kein Problem)."""
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=2) as r:
            names = [m.get("name", "") for m in json.load(r).get("models", [])]
    except Exception:  # noqa: BLE001 – Ollama einfach nicht da
        return None
    names = [n for n in names if n and "embed" not in n]
    if not names:
        log.info("Ollama läuft, hat aber kein Chat-Modell geladen (z.B. 'ollama pull qwen3:8b')")
        return None
    if wanted and wanted in names:
        model = wanted
    else:
        model = next((n for pref in OLLAMA_PREFERRED for n in names if n.startswith(pref)), names[0])
    return Provider("ollama (lokal)", f"{url}/v1", "ollama", [model], timeout=120)


def verify(provider: Provider, timeout: float = 6) -> Provider | None:
    """Fragt die Modell-Liste ab (kostet kein Kontingent). Unbekannte Modelle fliegen raus.
    Schlüssel falsch -> None. Liste nicht abrufbar -> Anbieter bleibt unverändert drin."""
    # Eigener User-Agent: Groq (Cloudflare) blockt den Standard "Python-urllib" mit 403 (Fehler 1010)
    req = urllib.request.Request(provider.base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {provider.api_key}", "User-Agent": USER_AGENT,
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            available = {m.get("id", "").removeprefix("models/") for m in json.load(r).get("data", [])}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            log.error("%s: Schlüssel wird abgelehnt (HTTP 401) – Reserve deaktiviert", provider.name)
            return None
        if e.code == 403:  # kann auch eine Firewall/Cloudflare sein – im Betrieb merkt Dave, ob es geht
            log.warning("%s: Modell-Liste verweigert (HTTP 403), probiere den Anbieter trotzdem", provider.name)
            return provider
        log.warning("%s: Modell-Liste nicht abrufbar (HTTP %s), nutze sie trotzdem", provider.name, e.code)
        return provider
    except Exception as e:  # noqa: BLE001
        log.warning("%s: nicht erreichbar beim Start (%s), nutze sie trotzdem", provider.name, e)
        return provider
    if not available:
        return provider
    ok = [m for m in provider.models if m in available]
    missing = [m for m in provider.models if m not in available]
    if missing:
        log.warning("%s: diese Modelle gibt es nicht: %s", provider.name, ", ".join(missing))
        similar = sorted(a for a in available if any(w in a for w in ("flash", "gemma", "pro")))[:15]
        if similar:
            log.info("%s: verfügbar wären z.B.: %s", provider.name, ", ".join(similar))
    if not ok:
        log.error("%s: keins der Modelle verfügbar – Reserve deaktiviert", provider.name)
        return None
    provider.models = ok
    log.info("%s: Schlüssel ok, Modelle: %s", provider.name, ", ".join(ok))
    return provider
