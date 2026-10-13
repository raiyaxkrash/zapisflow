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

    async def list_accessible_for_user(self, user_id: int) -> Sequence[Master]:
        """List all masters where user is either the owner, an admin, or an active staff member."""
        from app.database.models.master import MasterAdmin
        from app.database.models.staff import StaffMember
        from sqlalchemy import or_

        admin_subq = (
            select(MasterAdmin.master_id)
            .where(MasterAdmin.user_id == user_id, MasterAdmin.is_active.is_(True))
        )
        staff_subq = (
            select(StaffMember.master_id)
            .where(StaffMember.user_id == user_id, StaffMember.is_active.is_(True))
        )

        stmt = (
            select(Master)
            .where(
                or_(
                    Master.owner_user_id == user_id,
                    Master.id.in_(admin_subq),
                    Master.id.in_(staff_subq),
                )
            )
            .order_by(Master.id.asc())
        )
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
