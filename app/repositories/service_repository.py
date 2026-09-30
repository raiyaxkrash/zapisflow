"""Service repository for managing master offerings and pricing strictly scoped to a master_id."""

from typing import Any, Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.service import Service
from app.repositories.base import BaseRepository


class ServiceRepository(BaseRepository[Service]):
    """Repository for managing master's services with strict tenant isolation."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Service, session)

    async def get_by_id(self, service_id: int, master_id: int) -> Optional[Service]:
        """Fetch service by ID strictly ensuring it belongs to the given master."""
        query = select(Service).where(
            Service.id == service_id,
            Service.master_id == master_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def list_active(self, master_id: int) -> Sequence[Service]:
        """List active, non-archived services for a specific master."""
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

    async def list_all_for_admin(self, master_id: int) -> Sequence[Service]:
        """List all services for admin panel of a specific master."""
        query = (
            select(Service)
            .where(Service.master_id == master_id)
            .order_by(Service.is_archived.asc(), Service.display_order.asc(), Service.id.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def create_service(self, master_id: int, **kwargs: Any) -> Service:
        """Create a service strictly assigned to the given master."""
        service = Service(master_id=master_id, **kwargs)
        self.session.add(service)
        await self.session.flush()
        await self.session.refresh(service)
        return service

    async def update_service(
        self, service_id: int, master_id: int, **kwargs: Any
    ) -> Optional[Service]:
        """Update service fields ensuring tenant ownership."""
        service = await self.get_by_id(service_id, master_id)
        if not service:
            return None
        for key, value in kwargs.items():
            if hasattr(service, key) and key not in {"id", "master_id"}:
                setattr(service, key, value)
        await self.session.flush()
        await self.session.refresh(service)
        return service

    async def archive(self, service_id: int, master_id: int) -> bool:
        """Soft-delete / archive a service without deleting historical records."""
        service = await self.get_by_id(service_id, master_id)
        if not service:
            return False
        service.is_archived = True
        service.is_active = False
        await self.session.flush()
        return True

    async def unarchive(self, service_id: int, master_id: int) -> bool:
        """Restore an archived service for this master."""
        service = await self.get_by_id(service_id, master_id)
        if not service:
            return False
        service.is_archived = False
        service.is_active = True
        await self.session.flush()
        return True

    async def toggle_active(self, service_id: int, master_id: int) -> Optional[bool]:
        """Toggle active status of a service."""
        service = await self.get_by_id(service_id, master_id)
        if not service:
            return None
        service.is_active = not service.is_active
        await self.session.flush()
        return service.is_active
