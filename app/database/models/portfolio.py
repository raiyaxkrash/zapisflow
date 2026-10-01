"""
Portfolio categories and items models with multi-staff support.
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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.staff import StaffMember


class PortfolioCategory(Base):
    """
    Category for grouping portfolio works (e.g. Nails, Pedicure, Brows, Hair).
    """
    __tablename__ = "portfolio_categories"
    __table_args__ = (
        Index("idx_portfolio_categories_master_active", "master_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    items: Mapped[List["PortfolioItem"]] = relationship(
        "PortfolioItem", back_populates="category", cascade="all, delete-orphan"
    )


class PortfolioItem(Base):
    """
    Individual photo item in the master's portfolio.
    staff_id is NULL -> General studio/project work.
    staff_id is NOT NULL -> Specific staff member work.
    """
    __tablename__ = "portfolio_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["master_id", "staff_id"],
            ["staff_members.master_id", "staff_members.id"],
            name="fk_portfolio_items_staff_master",
            ondelete="SET NULL",
        ),
        Index("idx_portfolio_items_master_staff", "master_id", "staff_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    staff_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolio_categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("services.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(String(255), nullable=False)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    category: Mapped["PortfolioCategory"] = relationship("PortfolioCategory", back_populates="items")
    staff: Mapped[Optional["StaffMember"]] = relationship(
        "StaffMember",
        foreign_keys="[PortfolioItem.master_id, PortfolioItem.staff_id]",
    )


from sqlalchemy import event, text


@event.listens_for(PortfolioItem, "before_insert")
def _auto_assign_portfolio_item_master(mapper, connection, target: PortfolioItem) -> None:
    """Ensure portfolio item has a valid master_id, resolved from its category if omitted."""
    if target.master_id is None and target.category_id is not None:
        row = connection.execute(
            text("SELECT master_id FROM portfolio_categories WHERE id = :cid"),
            {"cid": target.category_id},
        ).fetchone()
        if row:
            target.master_id = row[0]
