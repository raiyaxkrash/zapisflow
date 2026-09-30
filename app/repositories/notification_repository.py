"""Repository for scheduled appointment reminder notifications with atomic multi-replica claiming."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional, Sequence
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.notification import (
    Notification,
    NotificationStatus,
    NotificationType,
)
from app.repositories.base import BaseRepository

logger = logging.getLogger("app.repositories.notification")


class NotificationRepository(BaseRepository[Notification]):
    """Notification repository handling idempotent creation, claiming, and lifecycle state."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Notification, session)

    async def get_by_id(self, notification_id: int) -> Optional[Notification]:
        """Fetch notification by primary key."""
        return await self.session.get(Notification, notification_id)

    async def get_by_id_with_relations(self, notification_id: int) -> Optional[Notification]:
        """Fetch notification by ID with eager-loaded appointment and user."""
        from app.database.models.appointment import Appointment
        stmt = (
            select(Notification)
            .where(Notification.id == notification_id)
            .options(
                selectinload(Notification.appointment).selectinload(Appointment.user),
            )
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def create_or_ignore(
        self,
        appointment_id: int,
        notification_type: NotificationType,
        scheduled_at: datetime,
    ) -> Optional[Notification]:
        """Idempotently insert a pending notification using ON CONFLICT DO NOTHING.

        Relies on unique constraint uq_notifications_appointment_type (appointment_id, type).
        Returns the Notification if created, or None if it already existed.
        """
        stmt = (
            pg_insert(Notification)
            .values(
                appointment_id=appointment_id,
                type=notification_type,
                scheduled_at=scheduled_at,
                status=NotificationStatus.PENDING,
                attempt_count=0,
            )
            .on_conflict_do_nothing(
                index_elements=["appointment_id", "type"]
            )
            .returning(Notification.id)
        )
        res = await self.session.execute(stmt)
        new_id = res.scalar_one_or_none()
        if new_id is not None:
            return await self.session.get(Notification, new_id)
        return None

    async def claim_due_notifications(
        self,
        worker_id: str,
        batch_size: int = 100,
        max_attempts: int = 3,
    ) -> Sequence[Notification]:
        """Atomically find, lock (SKIP LOCKED), and claim due notifications.

        Finds rows where:
        1. status = PENDING and scheduled_at <= now
        OR
        2. status = PROCESSING and next_attempt_at <= now and attempt_count < max_attempts

        Updates status to PROCESSING, records claimed_at, claimed_by, and increments attempt_count.
        """
        now_utc = datetime.now(timezone.utc)
        stmt = (
            select(Notification)
            .where(
                or_(
                    and_(
                        Notification.status == NotificationStatus.PENDING,
                        Notification.scheduled_at <= now_utc,
                    ),
                    and_(
                        Notification.status == NotificationStatus.PROCESSING,
                        Notification.next_attempt_at.is_not(None),
                        Notification.next_attempt_at <= now_utc,
                        Notification.attempt_count < max_attempts,
                    ),
                )
            )
            .order_by(Notification.scheduled_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        res = await self.session.execute(stmt)
        claimed = list(res.scalars().all())

        for notif in claimed:
            notif.status = NotificationStatus.PROCESSING
            notif.claimed_at = now_utc
            notif.claimed_by = worker_id
            notif.attempt_count += 1
            notif.next_attempt_at = None

        await self.session.flush()
        return claimed

    async def reclaim_stale_processing(
        self,
        timeout_seconds: int = 300,
        max_attempts: int = 3,
    ) -> int:
        """Find stale PROCESSING notifications where worker crashed or timed out.

        If attempt_count < max_attempts:
            Resets to PENDING for retry.
        Else:
            Marks as FAILED.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        stmt = (
            select(Notification)
            .where(
                Notification.status == NotificationStatus.PROCESSING,
                Notification.claimed_at.is_not(None),
                Notification.claimed_at <= cutoff,
                Notification.next_attempt_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        res = await self.session.execute(stmt)
        stale = list(res.scalars().all())

        for notif in stale:
            if notif.attempt_count < max_attempts:
                notif.status = NotificationStatus.PENDING
                notif.claimed_at = None
                notif.claimed_by = None
                notif.next_attempt_at = None
                notif.last_error = f"Reclaimed from stale processing (exceeded {timeout_seconds}s)"
            else:
                notif.status = NotificationStatus.FAILED
                notif.last_error = f"Abandoned: max attempts ({max_attempts}) reached after timeout"

        await self.session.flush()
        return len(stale)

    async def mark_sent(self, notification_id: int) -> None:
        """Mark notification as successfully delivered."""
        stmt = (
            update(Notification)
            .where(Notification.id == notification_id)
            .values(
                status=NotificationStatus.SENT,
                sent_at=datetime.now(timezone.utc),
                next_attempt_at=None,
                last_error=None,
            )
        )
        await self.session.execute(stmt)

    async def mark_failed(
        self,
        notification_id: int,
        error: str,
        retry_delay_seconds: Optional[int] = None,
        max_attempts: int = 3,
    ) -> None:
        """Mark notification delivery failure, scheduling retry if within limits."""
        notif = await self.session.get(Notification, notification_id)
        if not notif:
            return

        notif.last_error = error[:1000] if error else None
        if retry_delay_seconds is not None and notif.attempt_count < max_attempts:
            notif.status = NotificationStatus.PROCESSING
            notif.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
        else:
            notif.status = NotificationStatus.FAILED
            notif.next_attempt_at = None

        await self.session.flush()

    async def mark_cancelled(self, notification_id: int, reason: Optional[str] = None) -> None:
        """Mark notification as cancelled (e.g. appointment cancelled/rescheduled)."""
        stmt = (
            update(Notification)
            .where(Notification.id == notification_id)
            .values(
                status=NotificationStatus.CANCELLED,
                last_error=reason[:1000] if reason else None,
                next_attempt_at=None,
            )
        )
        await self.session.execute(stmt)

    async def cancel_pending_for_appointment(
        self, appointment_id: int, reason: str = "Appointment status changed"
    ) -> int:
        """Cancel any PENDING or waiting PROCESSING notifications for a given appointment."""
        stmt = (
            update(Notification)
            .where(
                Notification.appointment_id == appointment_id,
                Notification.status.in_([NotificationStatus.PENDING, NotificationStatus.PROCESSING]),
            )
            .values(
                status=NotificationStatus.CANCELLED,
                last_error=reason,
                next_attempt_at=None,
            )
        )
        res = await self.session.execute(stmt)
        return res.rowcount

    async def handle_appointment_rescheduled(
        self, appointment_id: int, new_start_time: datetime
    ) -> None:
        """Recalculate or reset reminder schedules when an appointment is rescheduled.

        Guarantees:
        - Outdated scheduled_at is discarded.
        - PENDING/PROCESSING reminders are reset with recalculated scheduled_at based on new_start_time.
        - SENT reminders from past dates are safely reset to PENDING if new_start_time is in the future.
        - If new_start_time is too close, expired reminder windows are set to CANCELLED.
        - Metadata fields (attempt_count, claimed_at, claimed_by, next_attempt_at, last_error, sent_at)
          are cleanly reset.
        """
        now_utc = datetime.now(timezone.utc)
        if new_start_time.tzinfo is None:
            new_start_time = new_start_time.replace(tzinfo=timezone.utc)

        time_until = new_start_time - now_utc

        stmt = select(Notification).where(Notification.appointment_id == appointment_id)
        res = await self.session.execute(stmt)
        notifs = list(res.scalars().all())

        for notif in notifs:
            if notif.type == NotificationType.REMINDER_24H:
                if time_until > timedelta(hours=24):
                    notif.scheduled_at = new_start_time - timedelta(hours=24)
                    notif.status = NotificationStatus.PENDING
                    notif.attempt_count = 0
                    notif.claimed_at = None
                    notif.claimed_by = None
                    notif.next_attempt_at = None
                    notif.sent_at = None
                    notif.last_error = None
                elif timedelta(hours=3) < time_until <= timedelta(hours=24):
                    notif.scheduled_at = now_utc
                    notif.status = NotificationStatus.PENDING
                    notif.attempt_count = 0
                    notif.claimed_at = None
                    notif.claimed_by = None
                    notif.next_attempt_at = None
                    notif.sent_at = None
                    notif.last_error = None
                else:
                    notif.status = NotificationStatus.CANCELLED
                    notif.last_error = "24h window passed after reschedule"
                    notif.claimed_at = None
                    notif.claimed_by = None
                    notif.next_attempt_at = None

            elif notif.type == NotificationType.REMINDER_3H:
                if time_until > timedelta(hours=3):
                    notif.scheduled_at = new_start_time - timedelta(hours=3)
                    notif.status = NotificationStatus.PENDING
                    notif.attempt_count = 0
                    notif.claimed_at = None
                    notif.claimed_by = None
                    notif.next_attempt_at = None
                    notif.sent_at = None
                    notif.last_error = None
                elif timedelta(seconds=0) < time_until <= timedelta(hours=3):
                    notif.scheduled_at = now_utc
                    notif.status = NotificationStatus.PENDING
                    notif.attempt_count = 0
                    notif.claimed_at = None
                    notif.claimed_by = None
                    notif.next_attempt_at = None
                    notif.sent_at = None
                    notif.last_error = None
                else:
                    notif.status = NotificationStatus.CANCELLED
                    notif.last_error = "3h window passed after reschedule"
                    notif.claimed_at = None
                    notif.claimed_by = None
                    notif.next_attempt_at = None

        await self.session.flush()

