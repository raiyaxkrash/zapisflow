"""
Payment repository for managing financial records, deposits and proofs.
"""

from decimal import Decimal
from typing import Optional, Sequence
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.appointment import Appointment
from app.database.models.payment import MediaType, Payment, PaymentProof, PaymentStatus
from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    """
    Repository for managing payments and deposit verification proofs.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Payment, session)

    async def get_by_appointment_id(self, appointment_id: int) -> Optional[Payment]:
        """
        Get payment details by appointment ID.
        """
        query = (
            select(Payment)
            .where(Payment.appointment_id == appointment_id)
            .options(
                selectinload(Payment.proofs),
                selectinload(Payment.user),
                selectinload(Payment.appointment),
            )
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def create_payment(
        self, appointment_id: int, user_id: int, amount: Decimal
    ) -> Payment:
        """
        Create a new pending deposit record.
        """
        payment = Payment(
            appointment_id=appointment_id,
            user_id=user_id,
            amount=amount,
            status=PaymentStatus.PENDING,
        )
        self.session.add(payment)
        await self.session.flush()
        await self.session.refresh(payment)
        return payment

    async def add_proof(
        self,
        payment_id: int,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        media_type: MediaType = MediaType.PHOTO,
        user_comment: Optional[str] = None,
    ) -> PaymentProof:
        """
        Attach payment receipt or screenshot to a payment.
        """
        proof = PaymentProof(
            payment_id=payment_id,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            media_type=media_type,
            user_comment=user_comment,
        )
        self.session.add(proof)

        # Update payment status to SUBMITTED
        payment = await self.get_by_id(payment_id)
        if payment and payment.status == PaymentStatus.PENDING:
            payment.status = PaymentStatus.SUBMITTED

        await self.session.flush()
        await self.session.refresh(proof)
        return proof

    async def update_status(
        self,
        payment_id: int,
        status: PaymentStatus,
        admin_id: Optional[int] = None,
        rejection_reason: Optional[str] = None,
    ) -> Optional[Payment]:
        """
        Update payment status upon admin decision.
        """
        payment = await self.get_by_id(payment_id)
        if not payment:
            return None
        payment.status = status
        if status == PaymentStatus.CONFIRMED:
            payment.confirmed_at = func.now()
            payment.confirmed_by_admin_id = admin_id
        elif status == PaymentStatus.REJECTED:
            payment.rejection_reason = rejection_reason
        await self.session.flush()
        await self.session.refresh(payment)
        return payment

    async def list_pending_inbox(self, master_id: int = 1) -> Sequence[Payment]:
        """
        List all payments waiting for admin confirmation (SUBMITTED proofs).
        """
        query = (
            select(Payment)
            .join(Appointment, Payment.appointment_id == Appointment.id)
            .where(
                Appointment.master_id == master_id,
                Payment.status == PaymentStatus.SUBMITTED,
            )
            .options(
                selectinload(Payment.proofs),
                selectinload(Payment.user),
                selectinload(Payment.appointment).selectinload(Appointment.service),
            )
            .order_by(Payment.created_at.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()
