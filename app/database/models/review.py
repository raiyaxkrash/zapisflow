"""Tenant-scoped client reviews and ratings database model with staff attribution."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.master import Master
    from app.database.models.user import User
    from app.database.models.appointment import Appointment
    from app.database.models.staff import StaffMember


class Review(Base):
    """Client review and rating for completed visit, strictly tenant-scoped."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_reviews_rating_range"),
        UniqueConstraint("appointment_id", name="uq_reviews_appointment_id"),
        ForeignKeyConstraint(
            ["master_id", "appointment_id"],
            ["appointments.master_id", "appointments.id"],
            name="fk_reviews_appointment_master",
            ondelete="CASCADE",
        ),
        Index("ix_reviews_master_created", "master_id", "created_at"),
        Index("ix_reviews_master_staff", "master_id", "staff_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    appointment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    staff_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    master: Mapped["Master"] = relationship("Master")
    user: Mapped["User"] = relationship("User")
    appointment: Mapped["Appointment"] = relationship(
        "Appointment",
        foreign_keys="[Review.master_id, Review.appointment_id]",
        overlaps="master",
    )
    staff: Mapped[Optional["StaffMember"]] = relationship("StaffMember")
