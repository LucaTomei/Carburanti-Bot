"""Entry-point per il bot Telegram Osservaprezzi Carburanti."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from telegram_bot.bot import FuelPriceTelegramBot


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Variabile TELEGRAM_BOT_TOKEN non impostata")

    data_dir = Path(os.getenv("BOT_DATA_DIR", ".bot_data"))
    bot = FuelPriceTelegramBot(token=token, data_dir=data_dir)
    bot.run()


if __name__ == "__main__":
    main()
