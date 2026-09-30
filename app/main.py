"""
Application entry point and bot polling runner.
"""

import asyncio
import logging
import sys

from app.bot.bot_instance import create_bot, create_dispatcher
from app.config.settings import settings
from app.database.session import close_db, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app.main")


async def main() -> None:
    """
    Main asynchronous initialization and runner.
    """
    logger.info("Initializing Beauty Master Bot Application...")
    logger.info(f"Target Database Host: {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")
    logger.info(f"Redis Host: {settings.redis_host}:{settings.redis_port}")
    logger.info(f"Timezone: {settings.timezone}")
    logger.info(f"Configured Admins Count: {len(settings.admin_ids)}")

    # Check database connectivity
    try:
        logger.info("Checking database connection...")
        async with engine.connect() as conn:
            from sqlalchemy import text
            result = await conn.execute(text("SELECT 1;"))
            scalar = result.scalar()
            logger.info(f"Database connection successful (ping result: {scalar})")
    except Exception as e:
        logger.warning(
            f"Database connection check: {e}. "
            "Ensure PostgreSQL is running via docker-compose up -d postgres."
        )

    # Validate bot token before polling
    if not settings.bot_token or settings.bot_token == "dummy_token_for_init":
        logger.warning(
            "BOT_TOKEN is not set or using dummy value. "
            "Specify a real BOT_TOKEN in .env to connect to Telegram. "
            "All client routers, middlewares and handlers verified and ready."
        )
        return

    bot = create_bot()
    dp = await create_dispatcher()
    
    from app.scheduler import setup_scheduler
    scheduler = setup_scheduler(bot)

    logger.info("Starting bot polling and background scheduler...")
    try:
        scheduler.start()
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application stopped.")
