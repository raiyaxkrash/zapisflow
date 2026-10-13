"""Recover successful YooKassa payments if a webhook was delayed or lost."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.database.models.subscription import SubscriptionPayment
from app.services.billing.yookassa_checkout import (
    PROVIDER_CODE,
    UNCREATED_PREFIX,
    YooKassaCheckoutService,
)
from app.services.billing.yookassa_client import YooKassaClient, YooKassaGatewayError
from app.services.exceptions import SubscriptionError


logger = logging.getLogger(__name__)


async def reconcile_pending_yookassa(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    batch_size: int = 100,
    client: YooKassaClient | None = None,
) -> int:
    """Check a bounded batch; subscription row locks make replicas idempotent."""
    if not settings.uses_yookassa:
        return 0
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if client is None:
        shop_id, secret_key = settings.yookassa_credentials
        client = YooKassaClient(shop_id, secret_key)

    now = datetime.now(timezone.utc)
    retry_before = now - timedelta(seconds=settings.yookassa_reconciliation_interval_seconds)
    async with session_maker.begin() as session:
        pending_rows = (await session.scalars(
            select(SubscriptionPayment)
            .where(
                SubscriptionPayment.provider == PROVIDER_CODE,
                SubscriptionPayment.status == "PENDING",
                SubscriptionPayment.provider_payment_id.not_like(UNCREATED_PREFIX + "%"),
                or_(
                    SubscriptionPayment.last_reconciled_at.is_(None),
                    SubscriptionPayment.last_reconciled_at < retry_before,
                ),
            )
            .order_by(
                SubscriptionPayment.last_reconciled_at.asc().nullsfirst(),
                SubscriptionPayment.created_at,
                SubscriptionPayment.id,
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )).all()
        pending = [row.provider_payment_id for row in pending_rows]
        for row in pending_rows:
            row.last_reconciled_at = now

    service = YooKassaCheckoutService(session_maker, client)
    completed = 0
    for provider_payment_id in pending:
        try:
            if await service.reconcile(provider_payment_id):
                completed += 1
        except (YooKassaGatewayError, SubscriptionError, ValueError):
            # The row remains PENDING for a later attempt or operator review.
            # Provider errors and payment metadata are deliberately not logged.
            logger.warning("YooKassa reconciliation failed for a pending local order")
    return completed
