"""Payment repository for managing financial records, deposits and proofs with strict tenant isolation."""

from decimal import Decimal
from typing import Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.appointment import Appointment
from app.database.models.payment import MediaType, Payment, PaymentProof, PaymentStatus
from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    """Repository for managing payments and deposit verification proofs per master."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Payment, session)

    async def get_by_id(self, payment_id: int, master_id: int) -> Optional[Payment]:
        """Fetch payment ensuring it belongs to the given master."""
        query = select(Payment).where(
            Payment.id == payment_id,
            Payment.master_id == master_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_by_appointment_id(
        self, appointment_id: int, master_id: int, *, for_update: bool = False
    ) -> Optional[Payment]:
        """Get payment details by appointment ID strictly verifying master ownership."""
        query = (
            select(Payment)
            .where(
                Payment.appointment_id == appointment_id,
                Payment.master_id == master_id,
            )
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
        self, payment_id: int, master_id: int, *, for_update: bool = False
    ) -> Optional[Payment]:
        """Load payment verification graph strictly for this master."""
        query = (
            select(Payment)
            .where(
                Payment.id == payment_id,
                Payment.master_id == master_id,
            )
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
        self, master_id: int, appointment_id: int, user_id: int, amount: Decimal
    ) -> Payment:
        """Create a new pending deposit record explicitly assigned to master_id."""
        payment = Payment(
            master_id=master_id,
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
        master_id: int,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        media_type: MediaType = MediaType.PHOTO,
        user_comment: Optional[str] = None,
    ) -> Optional[PaymentProof]:
        """Attach payment receipt or screenshot to a payment, verifying tenant ownership."""
        payment = await self.get_by_id(payment_id, master_id)
        if not payment:
            return None

        proof = PaymentProof(
            payment_id=payment.id,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            media_type=media_type,
            user_comment=user_comment,
        )
        self.session.add(proof)
        await self.session.flush()
        await self.session.refresh(proof)
        return proof

    async def list_pending_inbox(self, master_id: int) -> Sequence[Payment]:
        """List all payments waiting for master's confirmation (SUBMITTED proofs)."""
        query = (
            select(Payment)
            .where(
                Payment.master_id == master_id,
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
