"""
Payment service handling proof submission, verification and admin approval workflows.
"""

from datetime import datetime, timedelta
from typing import Optional, Sequence, Tuple
import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import MediaType, Payment, PaymentProof, PaymentStatus
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.payment_repository import PaymentRepository
from app.services.exceptions import (
    BookingNotFoundError,
    InvalidBookingStatusError,
    PaymentNotFoundError,
)


class PaymentService:
    """
    Business service managing payment receipts, client verification proofs and admin approvals.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.payment_repo = PaymentRepository(session)
        self.appointment_repo = AppointmentRepository(session)

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
        appointment = await self.appointment_repo.get_by_id_with_relations(appointment_id)
        if not appointment:
            raise BookingNotFoundError(f"Запись #{appointment_id} не найдена")

        if appointment.user_id != user_id:
            raise BookingNotFoundError("У вас нет доступа к этой записи")

        if appointment.status not in [
            AppointmentStatus.WAITING_PAYMENT,
            AppointmentStatus.PAYMENT_PROOF_SENT,
        ]:
            raise InvalidBookingStatusError(
                f"Нельзя прикрепить чек для записи в статусе {appointment.status.display_name}"
            )

        payment = await self.payment_repo.get_by_appointment_id(appointment_id)
        if not payment:
            raise PaymentNotFoundError(f"Платеж для записи #{appointment_id} не найден")

        # Attach receipt proof
        proof = await self.payment_repo.add_proof(
            payment_id=payment.id,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            media_type=media_type,
            user_comment=comment,
        )

        # Transition status and neutralize hold countdown
        appointment.status = AppointmentStatus.PAYMENT_PROOF_SENT
        appointment.hold_until = None
        await self.session.flush()

        return appointment, payment, proof

    async def approve_payment(
        self, payment_id: int, admin_id: int
    ) -> Tuple[Appointment, Payment]:
        """
        Admin approves payment receipt: confirms both payment and appointment.
        """
        payment = await self.payment_repo.get_by_id(payment_id)
        if not payment:
            raise PaymentNotFoundError(f"Платеж #{payment_id} не найден")

        if payment.status == PaymentStatus.CONFIRMED:
            # Already confirmed (idempotent return)
            appointment = await self.appointment_repo.get_by_id(payment.appointment_id)
            return appointment, payment

        # Update payment
        await self.payment_repo.update_status(
            payment_id=payment_id,
            status=PaymentStatus.CONFIRMED,
            admin_id=admin_id,
        )

        # Update appointment
        appointment = await self.appointment_repo.get_by_id(payment.appointment_id)
        if not appointment:
            raise BookingNotFoundError(f"Связанная запись #{payment.appointment_id} не найдена")

        appointment.status = AppointmentStatus.CONFIRMED
        appointment.hold_until = None
        await self.session.flush()

        return appointment, payment

    async def reject_payment(
        self,
        payment_id: int,
        admin_id: int,
        reason: str,
        extend_hold_minutes: int = 15,
    ) -> Tuple[Appointment, Payment]:
        """
        Admin rejects payment receipt with explanation.
        Reverts appointment back to WAITING_PAYMENT with extended hold window.
        """
        payment = await self.payment_repo.get_by_id(payment_id)
        if not payment:
            raise PaymentNotFoundError(f"Платеж #{payment_id} не найден")

        await self.payment_repo.update_status(
            payment_id=payment_id,
            status=PaymentStatus.REJECTED,
            admin_id=admin_id,
            rejection_reason=reason,
        )

        appointment = await self.appointment_repo.get_by_id(payment.appointment_id)
        if not appointment:
            raise BookingNotFoundError(f"Связанная запись #{payment.appointment_id} не найдена")

        # Grant additional time to pay or upload correct proof
        now_utc = datetime.now(pytz.UTC)
        appointment.status = AppointmentStatus.WAITING_PAYMENT
        appointment.hold_until = now_utc + timedelta(minutes=extend_hold_minutes)
        await self.session.flush()

        return appointment, payment

    async def list_pending_inbox(self, master_id: int = 1) -> Sequence[Payment]:
        """
        Get all submitted payments waiting for master's validation.
        """
        return await self.payment_repo.list_pending_inbox(master_id=master_id)
