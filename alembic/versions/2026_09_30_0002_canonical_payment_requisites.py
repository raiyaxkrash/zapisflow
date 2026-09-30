"""Move legacy payment requisites to the keys used by admin and client screens.

Revision ID: 2026_09_30_0002
Revises: 2026_09_30_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2026_09_30_0002"
down_revision: str | None = "2026_09_30_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REQUISITE_KEYS = (
    ("default_card_number", "bank_card_number"),
    ("default_bank_name", "bank_name"),
    ("default_recipient_name", "bank_recipient_name"),
)


def _copy_if_absent(source: str, target: str) -> None:
    # Fixed migration-owned keys only. Existing target values take precedence.
    op.execute(
        "INSERT INTO settings (key, value, description, updated_at) "
        f"SELECT '{target}', value, description, updated_at "
        f"FROM settings WHERE key = '{source}' "
        "ON CONFLICT (key) DO NOTHING"
    )


def upgrade() -> None:
    for legacy_key, canonical_key in REQUISITE_KEYS:
        _copy_if_absent(legacy_key, canonical_key)
        op.execute(f"DELETE FROM settings WHERE key = '{legacy_key}'")


def downgrade() -> None:
    # Keep canonical rows too: downgrades must not discard values an admin edited.
    for legacy_key, canonical_key in REQUISITE_KEYS:
        _copy_if_absent(canonical_key, legacy_key)
