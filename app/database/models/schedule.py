"""
Schedule templates, exceptions and blocked intervals models.
"""

from datetime import date as dt_date, datetime, time as dt_time
from typing import List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
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


class ScheduleTemplate(Base):
    """
    Standard weekly schedule template per day of the week (0 = Monday, ..., 6 = Sunday).
    """
    __tablename__ = "schedule_templates"
    __table_args__ = (
        UniqueConstraint("master_id", "day_of_week", name="uq_master_weekday"),
        Index("idx_schedule_templates_master_day", "master_id", "day_of_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_day_off: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    work_start: Mapped[dt_time] = mapped_column(Time, default=dt_time(10, 0), nullable=False)
    work_end: Mapped[dt_time] = mapped_column(Time, default=dt_time(19, 0), nullable=False)

    # Relationships
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
    Date-specific schedule override (working day, day off, custom hours).
    """
    __tablename__ = "schedule_exceptions"
    __table_args__ = (
        UniqueConstraint("master_id", "date", name="uq_master_date"),
        Index("idx_schedule_exceptions_master_date", "master_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    date: Mapped[dt_date] = mapped_column(Date, nullable=False, index=True)
    is_day_off: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    work_start: Mapped[Optional[dt_time]] = mapped_column(Time, nullable=True)
    work_end: Mapped[Optional[dt_time]] = mapped_column(Time, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
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
    Manually blocked interval for a master (e.g. personal appointment, sick leave).
    """
    __tablename__ = "blocked_intervals"
    __table_args__ = (
        CheckConstraint("start_time < end_time", name="chk_blocked_intervals_time_order"),
        Index("idx_blocked_intervals_master_start", "master_id", "start_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
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
