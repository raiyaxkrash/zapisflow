"""Durable tenant Telegram deliveries created in the business transaction."""

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class TelegramOutboxStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    FAILED = "FAILED"


class TelegramOutbox(Base):
    """One durable attempt stream for a tenant notification.

    Telegram does not offer a client idempotency key for sendMessage. A crash
    after acceptance and before SENT is committed may therefore send twice.
    """

    __tablename__ = "telegram_outbox"
    __table_args__ = (
        Index("ix_telegram_outbox_due", "status", "next_attempt_at"),
        Index("ix_telegram_outbox_claim", "status", "claimed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    bot_instance_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bot_instances.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[TelegramOutboxStatus] = mapped_column(
        SQLEnum(TelegramOutboxStatus, name="telegram_outbox_status_enum", native_enum=True),
        nullable=False,
        default=TelegramOutboxStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
