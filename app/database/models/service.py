"""
Service database model.
"""

from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, Enum as SQLEnum, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.database.models.appointment import Appointment


class DepositType(str, Enum):
    FIXED = "FIXED"
    PERCENT = "PERCENT"


class Service(Base, TimestampMixin):
    """
    Service offered by the master.
    """
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_min: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    
    deposit_type: Mapped[DepositType] = mapped_column(
        SQLEnum(DepositType, name="deposit_type_enum", native_enum=True),
        default=DepositType.FIXED,
        nullable=False,
    )
    deposit_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    appointments: Mapped[List["Appointment"]] = relationship(
        "Appointment", back_populates="service"
    )
