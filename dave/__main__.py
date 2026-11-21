"""Start: python -m dave"""

import logging
import logging.handlers
import os
import socket
import sys
from pathlib import Path

from .bot import Dave
from .brain import Brain
from .config import Config, load_personality


LOCK_PORT = 47831  # nur lokal: verhindert, dass Dave zweimal läuft (sonst antwortet er doppelt)


def single_instance() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        sock.close()
        return None
    return sock


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.handlers.RotatingFileHandler(log_dir / "dave.log", maxBytes=2_000_000, backupCount=3,
                                                       encoding="utf-8")],
    )
    lock = single_instance()
    if lock is None:
        logging.getLogger("dave").warning("Dave läuft schon (zweite Instanz beendet sich).")
        return
    (log_dir / "dave.pid").write_text(str(os.getpid()), encoding="utf-8")
    config = Config.from_env()
    if config.provider == "openai":
        from .brain_openai import OpenAIBrain

        from .providers import fallbacks_from_env, find_ollama, verify

        models = [m.strip() for m in config.model.split(",") if m.strip()]
        fallbacks = [p for p in (verify(p) for p in fallbacks_from_env()) if p]
        if os.getenv("DAVE_OLLAMA", "auto").strip().lower() != "aus":
            ollama = find_ollama(os.getenv("DAVE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
                                 os.getenv("DAVE_OLLAMA_MODEL", "").strip())
            if ollama:
                fallbacks.append(ollama)
        main_name = "requesty" if "requesty" in config.api_base else "haupt"
        brain = OpenAIBrain(load_personality(), models=models, max_tokens=config.max_tokens,
                            base_url=config.api_base, api_key=config.api_key, fallbacks=fallbacks, name=main_name)
        logging.getLogger("dave").info("Gehirn: %s", " → ".join(
            f"{p.name} [{', '.join(p.models)}]" for p in brain.providers))
    else:
        brain = Brain(load_personality(), model=config.model, effort=config.effort, max_tokens=config.max_tokens)
        logging.getLogger("dave").info("Gehirn: %s (Anthropic)", config.model)
    store = None
    if config.longterm:
        from .longterm import MemoryStore

        store = MemoryStore(config.data_dir)
        logging.getLogger("dave").info("Langzeitgedächtnis in %s (lernt alle %d Nachrichten)",
                                       config.data_dir, config.learn_every)
    from .serverdata import ServerData

    server = ServerData(config.data_dir)
    dave = Dave(config, brain, store, server)
    dave.run(config.discord_token, log_handler=None)
    if dave.reload_requested:
        lock.close()
        sys.exit(3)  # 3 = "bitte sofort neu starten" für dave_dienst.bat


if __name__ == "__main__":
    main()
