"""Repository for Master tenant entities."""

from collections.abc import Sequence
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.master import Master, MasterStatus, SubscriptionStatus


class MasterRepository:
    """Repository for querying and managing Master entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, master_id: int) -> Optional[Master]:
        """Fetch master by its primary key ID."""
        return await self.session.get(Master, master_id)

    async def list_by_owner_id(self, owner_user_id: int) -> Sequence[Master]:
        """List all masters owned by a user."""
        stmt = select(Master).where(Master.owner_user_id == owner_user_id).order_by(Master.id.asc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create_master(
        self,
        owner_user_id: int,
        display_name: str,
        status: MasterStatus = MasterStatus.ACTIVE,
        subscription_status: SubscriptionStatus = SubscriptionStatus.TRIAL,
        timezone: str = "Europe/Moscow",
    ) -> Master:
        """Create and persist a new Master entity."""
        master = Master(
            owner_user_id=owner_user_id,
            display_name=display_name,
            status=status,
            subscription_status=subscription_status,
            timezone=timezone,
        )
        self.session.add(master)
        await self.session.flush()
        await self.session.refresh(master)
        return master
