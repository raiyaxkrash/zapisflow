"""Scheduled background job for reliable client visit reminder delivery across multiple replicas.

Claims due notifications using PostgreSQL FOR UPDATE SKIP LOCKED, commits before external dispatch,
routes via dynamic tenant BotRegistry, and handles transient retries and permanent bot blocks.
"""

from datetime import datetime, timezone
import logging
from typing import Optional
import uuid

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
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
from app.scheduler.jobs.reminder_generator import generate_visit_reminders
from app.services.bot_registry import BotRegistry
from app.utils.formatters import format_datetime_ru, format_rub, format_time_ru

logger = logging.getLogger("app.scheduler.reminder_worker")


async def send_visit_reminders(
    bot: Optional[Bot] = None,
    registry: Optional[BotRegistry] = None,
    session_maker: async_sessionmaker = async_session_maker,
    worker_id: Optional[str] = None,
    batch_size: Optional[int] = None,
    max_attempts: Optional[int] = None,
    auto_generate: bool = True,
) -> int:
    """Claim and send due visit reminders outside long database write transactions.

    Multi-replica safety guarantees:
    - Auto-generates pending reminders if enabled (idempotent via ON CONFLICT DO NOTHING).
    - Reclaims stale processing rows from crashed workers.
    - Atomically claims PENDING and due RETRY rows using FOR UPDATE SKIP LOCKED.
    - Commits the claim transaction BEFORE making any external network requests.
    - Routes outbound messages through tenant's BotInstance via BotRegistry.
    - Marks MasterClient.is_bot_blocked = True on TelegramForbiddenError.
    - Reschedules with exponential backoff on transient network errors.
    """
    actual_worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
    actual_batch_size = batch_size or settings.scheduler_batch_size
    actual_max_attempts = max_attempts or settings.job_max_attempts
    sent_count = 0

    # 1. Optionally run generator to ensure any due appointments are in notifications table
    if auto_generate:
        try:
            await generate_visit_reminders(session_maker=session_maker)
        except Exception as exc:
            logger.error("Auto-generation of reminders failed: %s", exc, exc_info=True)

    # 2. Reclaim stale processing notifications (from dead pods / unhandled terminations)
    try:
        async with session_maker() as session:
            notif_repo = NotificationRepository(session)
            reclaimed = await notif_repo.reclaim_stale_processing(
                timeout_seconds=settings.job_processing_timeout_seconds,
                max_attempts=actual_max_attempts,
            )
            if reclaimed > 0:
                await session.commit()
                logger.info("Worker %s reclaimed %d stale notifications", actual_worker_id, reclaimed)
    except Exception as exc:
        logger.error("Error reclaiming stale notifications: %s", exc, exc_info=True)

    # 3. Claim due notifications in a short atomic transaction
    claimed_ids: list[int] = []
    try:
        async with session_maker() as session:
            notif_repo = NotificationRepository(session)
            claimed = await notif_repo.claim_due_notifications(
                worker_id=actual_worker_id,
                batch_size=actual_batch_size,
                max_attempts=actual_max_attempts,
            )
            claimed_ids = [n.id for n in claimed]
            await session.commit()
    except Exception as exc:
        logger.error("Error claiming due notifications: %s", exc, exc_info=True)
        return 0

    if not claimed_ids:
        return 0

    # 4. Dispatch each claimed notification individually outside the claim transaction
    for notification_id in claimed_ids:
        try:
            # Fetch notification details in a clean read session
            async with session_maker() as session:
                stmt = (
                    select(Notification)
                    .where(Notification.id == notification_id)
                    .options(
                        selectinload(Notification.appointment).selectinload(Appointment.user),
                    )
                )
                res = await session.execute(stmt)
                notif = res.scalars().first()

            if not notif:
                continue

            app = notif.appointment
            if not app:
                async with session_maker() as session:
                    await NotificationRepository(session).mark_cancelled(
                        notification_id, "No appointment linked"
                    )
                    await session.commit()
                continue

            # Verify appointment status: must still be CONFIRMED!
            if app.status != AppointmentStatus.CONFIRMED:
                async with session_maker() as session:
                    await NotificationRepository(session).mark_cancelled(
                        notification_id, f"Appointment status is {app.status}"
                    )
                    await session.commit()
                continue

            user = app.user
            if not user or not user.telegram_id or user.telegram_id <= 0:
                async with session_maker() as session:
                    await NotificationRepository(session).mark_failed(
                        notification_id, "Invalid user or telegram_id", retry_delay_seconds=None
                    )
                    await session.commit()
                continue

            # Check if user has blocked the bot in master_clients
            async with session_maker() as session:
                mc_res = await session.execute(
                    select(MasterClient).where(
                        MasterClient.master_id == app.master_id,
                        MasterClient.user_id == user.id,
                    )
                )
                mc = mc_res.scalars().first()
                if mc and mc.is_bot_blocked:
                    await NotificationRepository(session).mark_failed(
                        notification_id,
                        "MasterClient is marked bot_blocked",
                        retry_delay_seconds=None,
                    )
                    await session.commit()
                    continue

            # Resolve bot instance
            target_bot: Optional[Bot] = None
            if registry:
                try:
                    target_bot = await registry.get_by_master_id(app.master_id)
                except Exception as exc:
                    logger.warning(
                        "BotRegistry failed to get bot for master %s: %s", app.master_id, exc
                    )
            if not target_bot:
                target_bot = bot

            if not target_bot:
                async with session_maker() as session:
                    await NotificationRepository(session).mark_failed(
                        notification_id,
                        f"No active bot found for master #{app.master_id}",
                        retry_delay_seconds=60,
                        max_attempts=actual_max_attempts,
                    )
                    await session.commit()
                continue

            # Prepare localized message content
            async with session_maker() as session:
                master = await session.get(Master, app.master_id)
                m_settings_repo = MasterSettingsRepository(session)
                m_settings = await m_settings_repo.get_by_master_id(app.master_id)

            tz_str = (
                (master.timezone if master and master.timezone else None)
                or settings.timezone
            )
            studio_address = (
                m_settings.studio_address
                if m_settings and m_settings.studio_address
                else "Адрес студии мастера"
            )

            if notif.type == NotificationType.REMINDER_24H:
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
            elif notif.type == NotificationType.REMINDER_3H:
                time_str = format_time_ru(app.start_time, tz_name=tz_str)
                msg_text = (
                    f"⏰ <b>Скоро ваша запись!</b>\n\n"
                    f"Через несколько часов мы ждём вас на <b>{app.snapshot_service_title}</b>:\n"
                    f"🗓 <b>Время визита:</b> {time_str}\n"
                    f"📍 <b>Адрес:</b> {studio_address}\n\n"
                    "Пожалуйста, приходите без опозданий. До скорой встречи! 🌸"
                )
            else:
                msg_text = f"Напоминание о записи #{app.id} ({app.snapshot_service_title})"

            # Perform external network delivery
            delivered = False
            try:
                await target_bot.send_message(chat_id=user.telegram_id, text=msg_text)
                delivered = True
            except TelegramForbiddenError:
                logger.warning(
                    "Bot blocked by user %s on reminder #%s", user.telegram_id, notification_id
                )
                async with session_maker() as session:
                    mc_res = await session.execute(
                        select(MasterClient).where(
                            MasterClient.master_id == app.master_id,
                            MasterClient.user_id == user.id,
                        )
                    )
                    mc = mc_res.scalars().first()
                    if mc:
                        mc.is_bot_blocked = True
                    await NotificationRepository(session).mark_failed(
                        notification_id,
                        "Bot blocked by user (TelegramForbiddenError)",
                        retry_delay_seconds=None,
                        max_attempts=actual_max_attempts,
                    )
                    await session.commit()
            except TelegramRetryAfter as exc:
                logger.warning(
                    "Telegram rate limited reminder #%s, retry after %s s",
                    notification_id,
                    exc.retry_after,
                )
                async with session_maker() as session:
                    await NotificationRepository(session).mark_failed(
                        notification_id,
                        f"Rate limited: retry after {exc.retry_after}s",
                        retry_delay_seconds=int(exc.retry_after) + 1,
                        max_attempts=actual_max_attempts,
                    )
                    await session.commit()
            except Exception as exc:
                delay = 30 * (2 ** notif.attempt_count)
                logger.warning(
                    "Error delivering reminder #%s (attempt %s): %s",
                    notification_id,
                    notif.attempt_count,
                    exc,
                )
                async with session_maker() as session:
                    await NotificationRepository(session).mark_failed(
                        notification_id,
                        str(exc),
                        retry_delay_seconds=delay,
                        max_attempts=actual_max_attempts,
                    )
                    await session.commit()

            if delivered:
                async with session_maker() as session:
                    await NotificationRepository(session).mark_sent(notification_id)
                    await session.commit()
                sent_count += 1
                logger.info(
                    "Sent reminder #%s for appointment #%s to telegram_id=%s",
                    notification_id,
                    app.id,
                    user.telegram_id,
                )

        except Exception as exc:
            logger.error("Unhandled error processing reminder #%s: %s", notification_id, exc, exc_info=True)

    return sent_count
