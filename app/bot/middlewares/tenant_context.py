"""TenantContextMiddleware: Enforces strict tenant boundary for every incoming update.

Guarantees that handler data contains verified master_id, master and bot_instance.
In webhook mode, fails closed if tenant context is missing, invalid, or corrupted.
Restricts LegacyTenantResolver solely to polling mode.
"""

import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.master import BotInstance, BotInstanceStatus
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.services.tenant_context import LegacyTenantResolver

logger = logging.getLogger(__name__)


class TenantContextMiddleware(BaseMiddleware):
    """
    Middleware ensuring tenant context (master_id, master, bot_instance) is properly
    bound to the event. Rejects updates if tenant resolution fails.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        bot: Bot | None = data.get("bot")

        # 1. Check if bot_instance or master_id are provided (e.g. from webhook feed_update)
        bot_instance: BotInstance | None = data.get("bot_instance")
        master_id: int | None = data.get("master_id")

        if bot_instance is not None:
            if master_id is not None and master_id != bot_instance.master_id:
                logger.error(
                    "Tenant context mismatch: master_id=%s vs bot_instance.master_id=%s",
                    master_id,
                    bot_instance.master_id,
                )
                return None  # Fail closed

            if bot_instance.status in (BotInstanceStatus.DISABLED, BotInstanceStatus.ERROR):
                logger.warning(
                    "Rejecting update for bot_instance #%s with inactive status %s",
                    bot_instance.id,
                    bot_instance.status,
                )
                return None

            master_id = bot_instance.master_id
            data["master_id"] = master_id

        # A numeric master_id alone is not proof of tenancy in webhook mode.
        # Resolve the BotInstance server-side before any handler receives it.
        if bot_instance is None and session is not None:
            if bot is not None and hasattr(bot, "id") and bot.id:
                bot_repo = BotInstanceRepository(session)
                instance = await bot_repo.get_by_telegram_bot_id(bot.id)
                if instance is not None:
                    if master_id is not None and master_id != instance.master_id:
                        logger.error("Tenant context mismatch for resolved bot_instance #%s", instance.id)
                        return None
                    if instance.status in (BotInstanceStatus.DISABLED, BotInstanceStatus.ERROR):
                        logger.warning(
                            "Rejecting update for bot_instance #%s with inactive status %s",
                            instance.id,
                            instance.status,
                        )
                        return None
                    bot_instance = instance
                    data["bot_instance"] = instance
                    master_id = instance.master_id
                    data["master_id"] = master_id

            # 3. If still unresolved, fallback to legacy tenant ONLY in polling mode
            if bot_instance is None:
                if settings.app_mode == "polling":
                    master_id = await LegacyTenantResolver.get_master_id(session)
                    data["master_id"] = master_id
                    logger.debug("Resolved fallback master_id=%s in polling mode", master_id)
                else:
                    logger.error(
                        "Fail-closed: rejected update with unresolved tenant in %s mode",
                        settings.app_mode,
                    )
                    return None  # Fail closed in webhook mode!

        if master_id is None or (settings.app_mode == "webhook" and bot_instance is None):
            logger.error("Fail-closed: update rejected without trusted tenant context")
            return None

        if session is not None and bot_instance is not None:
            # Outbox producers use only this verified server-side binding.
            session.info["bot_instance_id"] = bot_instance.id
            session.info["trusted_master_id"] = master_id
        if settings.app_mode == "webhook" and session is None:
            logger.error("Fail-closed: webhook update rejected without database session")
            return None

        # Always resolve the Master from the verified id; never trust a supplied object.
        if session is not None:
            master_repo = MasterRepository(session)
            master = await master_repo.get_by_id(master_id)
            if not master:
                logger.error("Fail-closed: Master #%s not found in DB", master_id)
                return None
            data["master"] = master

        return await handler(event, data)
