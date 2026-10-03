"""
Schedule templates, exceptions and blocked intervals models with multi-staff support.
"""

from datetime import date as dt_date, datetime, time as dt_time
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.staff import StaffMember


class ScheduleTemplate(Base):
    """
    Standard weekly schedule template per staff member and day of the week (0 = Monday, ..., 6 = Sunday).
    Strictly scoped to a master_id and staff_id.
    """
    __tablename__ = "schedule_templates"
    __table_args__ = (
        UniqueConstraint("staff_id", "day_of_week", name="uq_staff_weekday"),
        ForeignKeyConstraint(
            ["master_id", "staff_id"],
            ["staff_members.master_id", "staff_members.id"],
            name="fk_schedule_templates_staff_master",
            ondelete="CASCADE",
        ),
        Index("idx_schedule_templates_master_staff_day", "master_id", "staff_id", "day_of_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    staff_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_day_off: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    work_start: Mapped[dt_time] = mapped_column(Time, default=dt_time(10, 0), nullable=False)
    work_end: Mapped[dt_time] = mapped_column(Time, default=dt_time(19, 0), nullable=False)

    # Relationships
    staff: Mapped["StaffMember"] = relationship(
        "StaffMember",
        back_populates="schedule_templates",
        foreign_keys="[ScheduleTemplate.master_id, ScheduleTemplate.staff_id]",
    )
    breaks: Mapped[List["ScheduleTemplateBreak"]] = relationship(
        "ScheduleTemplateBreak", back_populates="template", cascade="all, delete-orphan"
    )


class ScheduleTemplateBreak(Base):
    """
    Regular breaks within a weekly template.
    """
    __tablename__ = "schedule_template_breaks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("schedule_templates.id", ondelete="CASCADE"), nullable=False
    )
    break_start: Mapped[dt_time] = mapped_column(Time, nullable=False)
    break_end: Mapped[dt_time] = mapped_column(Time, nullable=False)

    # Relationships
    template: Mapped["ScheduleTemplate"] = relationship("ScheduleTemplate", back_populates="breaks")


class ScheduleException(Base):
    """
    Date-specific schedule override.
    staff_id is NULL -> Project-wide override (studio holiday, maintenance, closure for all staff).
    staff_id is NOT NULL -> Specific staff member override (personal vacation, sick day).
    """
    __tablename__ = "schedule_exceptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["master_id", "staff_id"],
            ["staff_members.master_id", "staff_members.id"],
            name="fk_schedule_exceptions_staff_master",
            ondelete="CASCADE",
        ),
        Index("idx_schedule_exceptions_master_date", "master_id", "date"),
        Index("idx_schedule_exceptions_staff_date", "staff_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    staff_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="CASCADE"), nullable=True, index=True
    )
    date: Mapped[dt_date] = mapped_column(Date, nullable=False, index=True)
    is_day_off: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    work_start: Mapped[Optional[dt_time]] = mapped_column(Time, nullable=True)
    work_end: Mapped[Optional[dt_time]] = mapped_column(Time, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    staff: Mapped[Optional["StaffMember"]] = relationship(
        "StaffMember",
        back_populates="schedule_exceptions",
        foreign_keys="[ScheduleException.master_id, ScheduleException.staff_id]",
    )
    breaks: Mapped[List["ScheduleExceptionBreak"]] = relationship(
        "ScheduleExceptionBreak", back_populates="exception", cascade="all, delete-orphan"
    )


class ScheduleExceptionBreak(Base):
    """
    Breaks for a specific date override.
    """
    __tablename__ = "schedule_exception_breaks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exception_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("schedule_exceptions.id", ondelete="CASCADE"), nullable=False
    )
    break_start: Mapped[dt_time] = mapped_column(Time, nullable=False)
    break_end: Mapped[dt_time] = mapped_column(Time, nullable=False)

    # Relationships
    exception: Mapped["ScheduleException"] = relationship("ScheduleException", back_populates="breaks")


class BlockedInterval(Base):
    """
    Manually blocked interval.
    staff_id is NULL -> Project-wide blocked interval (e.g. whole salon closed for renovation).
    staff_id is NOT NULL -> Specific staff member personal blocked time.
    """
    __tablename__ = "blocked_intervals"
    __table_args__ = (
        CheckConstraint("start_time < end_time", name="chk_blocked_intervals_time_order"),
        ForeignKeyConstraint(
            ["master_id", "staff_id"],
            ["staff_members.master_id", "staff_members.id"],
            name="fk_blocked_intervals_staff_master",
            ondelete="CASCADE",
        ),
        Index("idx_blocked_intervals_master_start", "master_id", "start_time"),
        Index("idx_blocked_intervals_staff_start", "staff_id", "start_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    staff_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="CASCADE"), nullable=True, index=True
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_by_admin_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("admins.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    staff: Mapped[Optional["StaffMember"]] = relationship(
        "StaffMember",
        back_populates="blocked_intervals",
        foreign_keys="[BlockedInterval.master_id, BlockedInterval.staff_id]",
    )


from sqlalchemy import event, text


@event.listens_for(ScheduleTemplate, "before_insert")
def _auto_assign_schedule_template_staff(mapper, connection, target: ScheduleTemplate) -> None:
    """Ensure schedule template has a valid staff_id, defaulting to primary/active staff of the master."""
    if target.staff_id is None and target.master_id is not None:
        row = connection.execute(
            text(
                "SELECT id FROM staff_members "
                "WHERE master_id = :mid AND is_active = true "
                "ORDER BY sort_order ASC, id ASC LIMIT 1"
            ),
            {"mid": target.master_id},
        ).fetchone()
        if row:
            target.staff_id = row[0]
            return

        row_any = connection.execute(
            text(
                "SELECT id FROM staff_members "
                "WHERE master_id = :mid "
                "ORDER BY id ASC LIMIT 1"
            ),
            {"mid": target.master_id},
        ).fetchone()
        if row_any:
            target.staff_id = row_any[0]
            return

        row_new = connection.execute(
            text(
                "INSERT INTO staff_members (master_id, display_name, is_active, sort_order, created_at, updated_at) "
                "VALUES (:mid, 'Основной специалист', true, 0, now(), now()) "
                "RETURNING id"
            ),
            {"mid": target.master_id},
        ).fetchone()
        if row_new:
            target.staff_id = row_new[0]
