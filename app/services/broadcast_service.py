"""Broadcast engine service for marketing and informational mass mailings.

Implements strict tenant recipient filtering via master_clients, safe rate-limiting,
per-master status tracking, and multi-replica batch claiming via FOR UPDATE SKIP LOCKED.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional
import uuid

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiolimiter import AsyncLimiter
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.broadcast import (
    Broadcast,
    BroadcastRecipient,
    BroadcastStatus,
    RecipientStatus,
)
from app.database.models.master import MasterClient
from app.database.models.user import User
from app.services.exceptions import SubscriptionExpiredError
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.utils.delivery_lock import BROADCAST_LOCK_NAMESPACE, delivery_is_in_flight, delivery_lock
from app.config.settings import settings

logger = logging.getLogger("app.broadcast")


class BroadcastService:
    """Mass messaging service with tenant recipient isolation, claiming, and status tracking."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_eligible_users(
        self, master_id: int, segment: Optional[str] = None
    ) -> List[User]:
        """Get active users who have opted into marketing specifically for this master,
        optionally filtered by client segment (ALL, REGULAR, NEW, INACTIVE_30, INACTIVE_60).
        """
        now_utc = datetime.now(timezone.utc)
        thirty_days_ago = now_utc - timedelta(days=30)
        sixty_days_ago = now_utc - timedelta(days=60)

        query = (
            select(User)
            .join(MasterClient, MasterClient.user_id == User.id)
            .where(
                MasterClient.master_id == master_id,
                MasterClient.is_marketing_allowed.is_(True),
                MasterClient.is_bot_blocked.is_(False),
                User.telegram_id > 0,
            )
        )

        seg = (segment or "ALL").upper().strip()
        if seg == "REGULAR":
            reg_subq = (
                select(Appointment.user_id)
                .where(
                    Appointment.master_id == master_id,
                    Appointment.status == AppointmentStatus.COMPLETED,
                )
                .group_by(Appointment.user_id)
                .having(func.count(Appointment.id) >= 3)
            )
            query = query.where(User.id.in_(reg_subq))
        elif seg == "NEW":
            new_subq = (
                select(Appointment.user_id)
                .where(Appointment.master_id == master_id)
                .group_by(Appointment.user_id)
                .having(
                    or_(
                        func.min(Appointment.start_time) >= thirty_days_ago,
                        func.count(Appointment.id) == 1,
                    )
                )
            )
            query = query.where(User.id.in_(new_subq))
        elif seg in ("INACTIVE_30", "INACTIVE30"):
            inact30_subq = (
                select(Appointment.user_id)
                .where(Appointment.master_id == master_id)
                .group_by(Appointment.user_id)
                .having(func.max(Appointment.start_time) < thirty_days_ago)
            )
            query = query.where(User.id.in_(inact30_subq))
        elif seg in ("INACTIVE_60", "INACTIVE60"):
            inact60_subq = (
                select(Appointment.user_id)
                .where(Appointment.master_id == master_id)
                .group_by(Appointment.user_id)
                .having(func.max(Appointment.start_time) < sixty_days_ago)
            )
            query = query.where(User.id.in_(inact60_subq))

        query = query.order_by(User.id.asc())
        res = await self.session.execute(query)
        return list(res.scalars().all())

    async def count_recipients(self, master_id: int, segment: Optional[str] = None) -> int:
        """Count active users opted into marketing for this master and segment."""
        users = await self.get_eligible_users(master_id, segment=segment)
        return len(users)

    async def create_broadcast(
        self,
        master_id: int,
        text: str,
        admin_id: Optional[int] = None,
        photo_file_id: Optional[str] = None,
        button_text: Optional[str] = None,
        button_url: Optional[str] = None,
        target_segment: Optional[str] = "ALL",
    ) -> Broadcast:
        """Create a new broadcast campaign strictly belonging to master_id."""
        if not await SubscriptionAccessPolicy(self.session).can_send_marketing_broadcast(master_id):
            raise SubscriptionExpiredError("Маркетинговые рассылки недоступны при истекшей подписке.")

        eligible = await self.get_eligible_users(master_id, segment=target_segment)
        broadcast = Broadcast(
            master_id=master_id,
            admin_id=admin_id,
            text=text,
            photo_file_id=photo_file_id,
            button_text=button_text,
            button_url=button_url,
            target_segment=target_segment or "ALL",
            status=BroadcastStatus.DRAFT,
            total_count=len(eligible),
            success_count=0,
            fail_count=0,
        )
        self.session.add(broadcast)
        await self.session.flush()
        await self.session.refresh(broadcast)
        return broadcast

    async def reclaim_stale_recipients(
        self,
        broadcast_id: int,
        stale_timeout_minutes: int = 10,
        max_attempts: Optional[int] = None,
    ) -> int:
        """
        Reclaims recipients stuck in PROCESSING if a worker crashed.
        Resets them to PENDING so another worker can pick them up.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_timeout_minutes)
        stmt = (
            select(BroadcastRecipient)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.duplicate_of_id.is_(None),
                BroadcastRecipient.status == RecipientStatus.PROCESSING,
                or_(
                    BroadcastRecipient.claimed_at.is_(None),
                    BroadcastRecipient.claimed_at < cutoff,
                ),
            )
            .with_for_update(skip_locked=True)
        )
        res = await self.session.execute(stmt)
        stale = list(res.scalars().all())
        reclaimed_count = 0
        limit = max_attempts or settings.job_max_attempts
        for r in stale:
            if await delivery_is_in_flight(
                await self.session.connection(), BROADCAST_LOCK_NAMESPACE, r.id
            ):
                continue
            reclaimed_count += 1
            r.status = RecipientStatus.PENDING if r.attempt_count < limit else RecipientStatus.FAILED
            r.claimed_at = None
            r.claimed_by = None
            r.next_attempt_at = None
            r.error_message = "Abandoned delivery claim" if r.status == RecipientStatus.FAILED else None
        if reclaimed_count > 0:
            await self.session.commit()
            logger.warning(
                "Reclaimed %d stale recipients for broadcast #%d",
                reclaimed_count,
                broadcast_id,
            )
        return reclaimed_count

    async def queue_broadcast(self, master_id: int, broadcast_id: int) -> Broadcast:
        """Create durable recipient work in the caller-owned business transaction."""
        if not await SubscriptionAccessPolicy(self.session).can_send_marketing_broadcast(master_id):
            raise SubscriptionExpiredError("Маркетинговые рассылки недоступны при истекшей подписке.")
        result = await self.session.execute(
            select(Broadcast).where(
                Broadcast.id == broadcast_id,
                Broadcast.master_id == master_id,
            ).with_for_update()
        )
        broadcast = result.scalar_one_or_none()
        if broadcast is None:
            raise ValueError(f"Broadcast #{broadcast_id} not found for master {master_id}")
        if broadcast.status != BroadcastStatus.DRAFT:
            return broadcast
        recipients = await self.get_eligible_users(master_id, segment=broadcast.target_segment)
        broadcast.total_count = len(recipients)
        broadcast.status = BroadcastStatus.SENDING
        broadcast.started_at = datetime.now(timezone.utc)
        for user in recipients:
            await self.session.execute(
                pg_insert(BroadcastRecipient)
                .values(broadcast_id=broadcast_id, user_id=user.id, status=RecipientStatus.PENDING)
                .on_conflict_do_nothing()
            )
        await self.session.flush()
        return broadcast

    async def execute_broadcast(
        self,
        master_id: int,
        broadcast_id: int,
        bot: Optional[Bot] = None,
        registry: Optional[object] = None,
        worker_id: Optional[str] = None,
        batch_size: int = 50,
    ) -> Broadcast:
        """Send a campaign ensuring it strictly belongs to master_id with multi-replica claiming."""
        if not await SubscriptionAccessPolicy(self.session).can_send_marketing_broadcast(master_id):
            raise SubscriptionExpiredError("Маркетинговые рассылки недоступны при истекшей подписке.")

        broadcast = await self.queue_broadcast(master_id, broadcast_id)
        if broadcast.status not in (BroadcastStatus.DRAFT, BroadcastStatus.SENDING):
            await self.session.commit()
            return broadcast
        await self.session.commit()

        # Resolve tenant bot
        target_bot: Optional[Bot] = None
        if registry and hasattr(registry, "get_by_master_id"):
            try:
                target_bot = await registry.get_by_master_id(master_id)
            except Exception as exc:
                logger.warning("BotRegistry failed to get bot for master %s: %s", master_id, exc)
        if not target_bot:
            target_bot = bot

        if not target_bot:
            raise ValueError(
                f"No active Bot available for master #{master_id} to send broadcast #{broadcast_id}"
            )

        reply_markup = None
        if broadcast.button_text and broadcast.button_url:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text=broadcast.button_text,
                        url=broadcast.button_url,
                    )
                ]]
            )

        actual_worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        limiter = AsyncLimiter(25, 1.0)

        # Reclaim any stale PROCESSING recipients if another worker crashed
        await self.reclaim_stale_recipients(broadcast_id=broadcast_id)

        while True:
            # Claim one recipient at a time. A batch claimed before a slow
            # Telegram call can otherwise become stale while still queued in
            # this worker's memory and be reclaimed by another replica.
            claim_stmt = (
                select(BroadcastRecipient)
                .where(
                    BroadcastRecipient.broadcast_id == broadcast_id,
                    BroadcastRecipient.duplicate_of_id.is_(None),
                    BroadcastRecipient.status == RecipientStatus.PENDING,
                    or_(
                        BroadcastRecipient.next_attempt_at.is_(None),
                        BroadcastRecipient.next_attempt_at <= datetime.now(timezone.utc),
                    ),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            claim_res = await self.session.execute(claim_stmt)
            claimed = list(claim_res.scalars().all())
            if not claimed:
                break

            now_utc = datetime.now(timezone.utc)
            r = claimed[0]
            r.status = RecipientStatus.PROCESSING
            r.claimed_at = now_utc
            r.claimed_by = actual_worker_id
            r.attempt_count += 1
            r.next_attempt_at = None
            rcpt_id, user_id, claim_attempt = r.id, r.user_id, r.attempt_count

            await self.session.commit()

            user = await self.session.get(User, user_id)
            chat_id = user.telegram_id if user else None
            await self.session.commit()

            engine = self.session.bind
            async with delivery_lock(engine, BROADCAST_LOCK_NAMESPACE, rcpt_id) as acquired:
                if not acquired:
                    continue
                owned = await self.session.execute(
                    update(BroadcastRecipient).where(
                        BroadcastRecipient.id == rcpt_id,
                        BroadcastRecipient.status == RecipientStatus.PROCESSING,
                        BroadcastRecipient.claimed_by == actual_worker_id,
                        BroadcastRecipient.attempt_count == claim_attempt,
                    ).values(claimed_at=datetime.now(timezone.utc))
                )
                still_owned = owned.rowcount == 1
                await self.session.commit()
                if not still_owned:
                    continue

                delivered = False
                retry_delay: Optional[int] = None
                permanent_failure = False
                error_msg: Optional[str] = None
                if not chat_id or chat_id <= 0:
                    error_msg = "Invalid user or telegram_id"
                    permanent_failure = True
                else:
                    try:
                        async with limiter:
                            if broadcast.photo_file_id:
                                await target_bot.send_photo(
                                    chat_id=chat_id,
                                    photo=broadcast.photo_file_id,
                                    caption=broadcast.text,
                                    reply_markup=reply_markup,
                                )
                            else:
                                await target_bot.send_message(
                                    chat_id=chat_id,
                                    text=broadcast.text,
                                    reply_markup=reply_markup,
                                )
                        delivered = True
                    except TelegramRetryAfter as exc:
                        retry_delay = int(exc.retry_after) + 1
                        error_msg = "Telegram rate limited"
                    except TelegramForbiddenError:
                        error_msg = "Bot blocked by user"
                        permanent_failure = True
                    except Exception:
                        retry_delay = min(30 * (2 ** min(claim_attempt, 6)), 3600)
                        error_msg = "Telegram delivery error"

                retryable = not delivered and not permanent_failure and claim_attempt < settings.job_max_attempts
                result = await self.session.execute(
                    update(BroadcastRecipient).where(
                        BroadcastRecipient.id == rcpt_id,
                        BroadcastRecipient.status == RecipientStatus.PROCESSING,
                        BroadcastRecipient.claimed_by == actual_worker_id,
                        BroadcastRecipient.attempt_count == claim_attempt,
                    ).values(
                        status=(RecipientStatus.SENT if delivered else
                                RecipientStatus.PENDING if retryable else RecipientStatus.FAILED),
                        error_message=error_msg,
                        sent_at=datetime.now(timezone.utc) if delivered else None,
                        claimed_at=None,
                        claimed_by=None,
                        next_attempt_at=(datetime.now(timezone.utc) + timedelta(seconds=retry_delay or 30))
                        if retryable else None,
                    )
                )
                if result.rowcount == 1 and error_msg == "Bot blocked by user":
                    mc_res = await self.session.execute(
                        select(MasterClient).where(
                            MasterClient.master_id == master_id,
                            MasterClient.user_id == user_id,
                        )
                    )
                    mc = mc_res.scalars().first()
                    if mc:
                        mc.is_bot_blocked = True
                await self.session.commit()

        # Serialize final recounts across replicas. Without this row lock, one
        # worker can count 99 SENT recipients, another can commit the 100th,
        # and the first worker can overwrite the newer success_count with 99.
        locked = await self.session.execute(
            select(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.master_id == master_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        refreshed = locked.scalar_one()

        # Count after acquiring the lock so the last finishing worker always
        # records the complete committed recipient state.
        res_sent = await self.session.execute(
            select(func.count(BroadcastRecipient.id)).where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.duplicate_of_id.is_(None),
                BroadcastRecipient.status == RecipientStatus.SENT,
            )
        )
        success_count = res_sent.scalar() or 0

        res_fail = await self.session.execute(
            select(func.count(BroadcastRecipient.id)).where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.duplicate_of_id.is_(None),
                BroadcastRecipient.status == RecipientStatus.FAILED,
            )
        )
        fail_count = res_fail.scalar() or 0

        res_pending = await self.session.execute(
            select(func.count(BroadcastRecipient.id)).where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.duplicate_of_id.is_(None),
                BroadcastRecipient.status.in_([RecipientStatus.PENDING, RecipientStatus.PROCESSING]),
            )
        )
        pending_count = res_pending.scalar() or 0

        refreshed.success_count = success_count
        refreshed.fail_count = fail_count
        if pending_count == 0:
            refreshed.status = BroadcastStatus.COMPLETED
            refreshed.finished_at = datetime.now(timezone.utc)
        broadcast = refreshed
        await self.session.commit()

        logger.info(
            "Broadcast #%s completed for master %s: %s sent, %s failed",
            broadcast_id,
            master_id,
            success_count,
            fail_count,
        )
        return broadcast
