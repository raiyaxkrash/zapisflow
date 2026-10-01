"""Durable completion markers for webhook updates."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class ProcessedWebhookUpdate(Base):
    """One completed update per tenant bot (or the platform manager bot)."""

    __tablename__ = "processed_webhook_updates"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
