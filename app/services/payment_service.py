"""
Payment service handling proof submission, verification and admin approval workflows.
"""

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple
import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import MediaType, Payment, PaymentProof, PaymentStatus
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.payment_repository import PaymentRepository
from app.services.exceptions import (
    BookingNotFoundError,
    HoldExpiredError,
    InvalidBookingStatusError,
    InvalidPaymentStatusError,
    PaymentNotFoundError,
)
from app.services.appointment_state import transition_appointment


@dataclass(frozen=True)
class PaymentDecision:
    appointment: Appointment
    payment: Payment
    changed: bool


class PaymentService:
    """
    Business service managing payment receipts, client verification proofs and admin approvals.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.payment_repo = PaymentRepository(session)
        self.appointment_repo = AppointmentRepository(session)

    _allowed_transitions = {
        PaymentStatus.PENDING: {PaymentStatus.SUBMITTED},
        PaymentStatus.SUBMITTED: {PaymentStatus.CONFIRMED, PaymentStatus.REJECTED},
        PaymentStatus.REJECTED: {PaymentStatus.SUBMITTED},
        PaymentStatus.CONFIRMED: {PaymentStatus.RETAINED},
        PaymentStatus.RETAINED: set(),
    }

    @classmethod
    def _transition_payment(cls, payment: Payment, target: PaymentStatus) -> None:
        if target not in cls._allowed_transitions[payment.status]:
            raise InvalidPaymentStatusError(
                f"Переход платежа {payment.status.value} → {target.value} запрещён"
            )
        payment.status = target

    async def submit_payment_proof(
        self,
        appointment_id: int,
        user_id: int,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        media_type: MediaType = MediaType.PHOTO,
        comment: Optional[str] = None,
    ) -> Tuple[Appointment, Payment, PaymentProof]:
        """
        Record receipt upload by client, freeze hold and transition to PAYMENT_PROOF_SENT.
        """
        payment = await self.payment_repo.get_by_appointment_id(
            appointment_id, for_update=True
        )
        appointment = await self.appointment_repo.get_by_id_with_relations(
            appointment_id, for_update=True
        )
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        if appointment.user_id != user_id:
            raise BookingNotFoundError("У вас нет доступа к этой записи")

        if not payment:
            raise PaymentNotFoundError(f"Платеж для записи #{appointment_id} не найден")

        if payment.status not in {PaymentStatus.PENDING, PaymentStatus.REJECTED, PaymentStatus.SUBMITTED}:
            raise InvalidPaymentStatusError("К этому платежу нельзя прикрепить новый чек")
        expected_appointment_status = (
            AppointmentStatus.PAYMENT_PROOF_SENT
            if payment.status == PaymentStatus.SUBMITTED
            else AppointmentStatus.WAITING_PAYMENT
        )
        if appointment.status != expected_appointment_status:
            raise InvalidBookingStatusError(
                f"Нельзя прикрепить чек для записи в статусе {appointment.status.display_name}"
            )
        now_utc = datetime.now(pytz.UTC)
        hold_until = appointment.hold_until
        if hold_until is not None and hold_until.tzinfo is None:
            hold_until = hold_until.replace(tzinfo=pytz.UTC)
        if appointment.status == AppointmentStatus.WAITING_PAYMENT and (
            hold_until is None or hold_until <= now_utc
        ):
            raise HoldExpiredError("Время для загрузки чека истекло")

        # Attach receipt proof
        proof = await self.payment_repo.add_proof(
            payment_id=payment.id,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            media_type=media_type,
            user_comment=comment,
        )

        if payment.status != PaymentStatus.SUBMITTED:
            self._transition_payment(payment, PaymentStatus.SUBMITTED)
        payment.rejection_reason = None

        # Transition status and neutralize hold countdown
        if appointment.status == AppointmentStatus.WAITING_PAYMENT:
            transition_appointment(appointment, AppointmentStatus.PAYMENT_PROOF_SENT)
        appointment.hold_until = None
        await self.session.flush()

        return appointment, payment, proof

    async def approve_payment(
        self, payment_id: int, admin_id: int
    ) -> PaymentDecision:
        """
        Admin approves payment receipt: confirms both payment and appointment.
        """
        payment = await self.payment_repo.get_by_id_with_proofs(payment_id, for_update=True)
        if not payment:
            raise PaymentNotFoundError(f"Платеж #{payment_id} не найден")
        appointment = await self.appointment_repo.get_by_id_with_relations(
            payment.appointment_id, for_update=True
        )
        if not appointment:
            raise BookingNotFoundError(f"Связанная запись #{payment.appointment_id} не найдена")
        if payment.status in {PaymentStatus.CONFIRMED, PaymentStatus.REJECTED, PaymentStatus.RETAINED}:
            return PaymentDecision(appointment, payment, changed=False)
        if payment.status != PaymentStatus.SUBMITTED:
            raise InvalidPaymentStatusError("Подтверждать можно только чек на проверке")
        if not payment.proofs:
            raise InvalidPaymentStatusError("Подтверждать платёж без чека нельзя")
        if appointment.status != AppointmentStatus.PAYMENT_PROOF_SENT:
            raise InvalidBookingStatusError("Запись не ожидает проверки оплаты")

        self._transition_payment(payment, PaymentStatus.CONFIRMED)
        payment.confirmed_at = datetime.now(pytz.UTC)
        payment.confirmed_by_admin_id = admin_id
        transition_appointment(appointment, AppointmentStatus.CONFIRMED)
        appointment.hold_until = None
        await self.session.flush()

        return PaymentDecision(appointment, payment, changed=True)

    async def reject_payment(
        self,
        payment_id: int,
        admin_id: int,
        reason: str,
        extend_hold_minutes: int = 15,
    ) -> PaymentDecision:
        """
        Admin rejects payment receipt with explanation.
        Reverts appointment back to WAITING_PAYMENT with extended hold window.
        """
        payment = await self.payment_repo.get_by_id_with_proofs(payment_id, for_update=True)
        if not payment:
            raise PaymentNotFoundError(f"Платеж #{payment_id} не найден")
        appointment = await self.appointment_repo.get_by_id_with_relations(
            payment.appointment_id, for_update=True
        )
        if not appointment:
            raise BookingNotFoundError(f"Связанная запись #{payment.appointment_id} не найдена")

        if payment.status in {PaymentStatus.CONFIRMED, PaymentStatus.REJECTED, PaymentStatus.RETAINED}:
            return PaymentDecision(appointment, payment, changed=False)

        if payment.status != PaymentStatus.SUBMITTED:
            raise InvalidPaymentStatusError("Отклонять можно только чек на проверке")
        if appointment.status != AppointmentStatus.PAYMENT_PROOF_SENT:
            raise InvalidBookingStatusError("Запись не ожидает проверки оплаты")

        self._transition_payment(payment, PaymentStatus.REJECTED)
        payment.rejection_reason = reason

        # Grant additional time to pay or upload correct proof
        now_utc = datetime.now(pytz.UTC)
        transition_appointment(appointment, AppointmentStatus.WAITING_PAYMENT)
        appointment.hold_until = now_utc + timedelta(minutes=extend_hold_minutes)
        await self.session.flush()

        return PaymentDecision(appointment, payment, changed=True)

    @classmethod
    def retain_confirmed_deposit(cls, payment: Payment | None) -> bool:
        """Account for money retained only after its receipt was confirmed."""
        if payment is None or payment.status != PaymentStatus.CONFIRMED:
            return False
        cls._transition_payment(payment, PaymentStatus.RETAINED)
        return True

    async def resolve_cancelled_payment(
        self, payment_id: int, admin_id: int, *, received: bool
    ) -> PaymentDecision:
        """Review an unconfirmed transfer after the client canceled its appointment."""
        payment = await self.payment_repo.get_by_id_with_proofs(payment_id, for_update=True)
        if payment is None:
            raise PaymentNotFoundError(f"Платеж #{payment_id} не найден")
        appointment = await self.appointment_repo.get_by_id_with_relations(
            payment.appointment_id, for_update=True
        )
        if appointment is None:
            raise BookingNotFoundError(f"Связанная запись #{payment.appointment_id} не найдена")
        if payment.status in {PaymentStatus.RETAINED, PaymentStatus.REJECTED}:
            return PaymentDecision(appointment, payment, changed=False)
        if payment.status != PaymentStatus.SUBMITTED:
            raise InvalidPaymentStatusError("Этот платёж не ожидает проверки")
        if appointment.status != AppointmentStatus.CANCELLED_BY_CLIENT:
            raise InvalidBookingStatusError("Запись не отменена клиентом")
        if not payment.proofs:
            raise InvalidPaymentStatusError("Нельзя подтвердить перевод без чека")

        if received:
            self._transition_payment(payment, PaymentStatus.CONFIRMED)
            payment.confirmed_at = datetime.now(pytz.UTC)
            payment.confirmed_by_admin_id = admin_id
            self._transition_payment(payment, PaymentStatus.RETAINED)
        else:
            self._transition_payment(payment, PaymentStatus.REJECTED)
            payment.rejection_reason = "Перевод по отменённой записи не поступил"
        await self.session.flush()
        return PaymentDecision(appointment, payment, changed=True)

    async def list_pending_inbox(self, master_id: int = 1) -> Sequence[Payment]:
        """
        Get all submitted payments waiting for master's validation.
        """
        return await self.payment_repo.list_pending_inbox(master_id=master_id)
