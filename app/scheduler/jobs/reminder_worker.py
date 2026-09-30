"""
Scheduled background job for sending automated client visit reminders.
Supports 24-hour and 3-hour pre-visit notifications with studio directions.
"""

from datetime import datetime, timedelta
import logging
import pytz
from aiogram import Bot
from sqlalchemy import select
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
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.utils.formatters import format_datetime_ru, format_rub, format_time_ru

logger = logging.getLogger("app.scheduler.reminder_worker")


async def send_visit_reminders(
    bot: Bot, session_maker: async_sessionmaker = async_session_maker
) -> int:
    """
    Claim due reminders, then send them outside database write transactions.

    A PENDING row is committed before an external send so a second worker cannot
    send the same reminder. An interrupted worker leaves a visible PENDING row
    for manual review; automatic retry could duplicate an already sent message.
    """
    sent_count = 0
    now_utc = datetime.now(pytz.UTC)
    claims: list[tuple[int, int, int, str]] = []

    async with session_maker() as session:
        try:
            master_settings_repo = MasterSettingsRepository(session)

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

                m_settings = await master_settings_repo.get_by_master_id(app.master_id)
                tz_str = settings.timezone
                studio_address = (
                    m_settings.studio_address
                    if m_settings and m_settings.studio_address
                    else "Адрес студии мастера"
                )
                reminder_24h_ok = m_settings.reminder_24h_enabled if m_settings else True
                reminder_3h_ok = m_settings.reminder_3h_enabled if m_settings else True

                claimed_types = {n.type for n in app.notifications}
                time_until = app.start_time - now_utc

                # 1. Check 24-Hour Reminder
                if timedelta(hours=3) < time_until <= timedelta(hours=24):
                    if reminder_24h_ok and NotificationType.REMINDER_24H not in claimed_types:
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
                        notif = Notification(
                            appointment_id=app.id,
                            type=NotificationType.REMINDER_24H,
                            scheduled_at=now_utc,
                            status=NotificationStatus.PENDING,
                        )
                        session.add(notif)
                        await session.flush()
                        claims.append((notif.id, app.id, user.telegram_id, msg_text))

                # 2. Check 3-Hour Reminder
                elif timedelta(minutes=0) < time_until <= timedelta(hours=3):
                    if reminder_3h_ok and NotificationType.REMINDER_3H not in claimed_types:
                        time_str = format_time_ru(app.start_time, tz_name=tz_str)
                        msg_text = (
                            f"⏰ <b>Скоро ваша запись!</b>\n\n"
                            f"Через несколько часов мы ждём вас на <b>{app.snapshot_service_title}</b>:\n"
                            f"🗓 <b>Время визита:</b> {time_str}\n"
                            f"📍 <b>Адрес:</b> {studio_address}\n\n"
                            "Пожалуйста, приходите без опозданий. До скорой встречи! 🌸"
                        )
                        notif = Notification(
                            appointment_id=app.id,
                            type=NotificationType.REMINDER_3H,
                            scheduled_at=now_utc,
                            status=NotificationStatus.PENDING,
                        )
                        session.add(notif)
                        await session.flush()
                        claims.append((notif.id, app.id, user.telegram_id, msg_text))

            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("Error claiming visit reminders: %s", exc, exc_info=True)
            return 0

        for notification_id, appointment_id, telegram_id, msg_text in claims:
            try:
                async with session.begin():
                    status = await session.scalar(
                        select(Appointment.status).where(Appointment.id == appointment_id)
                    )
                    if status != AppointmentStatus.CONFIRMED:
                        notification = await session.get(Notification, notification_id)
                        if notification is not None:
                            notification.status = NotificationStatus.CANCELLED
                if status != AppointmentStatus.CONFIRMED:
                    continue

                delivered = False
                try:
                    await bot.send_message(chat_id=telegram_id, text=msg_text)
                    delivered = True
                except Exception:
                    logger.warning(
                        "Failed to send reminder #%s to %s",
                        notification_id,
                        telegram_id,
                        exc_info=True,
                    )

                async with session.begin():
                    notification = await session.get(Notification, notification_id)
                    if notification is not None:
                        notification.status = (
                            NotificationStatus.SENT if delivered else NotificationStatus.FAILED
                        )
                        if delivered:
                            notification.sent_at = datetime.now(pytz.UTC)
                if delivered:
                    sent_count += 1
                    logger.info(
                        "Sent reminder #%s for appointment #%s to %s",
                        notification_id,
                        appointment_id,
                        telegram_id,
                    )
            except Exception:
                await session.rollback()
                logger.error(
                    "Failed to record reminder #%s outcome",
                    notification_id,
                    exc_info=True,
                )

    return sent_count
