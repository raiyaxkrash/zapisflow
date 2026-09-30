"""
Appointment database model with historical service snapshot and status enum.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.database.models.user import User
    from app.database.models.service import Service
    from app.database.models.payment import Payment
    from app.database.models.notification import Notification


class AppointmentStatus(str, Enum):
    WAITING_PAYMENT = "WAITING_PAYMENT"
    PAYMENT_PROOF_SENT = "PAYMENT_PROOF_SENT"
    CONFIRMED = "CONFIRMED"
    CANCELLED_BY_CLIENT = "CANCELLED_BY_CLIENT"
    CANCELLED_BY_ADMIN = "CANCELLED_BY_ADMIN"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"
    EXPIRED = "EXPIRED"

    @property
    def display_name(self) -> str:
        names = {
            self.WAITING_PAYMENT: "⏳ Ожидает оплаты",
            self.PAYMENT_PROOF_SENT: "🔎 Чек на проверке",
            self.CONFIRMED: "✅ Подтверждена",
            self.CANCELLED_BY_CLIENT: "❌ Отменена клиентом",
            self.CANCELLED_BY_ADMIN: "❌ Отменена мастером",
            self.COMPLETED: "🎉 Выполнена",
            self.NO_SHOW: "🚫 Не пришел (No-Show)",
            self.EXPIRED: "⏰ Время оплаты истекло",
        }
        return names.get(self, self.value)


class Appointment(Base, TimestampMixin):
    """
    Client booking with immutable service snapshot and hold timestamp.
    """
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    status: Mapped[AppointmentStatus] = mapped_column(
        SQLEnum(AppointmentStatus, name="appointment_status_enum"),
        default=AppointmentStatus.WAITING_PAYMENT,
        nullable=False,
        index=True,
    )

    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time_with_buffer: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    hold_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    cancel_policy_agreed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Immutable Snapshot fields of the service at time of booking
    snapshot_service_title: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_service_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    snapshot_service_duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_buffer_duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_deposit_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    is_manual_by_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="appointments")
    service: Mapped["Service"] = relationship("Service", back_populates="appointments")
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="appointment", cascade="all, delete-orphan"
    )
    notifications: Mapped[List["Notification"]] = relationship(
        "Notification", back_populates="appointment", cascade="all, delete-orphan"
    )
