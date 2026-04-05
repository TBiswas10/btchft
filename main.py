from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from btc_hft.bot import Bot
from btc_hft.config import load_and_validate_settings
from btc_hft.logging_utils import configure_logging


def main() -> None:
    load_dotenv()
    settings = load_and_validate_settings()
    configure_logging(settings.log_level, Path("runtime/logs"))
    bot = Bot(settings)
    bot.run()


if __name__ == "__main__":
    main()
