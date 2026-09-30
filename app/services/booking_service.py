"""
Booking service handling appointment lifecycle, holds, snapshotting and cancellations.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple
import pytz
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import Payment, PaymentStatus
from app.database.models.service import DepositType, Service
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.settings_repository import SettingsRepository
from app.repositories.user_repository import UserRepository
from app.services.exceptions import (
    BookingNotFoundError,
    InvalidBookingStatusError,
    ServiceNotFoundError,
    SlotAlreadyBookedError,
    UserNotFoundError,
)


class BookingService:
    """
    Business service managing client appointments, temporary holds and cancellations.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.appointment_repo = AppointmentRepository(session)
        self.service_repo = ServiceRepository(session)
        self.payment_repo = PaymentRepository(session)
        self.user_repo = UserRepository(session)
        self.settings_repo = SettingsRepository(session)

    async def create_hold_booking(
        self,
        user_id: int,
        service_id: int,
        start_time: datetime,
        cancel_policy_agreed: bool = True,
        master_id: int = 1,
        is_manual: bool = False,
        admin_notes: Optional[str] = None,
    ) -> Tuple[Appointment, Payment]:
        """
        Atomically reserve a slot, create immutable snapshot and pending deposit payment.
        """
        # 1. Validate user
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(f"User with ID {user_id} not found")

        # 2. Validate service
        service = await self.service_repo.get_by_id(service_id)
        if not service or not service.is_active or service.is_archived:
            raise ServiceNotFoundError(f"Service ID {service_id} not available")

        # 3. Calculate time intervals using service parameters
        duration = timedelta(minutes=service.duration_min)
        buffer = timedelta(minutes=service.buffer_min)
        end_time = start_time + duration
        end_time_with_buffer = end_time + buffer

        # 4. Check for existing active overlaps before insert
        overlaps = await self.appointment_repo.get_active_overlapping(
            master_id=master_id,
            start_time=start_time,
            end_time_with_buffer=end_time_with_buffer,
        )
        if overlaps:
            raise SlotAlreadyBookedError("Выбранное время уже занято или удерживается другим клиентом.")

        # 5. Calculate deposit amount
        if service.deposit_type == DepositType.PERCENT:
            deposit_amount = (service.price * service.deposit_value / Decimal("100")).quantize(
                Decimal("1.00")
            )
        else:
            deposit_amount = service.deposit_value.quantize(Decimal("1.00"))

        # 6. Determine hold deadline
        hold_minutes = int(
            await self.settings_repo.get_value("hold_duration_minutes", settings.hold_duration_minutes)
        )
        now_utc = datetime.now(pytz.UTC)
        hold_until = now_utc + timedelta(minutes=hold_minutes)

        # Initial status
        initial_status = AppointmentStatus.CONFIRMED if is_manual else AppointmentStatus.WAITING_PAYMENT

        # 7. Create appointment with immutable snapshot
        appointment = Appointment(
            master_id=master_id,
            user_id=user_id,
            service_id=service_id,
            status=initial_status,
            start_time=start_time,
            end_time=end_time,
            end_time_with_buffer=end_time_with_buffer,
            hold_until=None if is_manual else hold_until,
            cancel_policy_agreed=cancel_policy_agreed,
            snapshot_service_title=service.title,
            snapshot_service_price=service.price,
            snapshot_service_duration_min=service.duration_min,
            snapshot_buffer_duration_min=service.buffer_min,
            snapshot_deposit_amount=deposit_amount,
            is_manual_by_admin=is_manual,
            admin_notes=admin_notes,
        )
        self.session.add(appointment)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise SlotAlreadyBookedError(
                "Выбранный интервал только что был забронирован другим клиентом."
            ) from exc

        # 8. Create associated deposit payment
        payment = await self.payment_repo.create_payment(
            appointment_id=appointment.id,
            user_id=user_id,
            amount=deposit_amount,
        )
        if is_manual:
            payment.status = PaymentStatus.CONFIRMED

        await self.session.flush()
        await self.session.refresh(appointment)
        return appointment, payment

    async def cancel_booking_by_client(
        self,
        appointment_id: int,
        user_id: int,
        reason: Optional[str] = None,
    ) -> Appointment:
        """
        Client-initiated cancellation. Marks deposit retained according to policy.
        """
        appointment = await self.appointment_repo.get_by_id_with_relations(appointment_id)
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        if appointment.user_id != user_id:
            raise BookingNotFoundError("У вас нет доступа к этой записи")

        if appointment.status in [
            AppointmentStatus.CANCELLED_BY_CLIENT,
            AppointmentStatus.CANCELLED_BY_ADMIN,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.EXPIRED,
        ]:
            raise InvalidBookingStatusError(
                f"Невозможно отменить запись в статусе {appointment.status.display_name}"
            )

        appointment.status = AppointmentStatus.CANCELLED_BY_CLIENT
        appointment.cancel_reason = reason
        appointment.hold_until = None

        # Handle deposit: per policy, prepaid amount is retained
        payment = await self.payment_repo.get_by_appointment_id(appointment_id)
        if payment and payment.status in [PaymentStatus.CONFIRMED, PaymentStatus.SUBMITTED]:
            payment.status = PaymentStatus.RETAINED

        await self.session.flush()
        return appointment

    async def cancel_booking_by_admin(
        self,
        appointment_id: int,
        reason: Optional[str] = None,
    ) -> Appointment:
        """
        Admin-initiated cancellation.
        """
        appointment = await self.appointment_repo.get_by_id_with_relations(appointment_id)
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        appointment.status = AppointmentStatus.CANCELLED_BY_ADMIN
        appointment.cancel_reason = reason
        appointment.hold_until = None

        await self.session.flush()
        return appointment

    async def reschedule_booking_by_admin(
        self,
        appointment_id: int,
        new_start_time: datetime,
    ) -> Appointment:
        """
        Reschedule an appointment to a new date/time by master.
        Preserves snapshot durations and recalculates boundaries.
        """
        appointment = await self.appointment_repo.get_by_id(appointment_id)
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        duration = timedelta(minutes=appointment.snapshot_service_duration_min)
        buffer = timedelta(minutes=appointment.snapshot_buffer_duration_min)
        new_end_time = new_start_time + duration
        new_end_time_with_buffer = new_end_time + buffer

        # Check collision with other appointments
        overlaps = await self.appointment_repo.get_active_overlapping(
            master_id=appointment.master_id,
            start_time=new_start_time,
            end_time_with_buffer=new_end_time_with_buffer,
            exclude_id=appointment.id,
        )
        if overlaps:
            raise SlotAlreadyBookedError("Новое выбранное время уже занято другой записью.")

        appointment.start_time = new_start_time
        appointment.end_time = new_end_time
        appointment.end_time_with_buffer = new_end_time_with_buffer

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise SlotAlreadyBookedError("Конфликт времени при переносе записи.") from exc

        return appointment

    async def complete_booking(self, appointment_id: int) -> Appointment:
        """
        Mark appointment as completed after client visit.
        """
        appointment = await self.appointment_repo.get_by_id(appointment_id)
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        appointment.status = AppointmentStatus.COMPLETED
        await self.session.flush()
        return appointment

    async def mark_no_show(self, appointment_id: int) -> Appointment:
        """
        Mark appointment as No-Show.
        """
        appointment = await self.appointment_repo.get_by_id(appointment_id)
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        appointment.status = AppointmentStatus.NO_SHOW
        await self.session.flush()
        return appointment

    async def expire_booking(self, appointment_id: int) -> Optional[Appointment]:
        """
        Release expired temporary hold.
        """
        appointment = await self.appointment_repo.get_by_id(appointment_id)
        if appointment and appointment.status == AppointmentStatus.WAITING_PAYMENT:
            appointment.status = AppointmentStatus.EXPIRED
            appointment.hold_until = None
            await self.session.flush()
            return appointment
        return None
