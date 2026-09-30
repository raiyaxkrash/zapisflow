"""
Scheduled background job for sending automated client visit reminders.
Supports 24-hour and 3-hour pre-visit notifications with studio directions.
"""

from datetime import datetime, timedelta
import logging
import pytz
from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.notification import (
    Notification,
    NotificationStatus,
    NotificationType,
)
from app.database.session import async_session_maker
from app.repositories.settings_repository import SettingsRepository
from app.utils.formatters import format_datetime_ru, format_rub, format_time_ru

logger = logging.getLogger("app.scheduler.reminder_worker")


async def send_visit_reminders(
    bot: Bot, session_maker: async_sessionmaker = async_session_maker
) -> int:
    """
    Check confirmed upcoming appointments and dispatch 24h and 3h reminder messages.
    """
    sent_count = 0
    now_utc = datetime.now(pytz.UTC)

    async with session_maker() as session:
        try:
            settings_repo = SettingsRepository(session)
            tz_str = await settings_repo.get_value("timezone", settings.timezone)
            studio_address = await settings_repo.get_value(
                "studio_address", "г. Москва, ул. Ленина, д. 25, студия 4"
            )

            # Look ahead up to 26 hours
            horizon_dt = now_utc + timedelta(hours=26)

            query = (
                select(Appointment)
                .where(
                    Appointment.status == AppointmentStatus.CONFIRMED,
                    Appointment.start_time > now_utc,
                    Appointment.start_time <= horizon_dt,
                )
                .options(
                    selectinload(Appointment.user),
                    selectinload(Appointment.notifications),
                )
            )
            res = await session.execute(query)
            upcoming_appointments = res.scalars().all()

            for app in upcoming_appointments:
                user = app.user
                if not user or not user.telegram_id or user.telegram_id <= 0 or user.is_bot_blocked:
                    continue

                sent_types = {
                    n.type
                    for n in app.notifications
                    if n.status == NotificationStatus.SENT
                }

                time_until = app.start_time - now_utc

                # 1. Check 24-Hour Reminder
                if timedelta(hours=3) < time_until <= timedelta(hours=24):
                    if NotificationType.REMINDER_24H not in sent_types:
                        dt_str = format_datetime_ru(app.start_time, tz_name=tz_str)
                        rem_amount = app.snapshot_service_price - app.snapshot_deposit_amount
                        rem_str = format_rub(rem_amount)

                        msg_text = (
                            f"🌸 <b>Напоминание о записи на завтра!</b>\n\n"
                            f"Напоминаем, что вы записаны на <b>{app.snapshot_service_title}</b>:\n"
                            f"🗓 <b>Дата и время:</b> {dt_str}\n"
                            f"📍 <b>Адрес:</b> {studio_address}\n"
                            f"💰 <b>К доплате на месте:</b> {rem_str}\n\n"
                            "Если ваши планы изменились, пожалуйста, предупредите мастера заранее ❤️"
                        )
                        try:
                            await bot.send_message(chat_id=user.telegram_id, text=msg_text)
                            notif = Notification(
                                appointment_id=app.id,
                                type=NotificationType.REMINDER_24H,
                                scheduled_at=now_utc,
                                status=NotificationStatus.SENT,
                                sent_at=now_utc,
                            )
                            session.add(notif)
                            sent_count += 1
                            logger.info(f"Sent 24h reminder for appointment #{app.id} to {user.telegram_id}")
                        except Exception as e:
                            logger.warning(f"Failed to send 24h reminder to {user.telegram_id}: {e}")

                # 2. Check 3-Hour Reminder
                elif timedelta(minutes=0) < time_until <= timedelta(hours=3):
                    if NotificationType.REMINDER_3H not in sent_types:
                        time_str = format_time_ru(app.start_time, tz_name=tz_str)
                        msg_text = (
                            f"⏰ <b>Скоро ваша запись!</b>\n\n"
                            f"Через несколько часов мы ждём вас на <b>{app.snapshot_service_title}</b>:\n"
                            f"🗓 <b>Время визита:</b> {time_str}\n"
                            f"📍 <b>Адрес:</b> {studio_address}\n\n"
                            "Пожалуйста, приходите без опозданий. До скорой встречи! 🌸"
                        )
                        try:
                            await bot.send_message(chat_id=user.telegram_id, text=msg_text)
                            notif = Notification(
                                appointment_id=app.id,
                                type=NotificationType.REMINDER_3H,
                                scheduled_at=now_utc,
                                status=NotificationStatus.SENT,
                                sent_at=now_utc,
                            )
                            session.add(notif)
                            sent_count += 1
                            logger.info(f"Sent 3h reminder for appointment #{app.id} to {user.telegram_id}")
                        except Exception as e:
                            logger.warning(f"Failed to send 3h reminder to {user.telegram_id}: {e}")

            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error(f"Error during send_visit_reminders job: {exc}", exc_info=True)

    return sent_count
