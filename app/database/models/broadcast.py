"""
Broadcast campaign and recipient models.
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.user import User, Admin


class BroadcastStatus(str, Enum):
    DRAFT = "DRAFT"
    SENDING = "SENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RecipientStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class Broadcast(Base):
    """
    Mass broadcast message campaign created by an admin.
    """
    __tablename__ = "broadcasts"
    __table_args__ = (
        Index("idx_broadcasts_master_status", "master_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    admin_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("admins.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    button_text: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    button_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    status: Mapped[BroadcastStatus] = mapped_column(
        SQLEnum(BroadcastStatus, name="broadcast_status_enum", native_enum=True),
        default=BroadcastStatus.DRAFT,
        nullable=False,
    )
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    admin: Mapped[Optional["Admin"]] = relationship("Admin")
    recipients: Mapped[List["BroadcastRecipient"]] = relationship(
        "BroadcastRecipient", back_populates="broadcast", cascade="all, delete-orphan"
    )


class BroadcastRecipient(Base):
    """
    Individual recipient tracking for a broadcast.
    """
    __tablename__ = "broadcast_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broadcast_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[RecipientStatus] = mapped_column(
        SQLEnum(RecipientStatus, name="recipient_status_enum", native_enum=True),
        default=RecipientStatus.PENDING,
        nullable=False,
    )
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    broadcast: Mapped["Broadcast"] = relationship("Broadcast", back_populates="recipients")
    user: Mapped["User"] = relationship("User")
