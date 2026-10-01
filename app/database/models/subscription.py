"""
Subscription, billing periods, payment history and plan database models.
"""

from datetime import datetime
from decimal import Decimal
import enum
import uuid
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.master import Master


class EffectiveSubscriptionStatus(str, enum.Enum):
    """Calculated effective subscription status considering timestamp expiration and administrative locks."""

    TRIAL_ACTIVE = "TRIAL_ACTIVE"
    PAID_ACTIVE = "PAID_ACTIVE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"


class SubscriptionPlan(Base):
    """
    Catalog of subscription plans (tariffs) for SaaS masters.
    """

    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", nullable=False)
    period_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    periods: Mapped[List["SubscriptionPeriod"]] = relationship("SubscriptionPeriod", back_populates="plan")
    payments: Mapped[List["SubscriptionPayment"]] = relationship("SubscriptionPayment", back_populates="plan")

    @property
    def price_rub(self) -> Decimal:
        """Alias for price in Russian Rubles."""
        return self.price

    @property
    def duration_days(self) -> int:
        """Alias for period_days."""
        return self.period_days


class SubscriptionPeriod(Base):
    """
    Historical record of master subscription periods (trials, renewals, manual extensions).
    """

    __tablename__ = "subscription_periods"
    __table_args__ = (
        Index("idx_sub_periods_master_starts", "master_id", "starts_at"),
        Index("idx_sub_periods_master_ends", "master_id", "ends_at"),
        UniqueConstraint("subscription_payment_id", name="uq_subscription_period_payment"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # TRIAL, PAYMENT, MANUAL, LEGACY
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", nullable=False)
    external_payment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    subscription_payment_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("subscription_payments.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    master: Mapped["Master"] = relationship("Master", back_populates="subscription_periods")
    plan: Mapped[Optional["SubscriptionPlan"]] = relationship("SubscriptionPlan", back_populates="periods")


class SubscriptionPayment(Base):
    """
    SaaS billing payments for platform subscription access (separate from customer booking prepayments).
    """

    __tablename__ = "subscription_payments"
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_subscription_payment_provider_id"),
        UniqueConstraint("checkout_ref", name="uq_subscription_payment_checkout_ref"),
        Index("idx_sub_payments_master_created", "master_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # MANUAL, YOOKASSA, etc.
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    checkout_ref: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    last_reconciled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)  # PENDING, SUCCEEDED, FAILED, CANCELLED
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    period_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    master: Mapped["Master"] = relationship("Master", back_populates="subscription_payments")
    plan: Mapped[Optional["SubscriptionPlan"]] = relationship("SubscriptionPlan", back_populates="payments")


class PromoCode(Base):
    """
    Promotional code architecture for future discounts and marketing campaigns.
    """

    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    discount_type: Mapped[str] = mapped_column(String(16), default="PERCENT", nullable=False)  # PERCENT, FIXED
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

