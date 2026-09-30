"""Tenant-scoped MasterAdmin repository.

Manages administrative privileges, assigned roles and active statuses strictly
within a specific master_id.
"""

from typing import List, Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.master import Master, MasterAdmin, MasterAdminRole
from app.database.models.user import User


class MasterAdminRepository:
    """Repository for managing MasterAdmin entities strictly scoped to master_id."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_admin(self, master_id: int, user_id: int) -> Optional[MasterAdmin]:
        """Fetch admin relation for a master and user."""
        query = (
            select(MasterAdmin)
            .where(
                MasterAdmin.master_id == master_id,
                MasterAdmin.user_id == user_id,
            )
            .options(selectinload(MasterAdmin.user))
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_role(self, master_id: int, user_id: int) -> Optional[MasterAdminRole]:
        """Fetch role of active admin for a master."""
        admin = await self.get_admin(master_id, user_id)
        if admin and admin.is_active:
            return admin.role
        return None

    async def list_admins(
        self, master_id: int, only_active: bool = True
    ) -> Sequence[MasterAdmin]:
        """List all administrators for a specific master."""
        query = (
            select(MasterAdmin)
            .where(MasterAdmin.master_id == master_id)
            .options(selectinload(MasterAdmin.user))
            .order_by(MasterAdmin.created_at.asc())
        )
        if only_active:
            query = query.where(MasterAdmin.is_active.is_(True))
        result = await self.session.execute(query)
        return result.scalars().all()

    async def add_admin(
        self,
        master_id: int,
        user_id: int,
        role: MasterAdminRole | str = MasterAdminRole.ADMIN,
        created_by_user_id: Optional[int] = None,
    ) -> MasterAdmin:
        """Add or reactivate an administrator for a master."""
        if isinstance(role, str):
            role = MasterAdminRole(role)
        admin = await self.get_admin(master_id, user_id)
        if admin:
            admin.role = role
            admin.is_active = True
        else:
            admin = MasterAdmin(
                master_id=master_id,
                user_id=user_id,
                role=role,
                is_active=True,
            )
            self.session.add(admin)
        await self.session.flush()
        await self.session.refresh(admin)
        return admin

    async def deactivate_admin(self, master_id: int, user_id: int) -> bool:
        """Deactivate administrative privileges for a user at this master."""
        # Safety rule: Master owner cannot be deactivated via master_admins
        master = await self.session.get(Master, master_id)
        if master and master.owner_user_id == user_id:
            return False

        admin = await self.get_admin(master_id, user_id)
        if not admin or not admin.is_active:
            return False
        admin.is_active = False
        await self.session.flush()
        return True

    async def reactivate_admin(self, master_id: int, user_id: int) -> bool:
        """Reactivate previously deactivated administrator privileges."""
        admin = await self.get_admin(master_id, user_id)
        if not admin:
            return False
        admin.is_active = True
        await self.session.flush()
        return True

    async def list_active_admin_telegram_ids(self, master_id: int) -> List[int]:
        """Get Telegram IDs of all active administrators and the master owner."""
        telegram_ids: set[int] = set()

        # 1. Include master owner
        master = await self.session.get(Master, master_id)
        if master:
            owner_user = await self.session.get(User, master.owner_user_id)
            if owner_user and owner_user.telegram_id > 0:
                telegram_ids.add(owner_user.telegram_id)

        # 2. Include all active master_admins
        query = (
            select(User.telegram_id)
            .join(MasterAdmin, MasterAdmin.user_id == User.id)
            .where(
                MasterAdmin.master_id == master_id,
                MasterAdmin.is_active.is_(True),
                User.telegram_id > 0,
            )
        )
        result = await self.session.execute(query)
        for tg_id in result.scalars().all():
            telegram_ids.add(tg_id)

        return sorted(telegram_ids)
