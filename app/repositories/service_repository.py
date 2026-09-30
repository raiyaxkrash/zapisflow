"""
Service repository for managing master offerings and pricing.
"""

from typing import Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.service import Service
from app.repositories.base import BaseRepository


class ServiceRepository(BaseRepository[Service]):
    """
    Repository for managing master's services.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Service, session)

    async def list_active(self, master_id: int = 1) -> Sequence[Service]:
        """
        List active, non-archived services ordered by display_order.
        """
        query = (
            select(Service)
            .where(
                Service.master_id == master_id,
                Service.is_active.is_(True),
                Service.is_archived.is_(False),
            )
            .order_by(Service.display_order.asc(), Service.id.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def list_all_for_admin(self, master_id: int = 1) -> Sequence[Service]:
        """
        List all services (including inactive and archived) for admin management.
        """
        query = (
            select(Service)
            .where(Service.master_id == master_id)
            .order_by(Service.is_archived.asc(), Service.display_order.asc(), Service.id.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def archive(self, service_id: int) -> bool:
        """
        Soft-delete / archive a service without deleting historical records.
        """
        service = await self.get_by_id(service_id)
        if not service:
            return False
        service.is_archived = True
        service.is_active = False
        await self.session.flush()
        return True

    async def unarchive(self, service_id: int) -> bool:
        """
        Restore an archived service.
        """
        service = await self.get_by_id(service_id)
        if not service:
            return False
        service.is_archived = False
        service.is_active = True
        await self.session.flush()
        return True

    async def toggle_active(self, service_id: int) -> Optional[bool]:
        """
        Toggle active status of a service.
        """
        service = await self.get_by_id(service_id)
        if not service:
            return None
        service.is_active = not service.is_active
        await self.session.flush()
        return service.is_active
