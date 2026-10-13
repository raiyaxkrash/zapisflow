"""Appointment repository for booking transactions, overlaps and queries with strict tenant isolation."""

from datetime import datetime, timezone
from typing import Optional, Sequence
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import Payment
from app.repositories.base import BaseRepository


class AppointmentRepository(BaseRepository[Appointment]):
    """Repository for handling appointments and overlap checks strictly scoped to master_id."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Appointment, session)

    async def get_by_id(self, appointment_id: int, master_id: int) -> Optional[Appointment]:
        """Fetch appointment ensuring it belongs to the given master."""
        query = select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.master_id == master_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_by_id_with_relations(
        self, appointment_id: int, master_id: int, *, for_update: bool = False
    ) -> Optional[Appointment]:
        """Fetch appointment by ID and master_id including user, service, payments and proofs."""
        query = (
            select(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.master_id == master_id,
            )
            .options(
                selectinload(Appointment.user),
                selectinload(Appointment.staff),
                selectinload(Appointment.service),
                selectinload(Appointment.payments).selectinload(Payment.proofs),
            )
        )
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalars().first()

    async def expire_waiting_hold_if_due(
        self, appointment_id: int, master_id: Optional[int] = None
    ) -> bool:
        """Expire a due hold with one conditional update inside the write transaction."""
        now_utc = datetime.now(timezone.utc)
        stmt = (
            update(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.status == AppointmentStatus.WAITING_PAYMENT,
                Appointment.hold_until.is_not(None),
                Appointment.hold_until <= now_utc,
            )
        )
        if master_id is not None:
            stmt = stmt.where(Appointment.master_id == master_id)
        stmt = stmt.values(status=AppointmentStatus.EXPIRED, hold_until=None).returning(Appointment.id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def claim_and_expire_holds(
        self, batch_size: int = 100
    ) -> Sequence[Appointment]:
        """Atomically find, lock (SKIP LOCKED), and expire due WAITING_PAYMENT appointments.

        Strict safety rules:
        - Only locks appointments where status == WAITING_PAYMENT and hold_until <= now.
        - Never touches PAYMENT_PROOF_SENT (protected from automatic expiration).
        - Multiple concurrent workers skip each other's locked rows without blocking.
        - Updates status to EXPIRED and returns appointments for external notification.
        """
        now_utc = datetime.now(timezone.utc)
        candidate_ids_stmt = (
            select(Appointment.id)
            .where(
                Appointment.status == AppointmentStatus.WAITING_PAYMENT,
                Appointment.hold_until.is_not(None),
                Appointment.hold_until <= now_utc,
            )
            .order_by(Appointment.hold_until.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        res = await self.session.execute(candidate_ids_stmt)
        candidate_ids = list(res.scalars().all())
        if not candidate_ids:
            return []

        # Load appointments with relations for notification dispatching
        query = (
            select(Appointment)
            .where(Appointment.id.in_(candidate_ids))
            .options(selectinload(Appointment.user))
        )
        appointments_res = await self.session.execute(query)
        appointments = list(appointments_res.scalars().all())

        for app in appointments:
            app.status = AppointmentStatus.EXPIRED
            app.hold_until = None

        await self.session.flush()
        return appointments

    async def get_active_overlapping(
        self,
        master_id: int,
        start_time: datetime,
        end_time_with_buffer: datetime,
        staff_id: Optional[int] = None,
        exclude_id: Optional[int] = None,
    ) -> Sequence[Appointment]:
        """Find any active appointments for master_id and optional staff_id that overlap with [start_time, end_time_with_buffer)."""
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

        if staff_id is not None:
            query = query.where(Appointment.staff_id == staff_id)

        if exclude_id is not None:
            query = query.where(Appointment.id != exclude_id)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_active_for_range(
        self,
        master_id: int,
        start_datetime: datetime,
        end_datetime: datetime,
        staff_id: Optional[int] = None,
        exclude_id: Optional[int] = None,
    ) -> Sequence[Appointment]:
        """Get all active appointments for master_id and optional staff_id within a date range for slot calculations."""
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
        if staff_id is not None:
            query = query.where(Appointment.staff_id == staff_id)
        if exclude_id is not None:
            query = query.where(Appointment.id != exclude_id)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_user_upcoming(self, user_id: int, master_id: int) -> Sequence[Appointment]:
        """Get upcoming active appointments for a client strictly within the current master's bot."""
        now = datetime.now(timezone.utc)
        query = (
            select(Appointment)
            .where(
                Appointment.user_id == user_id,
                Appointment.master_id == master_id,
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

    async def get_user_past(
        self, user_id: int, master_id: int, limit: int = 10
    ) -> Sequence[Appointment]:
        """Get past or concluded appointments for a client strictly within the current master's bot."""
        now = datetime.now(timezone.utc)
        query = (
            select(Appointment)
            .where(
                Appointment.user_id == user_id,
                Appointment.master_id == master_id,
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

    async def get_expired_holds(
        self, master_id: Optional[int] = None
    ) -> Sequence[Appointment]:
        """Get all appointments in WAITING_PAYMENT status where hold_until has passed."""
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
        if master_id is not None:
            query = query.where(Appointment.master_id == master_id)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def list_for_admin(
        self,
        master_id: int,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status: Optional[AppointmentStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Appointment]:
        """Filtered appointment list for the admin panel of a specific master."""
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
