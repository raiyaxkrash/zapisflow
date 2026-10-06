"""Project branding defaults and two bounded, normalized media assets per tenant."""
from alembic import op
import sqlalchemy as sa
revision = "2026_10_05_0029"
down_revision = "2026_10_05_0028"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("master_settings", sa.Column("branding", sa.JSON(), server_default=sa.text("'{}'"), nullable=False))
    op.create_table("master_brand_assets", sa.Column("master_id", sa.BigInteger(), sa.ForeignKey("masters.id", ondelete="CASCADE"), primary_key=True), sa.Column("kind", sa.String(8), primary_key=True), sa.Column("revision", sa.String(64), nullable=False), sa.Column("content", sa.LargeBinary(), nullable=False), sa.CheckConstraint("kind IN ('logo', 'cover')", name="ck_brand_asset_kind"), sa.CheckConstraint("octet_length(content) <= 262144", name="ck_brand_asset_size"))

def downgrade():
    op.drop_table("master_brand_assets")
    op.drop_column("master_settings", "branding")
