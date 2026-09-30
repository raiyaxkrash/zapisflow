"""
Payment repository for managing financial records, deposits and proofs.
"""

from decimal import Decimal
from typing import Optional, Sequence
from sqlalchemy import select
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

    async def get_by_appointment_id(
        self, appointment_id: int, *, for_update: bool = False
    ) -> Optional[Payment]:
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
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_by_id_with_proofs(
        self, payment_id: int, *, for_update: bool = False
    ) -> Optional[Payment]:
        """Load the payment decision graph without async lazy relationship access."""
        query = (
            select(Payment)
            .where(Payment.id == payment_id)
            .options(
                selectinload(Payment.proofs),
                selectinload(Payment.user),
                selectinload(Payment.appointment).selectinload(Appointment.user),
            )
        )
        if for_update:
            query = query.with_for_update()
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

        await self.session.flush()
        await self.session.refresh(proof)
        return proof

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
