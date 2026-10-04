"""
Multi-tenant master, bot instance, master settings, clients and admins models.
"""

from datetime import datetime
import enum
from typing import TYPE_CHECKING, List, Optional
import uuid
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.subscription import SubscriptionPayment, SubscriptionPeriod
    from app.database.models.user import User
    from app.database.models.staff import StaffMember


class MasterStatus(str, enum.Enum):
    SETUP_REQUIRED = "SETUP_REQUIRED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"


class BotInstanceStatus(str, enum.Enum):
    PROVISIONING = "PROVISIONING"
    SETUP_REQUIRED = "SETUP_REQUIRED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class MasterAdminRole(str, enum.Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    STAFF = "STAFF"


class Master(Base):
    """
    Tenant representation for an independent beauty master.
    One User may own multiple Masters (1 -> N relation).
    """
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[MasterStatus] = mapped_column(
        Enum(MasterStatus, name="master_status_enum", native_enum=True),
        default=MasterStatus.SETUP_REQUIRED,
        nullable=False,
    )
    subscription_status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum", native_enum=True),
        default=SubscriptionStatus.TRIAL,
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)
    activity_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped["User"] = relationship("User", foreign_keys=[owner_user_id])
    settings: Mapped[Optional["MasterSettings"]] = relationship(
        "MasterSettings", back_populates="master", uselist=False, cascade="all, delete-orphan"
    )
    bot_instances: Mapped[List["BotInstance"]] = relationship(
        "BotInstance", back_populates="master", cascade="all, delete-orphan"
    )
    clients: Mapped[List["MasterClient"]] = relationship(
        "MasterClient", back_populates="master", cascade="all, delete-orphan"
    )
    admins: Mapped[List["MasterAdmin"]] = relationship(
        "MasterAdmin", back_populates="master", cascade="all, delete-orphan"
    )
    staff_members: Mapped[List["StaffMember"]] = relationship(
        "StaffMember", back_populates="master", cascade="all, delete-orphan"
    )
    subscription_periods: Mapped[List["SubscriptionPeriod"]] = relationship(
        "SubscriptionPeriod", back_populates="master", cascade="all, delete-orphan"
    )
    subscription_payments: Mapped[List["SubscriptionPayment"]] = relationship(
        "SubscriptionPayment", back_populates="master", cascade="all, delete-orphan"
    )


class BotInstance(Base):
    """
    Dedicated customer bot assigned to a specific master.
    """
    __tablename__ = "bot_instances"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, default=uuid.uuid4, server_default=func.gen_random_uuid(), unique=True, nullable=False, index=True
    )
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_bot_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, nullable=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    telegram_first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    encrypted_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_secret: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[BotInstanceStatus] = mapped_column(
        Enum(BotInstanceStatus, name="bot_instance_status_enum", native_enum=True),
        default=BotInstanceStatus.PROVISIONING,
        nullable=False,
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    mini_app_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provisioning_source: Mapped[str] = mapped_column(
        String(32), default="manual_token", server_default=text("'manual_token'"), nullable=False
    )
    managed_by_platform: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    telegram_owner_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


    # Relationships
    master: Mapped["Master"] = relationship("Master", back_populates="bot_instances")


class MasterClient(Base):
    """
    Tenant-specific client relation, preferences and notes.
    """
    __tablename__ = "master_clients"
    __table_args__ = (
        UniqueConstraint("master_id", "user_id", name="uq_master_clients_master_user"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_marketing_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_visit_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_visit_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    master: Mapped["Master"] = relationship("Master", back_populates="clients")
    user: Mapped["User"] = relationship("User")


class MasterSettings(Base):
    """
    Tenant-specific configuration and payment requisites.
    """
    __tablename__ = "master_settings"

    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), primary_key=True
    )
    bank_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bank_card_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    bank_recipient_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    studio_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    studio_phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    whatsapp_phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    working_hours_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contacts_intro_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vk_profile: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    about_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hold_duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    cancel_policy_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    booking_horizon_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    min_advance_hours: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    grid_step_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    default_buffer_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    reminder_24h_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reminder_3h_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    master: Mapped["Master"] = relationship("Master", back_populates="settings")


class MasterAdmin(Base):
    """
    Tenant-specific administration privileges.
    """
    __tablename__ = "master_admins"
    __table_args__ = (
        UniqueConstraint("master_id", "user_id", name="uq_master_admins_master_user"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MasterAdminRole] = mapped_column(
        Enum(MasterAdminRole, name="master_admin_role_enum", native_enum=True),
        default=MasterAdminRole.ADMIN,
        nullable=False,
    )
    staff_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="CASCADE"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    master: Mapped["Master"] = relationship("Master", back_populates="admins")
    user: Mapped["User"] = relationship("User")
    staff: Mapped[Optional["StaffMember"]] = relationship("StaffMember")
