"""
Application entry point and bootstrap runner.
"""

import asyncio
import logging
import sys

from app.config.settings import settings
from app.database.session import close_db, engine, init_db

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

    # Test database connectivity
    try:
        logger.info("Checking database connection...")
        async with engine.connect() as conn:
            from sqlalchemy import text
            result = await conn.execute(text("SELECT 1;"))
            scalar = result.scalar()
            logger.info(f"Database connection successful (ping result: {scalar})")
    except Exception as e:
        logger.warning(
            f"Database not accessible right now ({e}). "
            "Ensure PostgreSQL is running via docker-compose up -d postgres."
        )

    logger.info("Stage 2 infrastructure initialization completed successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application stopped.")
    finally:
        asyncio.run(close_db())
