"""Master dynamic authorization and access control service (Phase 4).

Enforces strict tenant-scoped administrative permissions based on:
1. Master.owner_user_id (ultimate owner, independent of master_admins).
2. master_admins table (additional active tenant admins with roles).
"""

from enum import Enum
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.master import Master, MasterAdminRole
from app.repositories.master_admin_repository import MasterAdminRepository
from app.services.exceptions import AccessDeniedError


class AdminRole(str, Enum):
    """Domain representation of an administrative role within a specific master."""
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    NONE = "NONE"


class MasterAuthorizationService:
    """Service evaluating user permissions for a specific master."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.master_admin_repo = MasterAdminRepository(session)

    async def get_role(self, master_id: int, user_id: int) -> AdminRole:
        """Evaluate administrative role for a user within a specific master.
        
        1. Checks Master.owner_user_id directly (dynamic owner resolution).
        2. If not primary owner, checks active entry in master_admins.
        3. Returns AdminRole.NONE if user has no privileges.
        """
        master = await self.session.get(Master, master_id)
        if not master:
            return AdminRole.NONE

        # 1. Primary Owner check: always holds OWNER role
        if master.owner_user_id == user_id:
            return AdminRole.OWNER

        # 2. Master admins check
        admin_record = await self.master_admin_repo.get_admin(master_id, user_id)
        if admin_record and admin_record.is_active:
            if admin_record.role == MasterAdminRole.OWNER:
                return AdminRole.OWNER
            elif admin_record.role == MasterAdminRole.ADMIN:
                return AdminRole.ADMIN

        return AdminRole.NONE

    async def is_owner(self, master_id: int, user_id: int) -> bool:
        """Check whether the user is owner of the master."""
        return (await self.get_role(master_id, user_id)) == AdminRole.OWNER

    async def is_admin(self, master_id: int, user_id: int) -> bool:
        """Check whether the user has administrative privileges (OWNER or ADMIN)."""
        role = await self.get_role(master_id, user_id)
        return role in (AdminRole.OWNER, AdminRole.ADMIN)

    async def require_admin(self, master_id: int, user_id: int) -> AdminRole:
        """Enforce admin privileges. Raises AccessDeniedError if unauthorized."""
        role = await self.get_role(master_id, user_id)
        if role not in (AdminRole.OWNER, AdminRole.ADMIN):
            raise AccessDeniedError("Доступ разрешён только администраторам мастера")
        return role

    async def require_owner(self, master_id: int, user_id: int) -> AdminRole:
        """Enforce owner privileges. Raises AccessDeniedError if not owner."""
        role = await self.get_role(master_id, user_id)
        if role != AdminRole.OWNER:
            raise AccessDeniedError("Действие разрешено только главному владельцу мастера")
        return AdminRole.OWNER

    async def get_admin_recipients(self, master_id: int) -> List[int]:
        """Fetch list of Telegram IDs for all active administrators of this master."""
        return await self.master_admin_repo.list_active_admin_telegram_ids(master_id)
