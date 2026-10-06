"""Bounded normalized brand media, never original uploads or base64 settings."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class MasterBrandAsset(Base):
    __tablename__ = "master_brand_assets"
    __table_args__ = (
        CheckConstraint("kind IN ('logo', 'cover')", name="ck_brand_asset_kind"),
        CheckConstraint("octet_length(content) <= 262144", name="ck_brand_asset_size"),
    )
    master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("masters.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
