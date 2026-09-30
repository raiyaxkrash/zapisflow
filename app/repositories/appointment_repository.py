"""
Appointment repository for booking transactions, overlaps and queries.
"""

from datetime import datetime, timezone
from typing import Optional, Sequence
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import Payment
from app.repositories.base import BaseRepository


class AppointmentRepository(BaseRepository[Appointment]):
    """
    Repository for handling appointments and overlap checks.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Appointment, session)

    async def get_by_id_with_relations(
        self, appointment_id: int, *, for_update: bool = False
    ) -> Optional[Appointment]:
        """
        Fetch appointment by ID including user, service, payments and proofs.
        """
        query = (
            select(Appointment)
            .where(Appointment.id == appointment_id)
            .options(
                selectinload(Appointment.user),
                selectinload(Appointment.service),
                selectinload(Appointment.payments).selectinload(Payment.proofs),
            )
        )
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalars().first()

    async def expire_waiting_hold_if_due(self, appointment_id: int) -> bool:
        """Expire a due hold with one conditional update inside the write transaction."""
        now_utc = datetime.now(timezone.utc)
        result = await self.session.execute(
            update(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.status == AppointmentStatus.WAITING_PAYMENT,
                Appointment.hold_until.is_not(None),
                Appointment.hold_until <= now_utc,
            )
            .values(status=AppointmentStatus.EXPIRED, hold_until=None)
            .returning(Appointment.id)
        )
        return result.scalar_one_or_none() is not None

    async def get_active_overlapping(
        self,
        master_id: int,
        start_time: datetime,
        end_time_with_buffer: datetime,
        exclude_id: Optional[int] = None,
    ) -> Sequence[Appointment]:
        """
        Find any active appointments that overlap with [start_time, end_time_with_buffer).
        A waiting hold occupies its interval until the expiry worker commits
        EXPIRED. This matches the PostgreSQL exclusion constraint and ensures a slot
        is not offered while a still-WAITING_PAYMENT row exists.
        """
        active_statuses = [
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.PAYMENT_PROOF_SENT,
            AppointmentStatus.WAITING_PAYMENT,
        ]

        query = select(Appointment).where(
            Appointment.master_id == master_id,
            Appointment.status.in_(active_statuses),
            Appointment.start_time < end_time_with_buffer,
            Appointment.end_time_with_buffer > start_time,
        )

        if exclude_id:
            query = query.where(Appointment.id != exclude_id)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_active_for_range(
        self,
        master_id: int,
        start_datetime: datetime,
        end_datetime: datetime,
        exclude_id: Optional[int] = None,
    ) -> Sequence[Appointment]:
        """
        Get all active appointments within a date range for slot calculations.
        """
        active_statuses = [
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.PAYMENT_PROOF_SENT,
            AppointmentStatus.WAITING_PAYMENT,
        ]

        query = (
            select(Appointment)
            .where(
                Appointment.master_id == master_id,
                Appointment.status.in_(active_statuses),
                Appointment.start_time < end_datetime,
                Appointment.end_time_with_buffer > start_datetime,
            )
            .order_by(Appointment.start_time.asc())
        )
        if exclude_id is not None:
            query = query.where(Appointment.id != exclude_id)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_user_upcoming(self, user_id: int) -> Sequence[Appointment]:
        """
        Get upcoming active appointments for a client.
        """
        now = datetime.now(timezone.utc)
        query = (
            select(Appointment)
            .where(
                Appointment.user_id == user_id,
                Appointment.end_time >= now,
                Appointment.status.in_([
                    AppointmentStatus.CONFIRMED,
                    AppointmentStatus.PAYMENT_PROOF_SENT,
                    AppointmentStatus.WAITING_PAYMENT,
                ]),
            )
            .options(selectinload(Appointment.service))
            .order_by(Appointment.start_time.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_user_past(self, user_id: int, limit: int = 10) -> Sequence[Appointment]:
        """
        Get past or concluded appointments for a client.
        """
        now = datetime.now(timezone.utc)
        query = (
            select(Appointment)
            .where(
                Appointment.user_id == user_id,
                or_(
                    Appointment.end_time < now,
                    Appointment.status.in_([
                        AppointmentStatus.COMPLETED,
                        AppointmentStatus.CANCELLED_BY_CLIENT,
                        AppointmentStatus.CANCELLED_BY_ADMIN,
                        AppointmentStatus.NO_SHOW,
                        AppointmentStatus.EXPIRED,
                    ]),
                ),
            )
            .options(selectinload(Appointment.service))
            .order_by(Appointment.start_time.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_expired_holds(self) -> Sequence[Appointment]:
        """
        Get all appointments in WAITING_PAYMENT status where hold_until has passed.
        """
        now = datetime.now(timezone.utc)
        query = (
            select(Appointment)
            .where(
                Appointment.status == AppointmentStatus.WAITING_PAYMENT,
                Appointment.hold_until.is_not(None),
                Appointment.hold_until <= now,
            )
            .options(selectinload(Appointment.user))
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def list_for_admin(
        self,
        master_id: int = 1,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status: Optional[AppointmentStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Appointment]:
        """
        Filtered appointment list for the admin panel.
        """
        query = (
            select(Appointment)
            .where(Appointment.master_id == master_id)
            .options(
                selectinload(Appointment.user),
                selectinload(Appointment.service),
                selectinload(Appointment.payments),
            )
        )
        if date_from:
            query = query.where(Appointment.start_time >= date_from)
        if date_to:
            query = query.where(Appointment.start_time <= date_to)
        if status:
            query = query.where(Appointment.status == status)

        query = query.order_by(Appointment.start_time.asc()).limit(limit).offset(offset)
        result = await self.session.execute(query)
        return result.scalars().all()
