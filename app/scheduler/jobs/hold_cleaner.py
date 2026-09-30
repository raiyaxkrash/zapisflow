"""
Scheduled background job to clean expired WAITING_PAYMENT booking holds.
Releases reserved time slots and notifies clients.
"""

import logging
from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import settings
from app.database.models.appointment import AppointmentStatus
from app.database.models.payment import PaymentStatus
from app.database.session import async_session_maker
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.settings_repository import SettingsRepository
from app.utils.formatters import format_datetime_ru

logger = logging.getLogger("app.scheduler.hold_cleaner")


async def clean_expired_holds(bot: Bot, session_maker: async_sessionmaker = async_session_maker) -> int:
    """
    Search for WAITING_PAYMENT appointments where hold deadline passed and expire them.
    """
    cleaned_count = 0
    async with session_maker() as session:
        try:
            app_repo = AppointmentRepository(session)
            pay_repo = PaymentRepository(session)
            settings_repo = SettingsRepository(session)

            tz_str = await settings_repo.get_value("timezone", settings.timezone)
            expired_appointments = await app_repo.get_expired_holds()

            if not expired_appointments:
                return 0

            for app in expired_appointments:
                app.status = AppointmentStatus.EXPIRED
                app.hold_until = None

                # Cancel associated pending payment
                payment = await pay_repo.get_by_appointment_id(app.id)
                if payment and payment.status == PaymentStatus.PENDING:
                    payment.status = PaymentStatus.REJECTED

                cleaned_count += 1
                logger.info(f"Released expired hold for appointment #{app.id} (user {app.user_id})")

                # Notify client
                if app.user and app.user.telegram_id and app.user.telegram_id > 0:
                    dt_str = format_datetime_ru(app.start_time, tz_name=tz_str)
                    client_text = (
                        f"⌛️ <b>Время на оплату брони истекло</b>\n\n"
                        f"Запись <b>#{app.id}</b> на {dt_str} "
                        f"({app.snapshot_service_title}) была автоматически отменена, "
                        f"а слот освобождён для других клиентов.\n\n"
                        "Вы всегда можете выбрать новое удобное время в главном меню 🌸"
                    )
                    try:
                        await bot.send_message(
                            chat_id=app.user.telegram_id,
                            text=client_text,
                        )
                    except Exception as e:
                        logger.debug(f"Failed to send expiry notice to user {app.user.telegram_id}: {e}")

            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error(f"Error during clean_expired_holds job: {exc}", exc_info=True)

    return cleaned_count
