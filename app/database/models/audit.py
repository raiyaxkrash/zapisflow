"""
Administrative audit log model.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional
from sqlalchemy import BigInteger, DateTime, JSON, Integer, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.user import Admin


class AuditLog(Base):
    """
    Log of administrative actions for transparency and security.
    Hybrid model: master_id IS NULL for platform events, NOT NULL for tenant events.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_logs_master_created", "master_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admins.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_before: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    payload_after: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    admin: Mapped["Admin"] = relationship("Admin", back_populates="audit_logs")
