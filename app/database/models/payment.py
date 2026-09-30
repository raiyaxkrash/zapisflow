"""
Payment and PaymentProof database models.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.appointment import Appointment
    from app.database.models.user import User, Admin


class PaymentStatus(str, Enum):
    PENDING = "PENDING"          # Created, awaiting user payment
    SUBMITTED = "SUBMITTED"      # User clicked "I paid" and uploaded proof
    CONFIRMED = "CONFIRMED"      # Admin verified and confirmed receipt
    REJECTED = "REJECTED"        # Admin rejected the proof
    RETAINED = "RETAINED"        # Retained after client cancellation per policy


class MediaType(str, Enum):
    PHOTO = "PHOTO"
    DOCUMENT = "DOCUMENT"


class Payment(Base):
    """
    Financial deposit or payment record.
    Guarantees tenant consistency via composite FK to appointments(id, master_id).
    """
    __tablename__ = "payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["appointment_id", "master_id"],
            ["appointments.id", "appointments.master_id"],
            name="fk_payments_appointment_master",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount >= 0", name="chk_payments_amount_positive"),
        Index("idx_payments_master_status", "master_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    appointment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SQLEnum(PaymentStatus, name="payment_status_enum", native_enum=True),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_admin_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("admins.id", ondelete="SET NULL"), nullable=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    appointment: Mapped["Appointment"] = relationship(
        "Appointment",
        back_populates="payments",
        foreign_keys="[Payment.appointment_id, Payment.master_id]",
    )
    user: Mapped["User"] = relationship("User", back_populates="payments")
    confirmed_by_admin: Mapped[Optional["Admin"]] = relationship("Admin")
    proofs: Mapped[List["PaymentProof"]] = relationship(
        "PaymentProof", back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentProof(Base):
    """
    Uploaded screenshot or receipt for payment validation.
    """
    __tablename__ = "payment_proofs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(
        SQLEnum(MediaType, name="media_type_enum", native_enum=True),
        default=MediaType.PHOTO,
        nullable=False,
    )
    user_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    payment: Mapped["Payment"] = relationship("Payment", back_populates="proofs")
