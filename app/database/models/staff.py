"""
Staff members and staff-service associations database models with strict tenant isolation.
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.database.models.master import Master
    from app.database.models.user import User
    from app.database.models.service import Service
    from app.database.models.appointment import Appointment
    from app.database.models.schedule import ScheduleTemplate, ScheduleException, BlockedInterval


class StaffMember(Base, TimestampMixin):
    """
    Staff member (specialist/master) within a specific Master/Tenant project.
    Strictly tenant-scoped via master_id.
    """
    __tablename__ = "staff_members"
    __table_args__ = (
        UniqueConstraint("master_id", "id", name="uq_staff_members_master_id_id"),
        Index("idx_staff_members_master_active", "master_id", "is_active"),
        Index("idx_staff_members_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    specialization: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Secure crypto invite tokens for Manager Bot access
    invite_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    invite_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    invite_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    master: Mapped["Master"] = relationship("Master", back_populates="staff_members")
    user: Mapped[Optional["User"]] = relationship("User")
    staff_services: Mapped[List["StaffService"]] = relationship(
        "StaffService", back_populates="staff", cascade="all, delete-orphan"
    )
    appointments: Mapped[List["Appointment"]] = relationship(
        "Appointment",
        back_populates="staff",
        primaryjoin="and_(StaffMember.master_id == Appointment.master_id, StaffMember.id == Appointment.staff_id)",
        foreign_keys="[Appointment.master_id, Appointment.staff_id]",
        overlaps="appointments,service",
    )
    schedule_templates: Mapped[List["ScheduleTemplate"]] = relationship(
        "ScheduleTemplate",
        back_populates="staff",
        cascade="all, delete-orphan",
        foreign_keys="[ScheduleTemplate.master_id, ScheduleTemplate.staff_id]",
    )
    schedule_exceptions: Mapped[List["ScheduleException"]] = relationship(
        "ScheduleException",
        back_populates="staff",
        cascade="all, delete-orphan",
        foreign_keys="[ScheduleException.master_id, ScheduleException.staff_id]",
    )
    blocked_intervals: Mapped[List["BlockedInterval"]] = relationship(
        "BlockedInterval",
        back_populates="staff",
        cascade="all, delete-orphan",
        foreign_keys="[BlockedInterval.master_id, BlockedInterval.staff_id]",
    )


class StaffService(Base, TimestampMixin):
    """
    Association between a staff member and a service offered by the studio.
    Ensures both belong to the exact same master_id at PostgreSQL foreign key level.
    """
    __tablename__ = "staff_services"
    __table_args__ = (
        UniqueConstraint("staff_id", "service_id", name="uq_staff_services_staff_service"),
        ForeignKeyConstraint(
            ["master_id", "staff_id"],
            ["staff_members.master_id", "staff_members.id"],
            name="fk_staff_services_staff_master",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["master_id", "service_id"],
            ["services.master_id", "services.id"],
            name="fk_staff_services_service_master",
            ondelete="CASCADE",
        ),
        Index("idx_staff_services_master_staff", "master_id", "staff_id"),
        Index("idx_staff_services_master_service", "master_id", "service_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    staff_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    service_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    staff: Mapped["StaffMember"] = relationship("StaffMember", back_populates="staff_services")
    service: Mapped["Service"] = relationship("Service", overlaps="staff,staff_services")
