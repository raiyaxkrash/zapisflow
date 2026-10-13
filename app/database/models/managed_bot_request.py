"""Database model for durable tracking of Managed Bot creation requests."""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.master import Master
    from app.database.models.user import User


class ManagedBotRequestStatus(str, PyEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class ManagedBotCreationRequest(Base):
    """Durable state tracking pending Telegram Managed Bot creation flows.

    Guarantees strict tenant isolation by binding the creation intent to a
    specific Master before the bot is created in Telegram.
    """

    __tablename__ = "managed_bot_creation_requests"
    __table_args__ = (
        Index(
            "uq_pending_managed_bot_request_per_user",
            "telegram_owner_user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_managed_bot_requests_owner_master", "owner_user_id", "master_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_owner_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    suggested_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    suggested_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[ManagedBotRequestStatus] = mapped_column(
        Enum(ManagedBotRequestStatus, name="managed_bot_request_status_enum", native_enum=True),
        default=ManagedBotRequestStatus.PENDING,
        nullable=False,
        index=True,
    )
    telegram_bot_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Relationships
    owner: Mapped["User"] = relationship("User")
    master: Mapped["Master"] = relationship("Master")
