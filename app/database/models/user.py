"""
User and Admin database models.
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.appointment import Appointment
    from app.database.models.payment import Payment
    from app.database.models.audit import AuditLog


class User(Base):
    """
    Represents a client or administrator Telegram profile.
    telegram_id uses BigInteger (BIGINT) to support all 64-bit Telegram IDs.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    admin_profile: Mapped[Optional["Admin"]] = relationship(
        "Admin", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    marketing_preference: Mapped[Optional["UserMarketingPreference"]] = relationship(
        "UserMarketingPreference", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    appointments: Mapped[List["Appointment"]] = relationship(
        "Appointment", back_populates="user", passive_deletes="all"
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="user", passive_deletes="all"
    )


class Admin(Base):
    """
    Administrator role and permissions table.
    """
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), default="ADMIN", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="admin_profile")
    audit_logs: Mapped[List["AuditLog"]] = relationship("AuditLog", back_populates="admin")


class UserMarketingPreference(Base):
    """
    User preference for marketing and broadcast communications.
    """
    __tablename__ = "user_marketing_preferences"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    is_marketing_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="marketing_preference")
