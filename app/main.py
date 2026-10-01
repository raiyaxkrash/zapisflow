"""
Application entry point and bot polling runner.
"""

import asyncio
import logging
import sys

from app.bot.bot_instance import create_bot, create_dispatcher
from app.config.settings import settings
from app.database.session import close_db, init_db

from app.core.security import SensitiveDataFilter, install_sensitive_logging

install_sensitive_logging()

_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.addFilter(SensitiveDataFilter())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[_stdout_handler],
)
logger = logging.getLogger("app.main")


async def main() -> None:
    """
    Main asynchronous initialization and runner.
    """
    logger.info("Initializing Beauty Master Bot Application...")
    logger.info("Database URL: %s", settings.safe_database_url)
    logger.info("Redis Host: %s:%s", settings.redis_host, settings.redis_port)
    logger.info("Timezone: %s", settings.timezone)
    logger.info("Configured Admins Count: %s", len(settings.admin_ids))

    logger.info("Application Mode: %s", settings.app_mode)

    if settings.app_mode.lower() == "webhook":
        logger.info(
            "Starting Webhook Ingestion Engine on %s:%s (Base URL: %s)...",
            settings.webhook_host,
            settings.webhook_port,
            settings.webhook_base_url or "direct",
        )
        import uvicorn
        from app.web.app import create_app

        config = uvicorn.Config(
            app=create_app(),
            host=settings.webhook_host,
            port=settings.webhook_port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        await server.serve()
        return

    # Polling mode (legacy single-tenant development)
    if not settings.bot_token or settings.bot_token == "dummy_token_for_init":
        raise RuntimeError("BOT_TOKEN must be configured before starting the bot in polling mode")
    try:
        await init_db()
    except Exception:
        logger.exception("Database startup verification failed")
        raise

    bot = create_bot()
    dp = await create_dispatcher()

    from app.scheduler import setup_scheduler
    scheduler = setup_scheduler(bot)

    logger.info("Starting bot polling and background scheduler...")
    try:
        scheduler.start()
        await bot.delete_webhook(drop_pending_updates=False)
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
