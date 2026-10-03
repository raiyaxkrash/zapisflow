"""Mini App security state. Business entities remain in existing tables."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class MiniAppSession(Base):
    __tablename__ = "miniapp_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    init_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    bot_instance_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bot_instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class MiniAppOperation(Base):
    """Response ledger committed together with an HTTP business mutation."""

    __tablename__ = "miniapp_operations"
    __table_args__ = (
        UniqueConstraint(
            "bot_instance_id", "user_id", "key", name="uq_miniapp_operation"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_instance_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bot_instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
