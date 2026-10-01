"""Tenant-scoped MasterClient repository for CRM and marketing preferences.

Ensures that client profiles, notes, marketing opt-ins and stats are strictly
isolated per master_id.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import MasterClient
from app.database.models.user import User


class MasterClientRepository:
    """Repository for managing client relations scoped to a specific master."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_client(self, master_id: int, user_id: int) -> Optional[MasterClient]:
        """Fetch client record for a master."""
        query = (
            select(MasterClient)
            .where(MasterClient.master_id == master_id, MasterClient.user_id == user_id)
            .options(selectinload(MasterClient.user))
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_or_create(
        self,
        master_id: int,
        user_id: int,
        is_marketing_allowed: bool = False,
    ) -> MasterClient:
        """Fetch existing client relation or create a new one with default marketing=False."""
        client = await self.get_client(master_id, user_id)
        if not client:
            client = MasterClient(
                master_id=master_id,
                user_id=user_id,
                is_marketing_allowed=is_marketing_allowed,
                is_bot_blocked=False,
            )
            self.session.add(client)
            await self.session.flush()
            await self.session.refresh(client)
        return client

    async def set_bot_blocked(self, master_id: int, user_id: int, is_blocked: bool) -> None:
        """Set bot delivery blocked flag strictly for this master's bot."""
        client = await self.get_or_create(master_id, user_id)
        client.is_bot_blocked = is_blocked
        await self.session.flush()

    async def set_marketing_allowed(self, master_id: int, user_id: int, is_allowed: bool) -> None:
        """Set marketing consent for a client at this specific master."""
        client = await self.get_or_create(master_id, user_id)
        client.is_marketing_allowed = is_allowed
        await self.session.flush()

    async def update_notes(self, master_id: int, user_id: int, notes: Optional[str]) -> None:
        """Update master-specific notes about the client."""
        client = await self.get_client(master_id, user_id)
        if client is None:
            raise LookupError("Client does not belong to current master")
        client.notes = notes
        await self.session.flush()

    async def search_clients(
        self, master_id: int, search_text: str, limit: int = 20
    ) -> Sequence[Tuple[MasterClient, User]]:
        """Search clients belonging only to this master."""
        pattern = f"%{search_text.strip()}%"
        query = (
            select(MasterClient, User)
            .join(User, MasterClient.user_id == User.id)
            .where(
                MasterClient.master_id == master_id,
                or_(
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                    User.username.ilike(pattern),
                    User.phone.ilike(pattern),
                ),
            )
            .order_by(User.last_activity_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return result.all()

    async def list_clients(
        self, master_id: int, limit: int = 50, offset: int = 0
    ) -> Sequence[Tuple[MasterClient, User]]:
        """List all clients of this master."""
        query = (
            select(MasterClient, User)
            .join(User, MasterClient.user_id == User.id)
            .where(MasterClient.master_id == master_id)
            .order_by(MasterClient.last_visit_at.desc().nullslast(), User.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return result.all()

    async def get_client_crm_stats(self, master_id: int, user_id: int) -> Dict[str, Any]:
        """Calculate CRM metrics for a user strictly within this master's appointments."""
        query = (
            select(
                func.count(Appointment.id).label("total_bookings"),
                func.count().filter(Appointment.status == AppointmentStatus.COMPLETED).label("completed"),
                func.count().filter(
                    Appointment.status.in_([
                        AppointmentStatus.CANCELLED_BY_CLIENT,
                        AppointmentStatus.CANCELLED_BY_ADMIN,
                    ])
                ).label("cancelled"),
                func.count().filter(Appointment.status == AppointmentStatus.NO_SHOW).label("no_show"),
                func.coalesce(
                    func.sum(Appointment.snapshot_service_price).filter(
                        Appointment.status == AppointmentStatus.COMPLETED
                    ),
                    Decimal("0.00"),
                ).label("total_spent"),
                func.min(Appointment.start_time).label("first_booking_at"),
                func.max(Appointment.start_time).label("last_booking_at"),
            )
            .where(Appointment.master_id == master_id, Appointment.user_id == user_id)
        )
        result = await self.session.execute(query)
        row = result.one()
        return {
            "total_bookings": row.total_bookings or 0,
            "completed": row.completed or 0,
            "cancelled": row.cancelled or 0,
            "no_show": row.no_show or 0,
            "total_spent": row.total_spent or Decimal("0.00"),
            "first_booking_at": row.first_booking_at,
            "last_booking_at": row.last_booking_at,
        }
