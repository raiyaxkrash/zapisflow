"""Dispatcher initialization for platform Manager Bot."""

import logging
from typing import Optional
from aiogram import Dispatcher
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.config.settings import settings
from app.manager_bot.handlers import manager_router

logger = logging.getLogger("app.manager_bot")


async def create_manager_dispatcher(redis_client: Optional[Redis] = None) -> Dispatcher:
    """Create dedicated Dispatcher for platform Manager Bot.

    Operates on an isolated router tree and separate FSM storage namespace.
    """
    storage = None
    if redis_client is not None:
        storage = RedisStorage(
            redis=redis_client,
            key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True, prefix="fsm_mgr"),
        )
    else:
        try:
            rc = Redis.from_url(settings.redis_url)
            await rc.ping()
            storage = RedisStorage(
                redis=rc,
                key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True, prefix="fsm_mgr"),
            )
            logger.info("Manager Bot connected to Redis for FSM storage")
        except Exception as exc:
            logger.warning("Manager Bot using MemoryStorage fallback for FSM: %s", exc)
            storage = MemoryStorage()

    dp = Dispatcher(storage=storage)

    # Attach DB session middleware
    dp.update.outer_middleware(DbSessionMiddleware())

    # Include dedicated Manager Bot router
    dp.include_router(manager_router)

    return dp
