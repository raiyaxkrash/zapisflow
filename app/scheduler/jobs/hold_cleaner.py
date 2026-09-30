"""
Scheduled background job to clean expired WAITING_PAYMENT booking holds.
Releases reserved time slots and notifies clients.
"""

import logging
from datetime import datetime
from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import settings
from app.database.session import async_session_maker
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.settings_repository import SettingsRepository
from app.services.booking_service import BookingService
from app.utils.formatters import format_datetime_ru

logger = logging.getLogger("app.scheduler.hold_cleaner")


async def clean_expired_holds(bot: Bot, session_maker: async_sessionmaker = async_session_maker) -> int:
    """
    Search for WAITING_PAYMENT appointments where hold deadline passed and expire them.
    """
    expired_ids: list[int] = []
    notifications: list[tuple[int, int, datetime, str]] = []
    async with session_maker() as session:
        try:
            app_repo = AppointmentRepository(session)
            booking_service = BookingService(session)
            settings_repo = SettingsRepository(session)

            tz_str = await settings_repo.get_value("timezone", settings.timezone)
            expired_appointments = await app_repo.get_expired_holds()

            if not expired_appointments:
                return 0

            for app in expired_appointments:
                if await booking_service.expire_booking(app.id) is None:
                    continue

                expired_ids.append(app.id)
                if app.user and app.user.telegram_id and app.user.telegram_id > 0:
                    notifications.append(
                        (app.id, app.user.telegram_id, app.start_time, app.snapshot_service_title)
                    )

            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("Error during clean_expired_holds job: %s", exc, exc_info=True)
            return 0

    # The database change is durable before any external side effect is attempted.
    for appointment_id in expired_ids:
        logger.info("Released expired hold for appointment #%s", appointment_id)

    for appointment_id, telegram_id, start_time, service_title in notifications:
        try:
            dt_str = format_datetime_ru(start_time, tz_name=tz_str)
            client_text = (
                f"⌛️ <b>Время на оплату брони истекло</b>\n\n"
                f"Запись <b>#{appointment_id}</b> на {dt_str} "
                f"({service_title}) была автоматически отменена, "
                f"а слот освобождён для других клиентов.\n\n"
                "Вы всегда можете выбрать новое удобное время в главном меню 🌸"
            )
            await bot.send_message(chat_id=telegram_id, text=client_text)
        except Exception:
            logger.warning("Failed to send expiry notice to user %s", telegram_id, exc_info=True)

    return len(expired_ids)
