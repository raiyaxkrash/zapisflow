"""
Central transitional compatibility provider for single-tenant / Master #1 execution.
Used during Phase 2 to isolate runtime assumptions before Phase 3 multi-tenant routing.
"""

import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.master import Master, MasterSettings, MasterStatus, SubscriptionStatus

logger = logging.getLogger(__name__)

DEFAULT_MASTER_ID: int = 1


class DefaultMasterProvider:
    """
    Central provider resolving the default tenant during transitional phases.
    Encapsulates all fallback logic so repositories and handlers do not hardcode defaults.
    """

    @staticmethod
    def get_default_master_id() -> int:
        """Returns the canonical fallback master ID for legacy single-tenant operations."""
        return DEFAULT_MASTER_ID

    @staticmethod
    async def get_default_master(session: AsyncSession) -> Optional[Master]:
        """Fetch the primary default master entity from database if provisioned."""
        stmt = select(Master).where(Master.id == DEFAULT_MASTER_ID)
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def ensure_default_master(
        session: AsyncSession, owner_user_id: int, display_name: str = "Студия красоты"
    ) -> Master:
        """
        Idempotently ensure Master #1 exists with associated MasterSettings.
        """
        master = await DefaultMasterProvider.get_default_master(session)
        if master is not None:
            return master

        master = Master(
            id=DEFAULT_MASTER_ID,
            owner_user_id=owner_user_id,
            display_name=display_name,
            status=MasterStatus.ACTIVE,
            subscription_status=SubscriptionStatus.ACTIVE,
            timezone="Europe/Moscow",
        )
        session.add(master)
        await session.flush()

        settings = MasterSettings(
            master_id=master.id,
            hold_duration_minutes=30,
            cancel_policy_hours=24,
            booking_horizon_days=30,
            min_advance_hours=2,
            grid_step_minutes=30,
            default_buffer_minutes=15,
        )
        session.add(settings)
        await session.flush()
        logger.info("Provisioned default Master #%s with owner user #%s", master.id, owner_user_id)
        return master


# Backward compatibility alias
LegacyTenantResolver = DefaultMasterProvider
