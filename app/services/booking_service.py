"""Booking service handling appointment lifecycle, holds, snapshotting and cancellations with strict tenant isolation."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple
import pytz
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.audit import AuditLog
from app.database.models.master import Master
from app.database.models.payment import Payment, PaymentStatus
from app.database.models.service import DepositType
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.staff_repository import StaffRepository
from app.repositories.user_repository import UserRepository
from app.services.exceptions import (
    BookingNotFoundError,
    InvalidBookingStatusError,
    PaymentRequisitesMissingError,
    ServiceNotFoundError,
    StaffServiceUnavailableError,
    SlotAlreadyBookedError,
    SubscriptionExpiredError,
    UserNotFoundError,
)
from app.services.appointment_state import transition_appointment
from app.services.slot_engine import SlotEngine
from app.services.payment_service import PaymentService
from app.services.subscription_access_policy import (
    NEUTRAL_CLIENT_EXPIRED_MESSAGE,
    SubscriptionAccessPolicy,
)


class BookingService:
    """Business service managing client appointments, temporary holds and cancellations strictly per master_id."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.appointment_repo = AppointmentRepository(session)
        self.service_repo = ServiceRepository(session)
        self.staff_repo = StaffRepository(session)
        self.payment_repo = PaymentRepository(session)
        self.user_repo = UserRepository(session)
        self.master_settings_repo = MasterSettingsRepository(session)
        self.access_policy = SubscriptionAccessPolicy(session)

    @staticmethod
    def _is_overlap_violation(exc: IntegrityError) -> bool:
        """Recognize PostgreSQL exclusion constraint or overlap violations."""
        orig = getattr(exc, "orig", None)
        pgcode = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
        if pgcode == "23P01":
            return True
        exc_str = str(exc).lower()
        return (
            "no_overlapping" in exc_str
            or "exclusion" in exc_str
            or "23p01" in exc_str
        )

    async def create_hold_booking(
        self,
        master_id: int,
        user_id: int,
        service_id: int,
        start_time: datetime,
        cancel_policy_agreed: bool = True,
        is_manual: bool = False,
        admin_notes: Optional[str] = None,
        staff_id: Optional[int] = None,
    ) -> Tuple[Appointment, Optional[Payment]]:
        """Create a booking and, only for a positive deposit, its pending payment."""
        # Serialize booking creation with suspend/renewal/expiration updates.
        # A previously read ACTIVE state must not outlive an admin suspension.
        if isinstance(self.session, AsyncSession):
            locked = await self.session.scalar(
                select(Master)
                .where(Master.id == master_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if locked is None:
                raise SubscriptionExpiredError(NEUTRAL_CLIENT_EXPIRED_MESSAGE)
        # Every new appointment, including admin/manual creation, requires an active entitlement.
        if not await self.access_policy.can_create_hold(master_id):
            raise SubscriptionExpiredError(NEUTRAL_CLIENT_EXPIRED_MESSAGE)

        # 1. Validate user exists
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(f"User with ID {user_id} not found")

        # 2. Validate service exists AND strictly belongs to this master (prevents cross-tenant service injection)
        service = await self.service_repo.get_by_id(service_id, master_id=master_id)
        if not service or not service.is_active or service.is_archived:
            raise ServiceNotFoundError(f"Service ID {service_id} not available for master {master_id}")

        # 2.1 Validate or resolve staff member
        if staff_id is None:
            staff = await self.staff_repo.get_primary_or_default(master_id)
            if not staff:
                raise ValueError("В проекте нет доступных специалистов для записи")
            staff_id = staff.id
        else:
            staff = await self.staff_repo.get_by_id(staff_id, master_id=master_id)
            if not staff or not staff.is_active:
                raise StaffServiceUnavailableError(
                    "Выбранный специалист недоступен. Начните запись заново."
                )
            # An empty active mapping preserves the product's existing fallback:
            # an unconfigured specialist can perform all active project services.
            # Once any mappings are active, enforce the configured allow-list.
            staff_services = await self.staff_repo.list_services_for_staff(staff_id, master_id=master_id)
            if staff_services and service_id not in staff_services:
                raise StaffServiceUnavailableError(
                    "Выбранный специалист больше не оказывает эту услугу. "
                    "Начните запись заново."
                )

        # 3. Calculate time intervals using service parameters
        duration = timedelta(minutes=service.duration_min)
        buffer = timedelta(minutes=service.buffer_min)
        end_time = start_time + duration
        end_time_with_buffer = end_time + buffer

        # Validate slot availability against staff schedule and existing active bookings
        if start_time.tzinfo is None or not await SlotEngine(self.session).is_slot_available(
            service_id, start_time, master_id=master_id, staff_id=staff.id
        ):
            raise SlotAlreadyBookedError("Выбранное время больше недоступно. Пожалуйста, выберите другой слот.")

        # Check for existing active overlaps before insert within master_id and specific staff
        overlaps = await self.appointment_repo.get_active_overlapping(
            master_id=master_id,
            start_time=start_time,
            end_time_with_buffer=end_time_with_buffer,
            staff_id=staff.id,
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

        # Do not create a client hold/payment with sample, shared, or incomplete
        # payment details. Admin-created appointments do not collect a deposit.
        if deposit_amount > 0 and not is_manual:
            master_settings = await self.master_settings_repo.get_by_master_id(master_id)
            has_requisites = bool(
                master_settings
                and master_settings.bank_name
                and master_settings.bank_name.strip()
                and master_settings.bank_card_number
                and master_settings.bank_card_number.strip()
                and master_settings.bank_recipient_name
                and master_settings.bank_recipient_name.strip()
            )
            if not has_requisites:
                raise PaymentRequisitesMissingError(
                    "Предоплата временно недоступна: мастер ещё не настроил реквизиты."
                )

        # 6. Determine hold deadline from master's settings
        hold_minutes = int(
            await self.master_settings_repo.get_value(
                master_id, "hold_duration_minutes", settings.hold_duration_minutes
            )
        )
        now_utc = datetime.now(pytz.UTC)
        hold_until = now_utc + timedelta(minutes=hold_minutes)

        # Initial status
        no_deposit_required = deposit_amount <= 0
        initial_status = (
            AppointmentStatus.CONFIRMED
            if is_manual or no_deposit_required
            else AppointmentStatus.WAITING_PAYMENT
        )

        # Trial/paid_until can elapse while slot queries run. Administrative
        # status changes remain serialized by the Master row lock above.
        if not await self.access_policy.can_create_hold(master_id):
            raise SubscriptionExpiredError(NEUTRAL_CLIENT_EXPIRED_MESSAGE)

        # 7. Create appointment with immutable snapshot
        appointment = Appointment(
            master_id=master_id,
            staff_id=staff.id,
            user_id=user_id,
            service_id=service_id,
            status=initial_status,
            start_time=start_time,
            end_time=end_time,
            end_time_with_buffer=end_time_with_buffer,
            hold_until=None if is_manual or no_deposit_required else hold_until,
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
            if not self._is_overlap_violation(exc):
                raise
            raise SlotAlreadyBookedError(
                "Выбранный интервал только что был забронирован другим клиентом. Пожалуйста, выберите другое время."
            ) from exc

        if no_deposit_required and not is_manual:
            await self.session.refresh(appointment)
            return appointment, None

        # 8. Create associated deposit payment strictly under master_id
        payment = await self.payment_repo.create_payment(
            master_id=master_id,
            appointment_id=appointment.id,
            user_id=user_id,
            amount=deposit_amount,
        )
        await self.session.flush()
        await self.session.refresh(appointment)
        return appointment, payment

    async def cancel_booking_by_client(
        self,
        master_id: int,
        appointment_id: int,
        user_id: int,
        reason: Optional[str] = None,
    ) -> Appointment:
        """Client-initiated cancellation strictly within master_id."""
        payment = await self.payment_repo.get_by_appointment_id(
            appointment_id, master_id=master_id, for_update=True
        )
        appointment = await self.appointment_repo.get_by_id_with_relations(
            appointment_id, master_id=master_id, for_update=True
        )
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        if appointment.user_id != user_id:
            raise BookingNotFoundError("У вас нет доступа к этой записи")

        transition_appointment(appointment, AppointmentStatus.CANCELLED_BY_CLIENT)
        appointment.cancel_reason = reason
        appointment.hold_until = None

        # Handle deposit: per policy, prepaid amount is retained
        PaymentService.retain_confirmed_deposit(payment)

        await self.session.flush()
        return appointment

    async def cancel_booking_by_admin(
        self,
        master_id: int,
        appointment_id: int,
        reason: Optional[str] = None,
    ) -> Appointment:
        """Admin-initiated cancellation strictly within master_id."""
        payment = await self.payment_repo.get_by_appointment_id(
            appointment_id, master_id=master_id, for_update=True
        )
        appointment = await self.appointment_repo.get_by_id_with_relations(
            appointment_id, master_id=master_id, for_update=True
        )
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        if payment and payment.status == PaymentStatus.SUBMITTED:
            raise InvalidBookingStatusError("Сначала проверьте присланный чек")

        transition_appointment(appointment, AppointmentStatus.CANCELLED_BY_ADMIN)
        appointment.cancel_reason = reason
        appointment.hold_until = None

        await self.session.flush()
        return appointment

    async def reschedule_booking_by_admin(
        self,
        master_id: int,
        appointment_id: int,
        new_start_time: datetime,
        admin_id: int,
        new_staff_id: Optional[int] = None,
    ) -> Appointment:
        """Reschedule an appointment to a new date/time by master, strictly within master_id."""
        appointment = await self.appointment_repo.get_by_id_with_relations(
            appointment_id, master_id=master_id, for_update=True
        )
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")
        if appointment.status not in {
            AppointmentStatus.WAITING_PAYMENT,
            AppointmentStatus.PAYMENT_PROOF_SENT,
            AppointmentStatus.CONFIRMED,
        }:
            raise InvalidBookingStatusError("Перенос этой записи недоступен")
        appointment_start_utc = appointment.start_time
        if appointment_start_utc.tzinfo is None:
            appointment_start_utc = appointment_start_utc.replace(tzinfo=pytz.UTC)
        if appointment_start_utc <= datetime.now(pytz.UTC):
            raise InvalidBookingStatusError("Нельзя перенести запись, время которой уже наступило")

        target_staff_id = appointment.staff_id
        if new_staff_id is not None:
            staff = await self.staff_repo.get_by_id(new_staff_id, master_id=master_id)
            if not staff or not staff.is_active:
                raise ValueError("Специалист не найден или недоступен")
            target_staff_id = new_staff_id

        duration = timedelta(minutes=appointment.snapshot_service_duration_min)
        buffer = timedelta(minutes=appointment.snapshot_buffer_duration_min)
        new_end_time = new_start_time + duration
        new_end_time_with_buffer = new_end_time + buffer

        if not await SlotEngine(self.session).is_slot_available(
            appointment.service_id,
            new_start_time,
            master_id=master_id,
            staff_id=target_staff_id,
            duration_min=appointment.snapshot_service_duration_min,
            buffer_min=appointment.snapshot_buffer_duration_min,
            exclude_appointment_id=appointment.id,
            allow_inactive=True,
        ):
            raise SlotAlreadyBookedError("Новое время уже занято другим клиентом или недоступно в расписании.")

        appointment.start_time = new_start_time
        appointment.end_time = new_end_time
        appointment.end_time_with_buffer = new_end_time_with_buffer
        if new_staff_id is not None:
            appointment.staff_id = new_staff_id

        # Recalculate and reset pending/stale notifications for the new appointment time
        await NotificationRepository(self.session).handle_appointment_rescheduled(
            appointment.id, new_start_time
        )

        await self.session.flush()
        return appointment

    async def complete_booking(
        self,
        master_id: int,
        appointment_id: int,
    ) -> Appointment:
        """Mark appointment as completed after client visit, strictly within master_id."""
        appointment = await self.appointment_repo.get_by_id_with_relations(
            appointment_id, master_id=master_id, for_update=True
        )
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")
        transition_appointment(appointment, AppointmentStatus.COMPLETED)
        await self.session.flush()
        return appointment

    async def mark_no_show(
        self,
        master_id: int,
        appointment_id: int,
    ) -> Appointment:
        """Mark appointment as NO-SHOW, strictly within master_id."""
        appointment = await self.appointment_repo.get_by_id_with_relations(
            appointment_id, master_id=master_id, for_update=True
        )
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")
        transition_appointment(appointment, AppointmentStatus.NO_SHOW)
        payment = await self.payment_repo.get_by_appointment_id(
            appointment_id, master_id=master_id, for_update=True
        )
        PaymentService.retain_confirmed_deposit(payment)
        await self.session.flush()
        return appointment

    async def expire_booking(
        self, appointment_id: int, master_id: Optional[int] = None
    ) -> Optional[Appointment]:
        """Release an expired hold booking."""
        success = await self.appointment_repo.expire_waiting_hold_if_due(
            appointment_id, master_id=master_id
        )
        if not success:
            return None
        return await self.appointment_repo.get_by_id(
            appointment_id, master_id=master_id if master_id is not None else 1
        )
