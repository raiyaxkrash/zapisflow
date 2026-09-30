"""
Scheduled background job for reliable subscription status expiration across multiple replicas.
"""

from datetime import datetime, timezone
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.session import async_session_maker
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger("app.scheduler.subscription_worker")


async def refresh_subscriptions(
    session_maker: async_sessionmaker = async_session_maker,
    batch_size: int = 100,
    now_utc: Optional[datetime] = None,
) -> int:
    """
    Find TRIAL and ACTIVE master subscriptions whose duration has elapsed and mark them EXPIRED.
    Multi-replica safe via PostgreSQL FOR UPDATE SKIP LOCKED.
    """
    async with session_maker() as session:
        try:
            svc = SubscriptionService(session)
            count = await svc.refresh_expired_subscriptions(
                batch_size=batch_size,
                now_utc=now_utc,
            )
            await session.commit()
            if count > 0:
                logger.info("Successfully refreshed %d expired subscriptions to EXPIRED", count)
            return count
        except Exception as exc:
            await session.rollback()
            logger.error("Failed to refresh expired subscriptions: %s", exc, exc_info=True)
            return 0
