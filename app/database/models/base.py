"""
Base declarative model and common mixins for SQLAlchemy 2.0 with PostgreSQL.
"""

from datetime import datetime
from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Naming convention for clean constraint and index naming
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)

    def __repr__(self) -> str:
        cols = []
        for col in self.__table__.columns.keys():
            cols.append(f"{col}={getattr(self, col, None)!r}")
        return f"<{self.__class__.__name__}({', '.join(cols)})>"


class TimestampMixin:
    """
    Mixin adding timezone-aware created_at and updated_at TIMESTAMPTZ timestamps.
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# Backward compatibility alias for any existing imports
def UTCDateTime() -> DateTime:
    return DateTime(timezone=True)
