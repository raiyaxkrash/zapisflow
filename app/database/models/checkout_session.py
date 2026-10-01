"""Short-lived bearer capabilities for SaaS checkout on the external website."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class CheckoutSession(Base):
    __tablename__ = "checkout_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_checkout_session_token_hash"),
        UniqueConstraint("payment_id", name="uq_checkout_session_payment"),
        CheckConstraint("status IN ('ISSUED', 'USED')", name="ck_checkout_session_status"),
        Index("ix_checkout_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Never persist the URL token itself: only its SHA-256 digest.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    master_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False)
    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("subscription_payments.id", ondelete="RESTRICT"), nullable=False)
    plan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ISSUED", server_default="ISSUED")
    receipt_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
