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
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    ForeignKeyConstraint,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.database.models.user import User
    from app.database.models.service import Service
    from app.database.models.payment import Payment
    from app.database.models.notification import Notification
    from app.database.models.staff import StaffMember


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
    Timestamps are stored as TIMESTAMPTZ (DateTime(timezone=True)).
    """
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("id", "master_id", name="uq_appointments_id_master_id"),
        UniqueConstraint("master_id", "id", name="uq_appointments_master_id_id"),
        ForeignKeyConstraint(
            ["master_id", "staff_id"],
            ["staff_members.master_id", "staff_members.id"],
            name="fk_appointments_staff_master",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["master_id", "service_id"],
            ["services.master_id", "services.id"],
            name="fk_appointments_service_master",
            ondelete="RESTRICT",
        ),
        CheckConstraint("snapshot_service_price >= 0", name="chk_appointments_price_positive"),
        CheckConstraint("snapshot_deposit_amount >= 0", name="chk_appointments_deposit_positive"),
        CheckConstraint("snapshot_service_duration_min > 0", name="chk_appointments_duration_positive"),
        CheckConstraint("snapshot_buffer_duration_min >= 0", name="chk_appointments_buffer_positive"),
        CheckConstraint("start_time < end_time", name="chk_appointments_start_before_end"),
        CheckConstraint("end_time <= end_time_with_buffer", name="chk_appointments_buffer_order"),
        Index("idx_appointments_master_start", "master_id", "start_time"),
        Index("idx_appointments_master_status", "master_id", "status"),
        Index("idx_appointments_staff_start", "staff_id", "start_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    staff_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    status: Mapped[AppointmentStatus] = mapped_column(
        SQLEnum(AppointmentStatus, name="appointment_status_enum", native_enum=True),
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
    staff: Mapped["StaffMember"] = relationship(
        "StaffMember",
        back_populates="appointments",
        primaryjoin="and_(Appointment.master_id == StaffMember.master_id, Appointment.staff_id == StaffMember.id)",
        foreign_keys="[Appointment.master_id, Appointment.staff_id]",
        overlaps="appointments,service",
    )
    service: Mapped["Service"] = relationship(
        "Service",
        back_populates="appointments",
        primaryjoin="and_(Appointment.master_id == Service.master_id, Appointment.service_id == Service.id)",
        foreign_keys="[Appointment.master_id, Appointment.service_id]",
        overlaps="appointments,staff",
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment",
        back_populates="appointment",
        cascade="all, delete-orphan",
        foreign_keys="[Payment.appointment_id, Payment.master_id]",
    )
    notifications: Mapped[List["Notification"]] = relationship(
        "Notification", back_populates="appointment", cascade="all, delete-orphan"
    )


from sqlalchemy import event, text


@event.listens_for(Appointment, "before_insert")
def _auto_assign_appointment_staff(mapper, connection, target: Appointment) -> None:
    """Ensure appointment has a valid staff_id, defaulting to primary/active staff of the master."""
    if target.staff_id is None and target.master_id is not None:
        row = connection.execute(
            text(
                "SELECT id FROM staff_members "
                "WHERE master_id = :mid AND is_active = true "
                "ORDER BY sort_order ASC, id ASC LIMIT 1"
            ),
            {"mid": target.master_id},
        ).fetchone()
        if row:
            target.staff_id = row[0]
            return

        row_any = connection.execute(
            text(
                "SELECT id FROM staff_members "
                "WHERE master_id = :mid "
                "ORDER BY id ASC LIMIT 1"
            ),
            {"mid": target.master_id},
        ).fetchone()
        if row_any:
            target.staff_id = row_any[0]
            return

        row_new = connection.execute(
            text(
                "INSERT INTO staff_members (master_id, display_name, is_active, sort_order, created_at, updated_at) "
                "VALUES (:mid, 'Основной специалист', true, 0, now(), now()) "
                "RETURNING id"
            ),
            {"mid": target.master_id},
        ).fetchone()
        if row_new:
            target.staff_id = row_new[0]
