"""Start: python -m dave"""

import logging
import sys
from pathlib import Path

from .bot import Dave
from .brain import Brain
from .config import Config, load_personality


def main() -> None:
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(log_dir / "dave.log", encoding="utf-8")],
    )
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
    Dave(config, brain).run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
