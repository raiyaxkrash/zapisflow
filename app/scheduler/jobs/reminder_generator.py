"""Background job for idempotent generation of visit reminders.

Scans upcoming confirmed appointments, applies master settings (reminder_24h, reminder_3h)
and tenant configuration, inserting pending notifications with unique conflict guarantees.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Sequence
import pytz
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import Master, MasterClient
from app.database.models.notification import (
    Notification,
    NotificationStatus,
    NotificationType,
)
from app.database.session import async_session_maker
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.notification_repository import NotificationRepository

logger = logging.getLogger("app.scheduler.reminder_generator")


async def generate_visit_reminders(
    session_maker: async_sessionmaker = async_session_maker,
    lookahead_hours: int = 26,
) -> int:
    """Scan upcoming appointments and generate pending reminder rows idempotently.

    Rules:
    - Only appointments with status == CONFIRMED.
    - User must have a valid telegram_id > 0 and not be marked bot_blocked in MasterClient.
    - Respects MasterSettings reminder_24h_enabled and reminder_3h_enabled.
    - Idempotent via (appointment_id, type) unique constraint.
    - Cancels any PENDING reminders for appointments that are no longer CONFIRMED.
    """
    now_utc = datetime.now(timezone.utc)
    horizon_dt = now_utc + timedelta(hours=lookahead_hours)
    generated_count = 0

    async with session_maker() as session:
        try:
            notif_repo = NotificationRepository(session)
            master_settings_repo = MasterSettingsRepository(session)

            # 1. First, cancel any PENDING notifications whose appointments are no longer CONFIRMED
            # (e.g. cancelled, expired, completed, rescheduled)
            stale_notifs_subq = (
                select(Appointment.id)
                .where(Appointment.status != AppointmentStatus.CONFIRMED)
            )
            cancel_stmt = (
                update(Notification)
                .where(
                    Notification.appointment_id.in_(stale_notifs_subq),
                    Notification.status == NotificationStatus.PENDING,
                )
                .values(
                    status=NotificationStatus.CANCELLED,
                    last_error="Appointment no longer confirmed",
                )
            )
            await session.execute(cancel_stmt)

            # 2. Query upcoming confirmed appointments
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
            appointments = list(res.scalars().all())

            for app in appointments:
                user = app.user
                if not user or not user.telegram_id or user.telegram_id <= 0:
                    continue

                # Check if client has blocked bot for this master
                mc_res = await session.execute(
                    select(MasterClient).where(
                        MasterClient.master_id == app.master_id,
                        MasterClient.user_id == user.id,
                    )
                )
                mc = mc_res.scalars().first()
                if mc and mc.is_bot_blocked:
                    continue

                m_settings = await master_settings_repo.get_by_master_id(app.master_id)
                reminder_24h_ok = m_settings.reminder_24h_enabled if m_settings else True
                reminder_3h_ok = m_settings.reminder_3h_enabled if m_settings else True

                existing_types = {
                    n.type for n in app.notifications if n.status != NotificationStatus.CANCELLED
                }
                time_until = app.start_time - now_utc

                # 24-Hour Reminder check: between 3h and 24h away
                if timedelta(hours=3) < time_until <= timedelta(hours=24):
                    if reminder_24h_ok and NotificationType.REMINDER_24H not in existing_types:
                        created = await notif_repo.create_or_ignore(
                            appointment_id=app.id,
                            notification_type=NotificationType.REMINDER_24H,
                            scheduled_at=now_utc,
                        )
                        if created:
                            generated_count += 1

                # 3-Hour Reminder check: between 0h and 3h away
                elif timedelta(seconds=0) < time_until <= timedelta(hours=3):
                    if reminder_3h_ok and NotificationType.REMINDER_3H not in existing_types:
                        created = await notif_repo.create_or_ignore(
                            appointment_id=app.id,
                            notification_type=NotificationType.REMINDER_3H,
                            scheduled_at=now_utc,
                        )
                        if created:
                            generated_count += 1

            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("Error generating visit reminders: %s", exc, exc_info=True)
            return 0

    if generated_count > 0:
        logger.info("Generated %d new reminder notifications", generated_count)
    return generated_count
