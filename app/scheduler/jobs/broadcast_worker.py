"""Resume durable broadcast recipient work after webhook completion or a crash."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.broadcast import Broadcast, BroadcastRecipient, BroadcastStatus, RecipientStatus
from app.database.session import async_session_maker
from app.services.bot_registry import BotRegistry
from app.services.broadcast_service import BroadcastService

logger = logging.getLogger("app.scheduler.broadcast_worker")


def due_campaigns_query(now: datetime, stale_timeout_minutes: int = 10):
    """Skip campaigns waiting for retry while still finding abandoned work."""
    active = (
        (BroadcastRecipient.broadcast_id == Broadcast.id)
        & BroadcastRecipient.duplicate_of_id.is_(None)
    )
    due = exists(select(BroadcastRecipient.id).where(
        active,
        BroadcastRecipient.status == RecipientStatus.PENDING,
        or_(
            BroadcastRecipient.next_attempt_at.is_(None),
            BroadcastRecipient.next_attempt_at <= now,
        ),
    ))
    stale = exists(select(BroadcastRecipient.id).where(
        active,
        BroadcastRecipient.status == RecipientStatus.PROCESSING,
        or_(
            BroadcastRecipient.claimed_at.is_(None),
            BroadcastRecipient.claimed_at <= now - timedelta(minutes=stale_timeout_minutes),
        ),
    ))
    unfinished = exists(select(BroadcastRecipient.id).where(
        active,
        BroadcastRecipient.status.in_((RecipientStatus.PENDING, RecipientStatus.PROCESSING)),
    ))
    return select(Broadcast.id, Broadcast.master_id).where(
        Broadcast.status == BroadcastStatus.SENDING,
        or_(due, stale, ~unfinished),
    ).order_by(Broadcast.id)


async def resume_broadcasts(
    *,
    registry: Optional[BotRegistry] = None,
    bot: Optional[Bot] = None,
    session_maker: async_sessionmaker = async_session_maker,
    batch_size: int = 100,
) -> int:
    """Process campaigns with pending, retryable, or abandoned recipients.

    Individual recipient claims, not campaign claims, coordinate replicas.
    Each campaign can be resumed by the next scheduler tick after a crash.
    """
    async with session_maker() as session:
        rows = (await session.execute(
            due_campaigns_query(datetime.now(timezone.utc)).limit(batch_size)
        )).all()
    completed = 0
    for broadcast_id, master_id in rows:
        try:
            async with session_maker() as session:
                campaign = await BroadcastService(session).execute_broadcast(
                    master_id=master_id,
                    broadcast_id=broadcast_id,
                    bot=bot,
                    registry=registry,
                )
                if campaign.status == BroadcastStatus.COMPLETED:
                    completed += 1
        except Exception:
            logger.exception(
                "Broadcast resume failed: broadcast_id=%s master_id=%s",
                broadcast_id, master_id,
            )
    return completed
