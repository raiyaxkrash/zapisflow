"""Platform admin mutations remain owned by the webhook middleware transaction."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.database.models.subscription import SubscriptionPlan
from app.database.models.user import User
from app.services.platform_admin_service import PlatformAdminService
from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.asyncio
async def test_plan_toggle_rolls_back_with_outer_transaction(pg_engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    code = "tx_" + uuid.uuid4().hex[:20]
    async with sessions.begin() as session:
        user = User(telegram_id=8_000_000_000 + uuid.uuid4().int % 1_000_000_000,
                    first_name="Plan Admin")
        plan = SubscriptionPlan(code=code, name="Transaction Test", price=Decimal("499"),
                                currency="RUB", period_days=30, is_active=True)
        session.add_all([user, plan])
        await session.flush()
        user_id, plan_id = user.id, plan.id

    try:
        async with sessions() as session:
            ok, _ = await PlatformAdminService(session).toggle_plan_active(plan_id, user_id)
            assert ok
            await session.rollback()
        async with sessions() as session:
            persisted = await session.scalar(select(SubscriptionPlan.is_active).where(
                SubscriptionPlan.id == plan_id
            ))
            assert persisted is True
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
            await session.execute(delete(User).where(User.id == user_id))
