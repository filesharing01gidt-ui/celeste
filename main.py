import asyncio
import logging

from bot.client import BotClient
from bot.config import load_config
from bot.logging_setup import setup_logging


async def main() -> None:
    config = load_config()
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    intents = BotClient.configure_intents()
    bot = BotClient(config=config, intents=intents)

    logger.info("Starting bot...")
    async with bot:
        await bot.start(config.token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
