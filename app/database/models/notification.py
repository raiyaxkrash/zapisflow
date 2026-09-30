"""
Scheduled appointment reminder notifications model.
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    DateTime,
    Integer,
    Enum as SQLEnum,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.appointment import Appointment


class NotificationType(str, Enum):
    REMINDER_24H = "REMINDER_24H"
    REMINDER_3H = "REMINDER_3H"
    CUSTOM = "CUSTOM"


class NotificationStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Notification(Base):
    """
    Scheduled reminder notification for an appointment.
    """
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[NotificationType] = mapped_column(
        SQLEnum(NotificationType, name="notification_type_enum", native_enum=True),
        default=NotificationType.REMINDER_24H,
        nullable=False,
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[NotificationStatus] = mapped_column(
        SQLEnum(NotificationStatus, name="notification_status_enum", native_enum=True),
        default=NotificationStatus.PENDING,
        nullable=False,
        index=True,
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    appointment: Mapped["Appointment"] = relationship("Appointment", back_populates="notifications")
