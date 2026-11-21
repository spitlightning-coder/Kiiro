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

        models = [m.strip() for m in config.model.split(",") if m.strip()]
        brain = OpenAIBrain(load_personality(), models=models, max_tokens=config.max_tokens,
                            base_url=config.api_base, api_key=config.api_key)
        logging.getLogger("dave").info("Gehirn: %s über %s", ", ".join(models), config.api_base)
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
    Dave(config, brain, store, server).run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
