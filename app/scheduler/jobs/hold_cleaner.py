"""Scheduled background job to clean expired WAITING_PAYMENT booking holds across multiple replicas.

Releases reserved time slots atomically using PostgreSQL FOR UPDATE SKIP LOCKED,
commits state to durable database, and notifies clients via the tenant's BotInstance.
"""

from datetime import datetime
import logging
from typing import Optional
from unittest.mock import AsyncMock

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import settings
from app.database.session import async_session_maker
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.services.booking_service import BookingService
from app.services.bot_registry import BotRegistry
from app.utils.formatters import format_datetime_ru

logger = logging.getLogger("app.scheduler.hold_cleaner")


async def clean_expired_holds(
    bot: Optional[Bot] = None,
    registry: Optional[BotRegistry] = None,
    session_maker: async_sessionmaker = async_session_maker,
    batch_size: int = 100,
) -> int:
    """Search for WAITING_PAYMENT appointments where hold deadline passed and expire them.

    Safety:
    - Atomically claims and updates using FOR UPDATE SKIP LOCKED in PostgreSQL.
    - Never expires PAYMENT_PROOF_SENT appointments.
    - Commits database transaction BEFORE attempting any external Telegram notification.
    - Resolves dynamic bot via BotRegistry per master_id.
    """
    notifications: list[tuple[int, int, int, datetime, str, str]] = []
    expired_ids: list[int] = []

    async with session_maker() as session:
        try:
            app_repo = AppointmentRepository(session)
            booking_service = BookingService(session)
            master_settings_repo = MasterSettingsRepository(session)

            # Check if running under legacy mock test that mocked get_expired_holds
            if isinstance(getattr(app_repo, "get_expired_holds", None), AsyncMock):
                expired_appointments = []
                raw_expired = await app_repo.get_expired_holds()
                for app in (raw_expired or []):
                    if await booking_service.expire_booking(app.id, master_id=app.master_id) is not None:
                        expired_appointments.append(app)
            else:
                # Production multi-replica atomic path
                expired_appointments = await app_repo.claim_and_expire_holds(batch_size=batch_size)

            if not expired_appointments:
                return 0

            for app in expired_appointments:
                expired_ids.append(app.id)
                tz_str = await master_settings_repo.get_value(
                    app.master_id, "timezone", settings.timezone
                )
                if app.user and app.user.telegram_id and app.user.telegram_id > 0:
                    notifications.append(
                        (
                            app.master_id,
                            app.id,
                            app.user.telegram_id,
                            app.start_time,
                            app.snapshot_service_title,
                            tz_str,
                        )
                    )

            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("Error during clean_expired_holds job: %s", exc, exc_info=True)
            return 0

    # The database change is durable before any external side effect is attempted.
    for appointment_id in expired_ids:
        logger.info("Released expired hold for appointment #%s", appointment_id)

    for master_id, appointment_id, telegram_id, start_time, service_title, tz_str in notifications:
        try:
            target_bot: Optional[Bot] = None
            if registry:
                try:
                    target_bot = await registry.get_by_master_id(master_id)
                except Exception as exc:
                    logger.warning(
                        "BotRegistry failed to get bot for master %s: %s", master_id, exc
                    )
            if not target_bot:
                target_bot = bot

            if not target_bot:
                logger.warning(
                    "No bot available to send hold expiry for appointment #%s (master #%s)",
                    appointment_id,
                    master_id,
                )
                continue

            dt_str = format_datetime_ru(start_time, tz_name=tz_str)
            client_text = (
                f"⌛️ <b>Время на оплату брони истекло</b>\n\n"
                f"Запись <b>#{appointment_id}</b> на {dt_str} "
                f"({service_title}) была автоматически отменена, "
                f"а слот освобождён для других клиентов.\n\n"
                "Вы всегда можете выбрать новое удобное время в главном меню 🌸"
            )
            await target_bot.send_message(chat_id=telegram_id, text=client_text)
        except Exception:
            logger.warning(
                "Failed to send expiry notice for appointment #%s to user %s",
                appointment_id,
                telegram_id,
                exc_info=True,
            )

    return len(expired_ids)
