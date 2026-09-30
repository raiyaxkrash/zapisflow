"""Broadcast engine service for marketing and informational mass mailings.

Implements strict tenant recipient filtering via master_clients, safe rate-limiting,
per-master status tracking, and multi-replica batch claiming via FOR UPDATE SKIP LOCKED.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional
import uuid

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiolimiter import AsyncLimiter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.broadcast import (
    Broadcast,
    BroadcastRecipient,
    BroadcastStatus,
    RecipientStatus,
)
from app.database.models.master import MasterClient
from app.database.models.user import User

logger = logging.getLogger("app.broadcast")


class BroadcastService:
    """Mass messaging service with tenant recipient isolation, claiming, and status tracking."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_eligible_users(self, master_id: int) -> List[User]:
        """Get active users who have opted into marketing specifically for this master.

        Reads exclusively from master_clients where master_id = master_id
        and is_marketing_allowed = TRUE and is_bot_blocked = FALSE.
        """
        query = (
            select(User)
            .join(MasterClient, MasterClient.user_id == User.id)
            .where(
                MasterClient.master_id == master_id,
                MasterClient.is_marketing_allowed.is_(True),
                MasterClient.is_bot_blocked.is_(False),
                User.telegram_id > 0,
            )
            .order_by(User.id.asc())
        )
        res = await self.session.execute(query)
        return list(res.scalars().all())

    async def count_recipients(self, master_id: int) -> int:
        """Count active users opted into marketing for this master."""
        users = await self.get_eligible_users(master_id)
        return len(users)

    async def create_broadcast(
        self,
        master_id: int,
        text: str,
        admin_id: Optional[int] = None,
        photo_file_id: Optional[str] = None,
        button_text: Optional[str] = None,
        button_url: Optional[str] = None,
    ) -> Broadcast:
        """Create a new broadcast campaign strictly belonging to master_id."""
        eligible = await self.get_eligible_users(master_id)
        broadcast = Broadcast(
            master_id=master_id,
            admin_id=admin_id,
            text=text,
            photo_file_id=photo_file_id,
            button_text=button_text,
            button_url=button_url,
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
                BroadcastRecipient.status == RecipientStatus.PROCESSING,
                BroadcastRecipient.claimed_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        )
        res = await self.session.execute(stmt)
        stale = list(res.scalars().all())
        reclaimed_count = len(stale)
        for r in stale:
            r.status = RecipientStatus.PENDING
            r.claimed_at = None
            r.claimed_by = None
        if reclaimed_count > 0:
            await self.session.commit()
            logger.warning(
                "Reclaimed %d stale recipients for broadcast #%d",
                reclaimed_count,
                broadcast_id,
            )
        return reclaimed_count

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
        query = select(Broadcast).where(
            Broadcast.id == broadcast_id,
            Broadcast.master_id == master_id,
        )
        res = await self.session.execute(query)
        broadcast = res.scalars().first()
        if not broadcast:
            raise ValueError(f"Broadcast #{broadcast_id} not found for master {master_id}")
        if broadcast.status not in (BroadcastStatus.DRAFT, BroadcastStatus.SENDING):
            await self.session.commit()
            return broadcast

        if broadcast.status == BroadcastStatus.DRAFT:
            recipients = await self.get_eligible_users(master_id)
            broadcast.total_count = len(recipients)
            broadcast.status = BroadcastStatus.SENDING
            broadcast.started_at = datetime.now(timezone.utc)
            for u in recipients:
                rcpt = BroadcastRecipient(
                    broadcast_id=broadcast_id,
                    user_id=u.id,
                    status=RecipientStatus.PENDING,
                )
                self.session.add(rcpt)
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
            # Atomic claim batch of recipients with FOR UPDATE SKIP LOCKED
            claim_stmt = (
                select(BroadcastRecipient)
                .where(
                    BroadcastRecipient.broadcast_id == broadcast_id,
                    BroadcastRecipient.status == RecipientStatus.PENDING,
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            claim_res = await self.session.execute(claim_stmt)
            claimed = list(claim_res.scalars().all())
            if not claimed:
                break

            now_utc = datetime.now(timezone.utc)
            batch_items = []
            for r in claimed:
                r.status = RecipientStatus.PROCESSING
                r.claimed_at = now_utc
                r.claimed_by = actual_worker_id
                r.attempt_count += 1
                batch_items.append((r.id, r.user_id))

            await self.session.commit()

            # Process claimed batch outside the claim transaction
            for rcpt_id, user_id in batch_items:
                user = await self.session.get(User, user_id)
                if not user or not user.telegram_id or user.telegram_id <= 0:
                    r = await self.session.get(BroadcastRecipient, rcpt_id)
                    if r:
                        r.status = RecipientStatus.FAILED
                        r.error_message = "Invalid user or telegram_id"
                        await self.session.commit()
                    continue

                async with limiter:
                    delivered = False
                    error_msg = None
                    for attempt in range(3):
                        try:
                            if broadcast.photo_file_id:
                                await target_bot.send_photo(
                                    chat_id=user.telegram_id,
                                    photo=broadcast.photo_file_id,
                                    caption=broadcast.text,
                                    reply_markup=reply_markup,
                                )
                            else:
                                await target_bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=broadcast.text,
                                    reply_markup=reply_markup,
                                )
                            delivered = True
                            break
                        except TelegramRetryAfter as exc:
                            logger.warning(
                                "Telegram rate limited broadcast #%s, sleeping %s s",
                                broadcast_id,
                                exc.retry_after,
                            )
                            await asyncio.sleep(exc.retry_after)
                        except TelegramForbiddenError:
                            error_msg = "Bot blocked by user"
                            break
                        except Exception as exc:
                            error_msg = str(exc)
                            break

                    r = await self.session.get(BroadcastRecipient, rcpt_id)
                    if r:
                        r.status = RecipientStatus.SENT if delivered else RecipientStatus.FAILED
                        r.error_message = error_msg
                        r.sent_at = datetime.now(timezone.utc) if delivered else None

                    if not delivered and error_msg == "Bot blocked by user":
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

        # Final recount and completion check
        res_sent = await self.session.execute(
            select(func.count(BroadcastRecipient.id)).where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == RecipientStatus.SENT,
            )
        )
        success_count = res_sent.scalar() or 0

        res_fail = await self.session.execute(
            select(func.count(BroadcastRecipient.id)).where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == RecipientStatus.FAILED,
            )
        )
        fail_count = res_fail.scalar() or 0

        res_pending = await self.session.execute(
            select(func.count(BroadcastRecipient.id)).where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status.in_([RecipientStatus.PENDING, RecipientStatus.PROCESSING]),
            )
        )
        pending_count = res_pending.scalar() or 0

        refreshed = await self.session.get(Broadcast, broadcast_id)
        if refreshed:
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
