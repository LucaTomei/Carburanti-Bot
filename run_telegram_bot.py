"""Entry-point per il bot Telegram Osservaprezzi Carburanti."""

from __future__ import annotations

import asyncio
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

    # Python 3.12+ non crea più automaticamente un event loop con
    # asyncio.get_event_loop(). python-telegram-bot lo chiama internamente,
    # quindi va impostato esplicitamente prima di istanziare il bot.
    asyncio.set_event_loop(asyncio.new_event_loop())

    admin_ids_raw = os.getenv("ADMIN_TELEGRAM_ID", "")
    admin_ids = {int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().isdigit()}

    data_dir = Path(os.getenv("BOT_DATA_DIR", ".bot_data"))
    bot = FuelPriceTelegramBot(token=token, data_dir=data_dir, admin_ids=admin_ids)
    bot.run()


if __name__ == "__main__":
    main()
