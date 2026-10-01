"""Visible fail-safe for stale or unknown client inline-keyboard callbacks."""

import logging
from typing import Optional

from aiogram import Router
from aiogram.types import CallbackQuery

from app.database.models.master import BotInstance

logger = logging.getLogger("app.bot.handlers.client.callback_fallback")
router = Router(name="client_callback_fallback")


@router.callback_query()
async def cb_unhandled_client_callback(
    callback: CallbackQuery,
    bot_instance: Optional[BotInstance] = None,
) -> None:
    """Acknowledge callback queries that no feature handler recognized.

    Log only the callback data prefix; callback payloads may contain user-provided
    or tenant-specific identifiers and should not be copied into production logs.
    """
    callback_data = callback.data or ""
    prefix = callback_data.split(":", 1)[0][:32] or "<empty>"
    logger.warning(
        "Unhandled client callback: bot_instance_id=%s prefix=%s",
        bot_instance.id if bot_instance else None,
        prefix,
    )
    await callback.answer(
        "Эта кнопка устарела. Откройте меню командой /start и попробуйте снова.",
        show_alert=True,
    )
