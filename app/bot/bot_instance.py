"""
Bot and Dispatcher initialization with Redis FSM storage and global middlewares.
"""

import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from app.bot.handlers.admin import admin_router
from app.bot.handlers.client import client_router
from app.bot.middlewares import DbSessionMiddleware, UserContextMiddleware
from app.config.settings import settings

logger = logging.getLogger("app.bot")


def create_bot() -> Bot:
    """
    Create aiogram Bot instance with HTML parse mode.
    """
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def create_dispatcher() -> Dispatcher:
    """
    Create Dispatcher configured with Redis or Memory storage and attach global middlewares.
    """
    try:
        redis_client = Redis.from_url(settings.redis_url)
        # Ping redis
        await redis_client.ping()
        storage = RedisStorage(
            redis=redis_client,
            key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
        )
        logger.info(f"Connected to Redis at {settings.redis_host}:{settings.redis_port} for FSM storage")
    except Exception as e:
        logger.warning(f"Could not connect to Redis ({e}), falling back to in-memory FSM storage")
        storage = MemoryStorage()

    dp = Dispatcher(storage=storage)

    # 1. Outer middleware: DB session (available in all inner filters and handlers)
    dp.update.outer_middleware(DbSessionMiddleware())

    # 2. Inner middleware: User context (registration, CRM profile, admin checks)
    dp.update.middleware(UserContextMiddleware())

    # 3. Include routers
    dp.include_router(admin_router)
    dp.include_router(client_router)

    return dp
