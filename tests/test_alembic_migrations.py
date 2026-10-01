"""Tests for Alembic migrations structure, revisions graph and upgrade/downgrade execution."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import UniqueConstraint

from app.database.models.subscription import SubscriptionPayment

from tests.conftest import POSTGRES_AVAILABLE, TEST_DATABASE_URL, requires_postgres

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_revision_graph_consistency() -> None:
    """Verify that Alembic migrations form a consistent forward and backward DAG."""
    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script_dir = ScriptDirectory.from_config(alembic_cfg)

    # Revisions must have a single head
    heads = script_dir.get_heads()
    assert len(heads) == 1, f"Expected exactly 1 migration head, got: {heads}"
    head_rev = heads[0]

    # Trace back to base
    revs = list(script_dir.walk_revisions(base="base", head=head_rev))
    rev_ids = [r.revision for r in revs]

    assert "2026_09_30_0001" in rev_ids
    assert "2026_09_30_0002" in rev_ids
    assert "2026_09_30_0003" in rev_ids
    assert "2026_09_30_0004" in rev_ids
    assert "2026_09_30_0005" in rev_ids
    assert "2026_09_30_0006" in rev_ids
    assert "2026_10_01_0007" in rev_ids
    assert "2026_10_01_0008" in rev_ids
    assert "2026_10_01_0009" in rev_ids
    assert "2026_10_01_0010" in rev_ids
    assert "2026_10_01_0011" in rev_ids
    assert "2026_10_01_0012" in rev_ids
    assert "2026_10_01_0013" in rev_ids
    assert "2026_10_01_0014" in rev_ids
    assert "2026_10_01_0015" in rev_ids
    assert "2026_10_01_0016" in rev_ids
    assert "2026_10_01_0017" in rev_ids
    # 0017 is head, 0001 is base
    assert rev_ids[0] == "2026_10_01_0017"
    assert rev_ids[-1] == "2026_09_30_0001"


def test_subscription_checkout_reference_is_nullable_and_unique() -> None:
    """Existing payments keep NULL while new checkout references cannot collide."""
    table = SubscriptionPayment.__table__
    assert table.c.checkout_ref.nullable is True
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_subscription_payment_checkout_ref"
        and tuple(column.name for column in constraint.columns) == ("checkout_ref",)
        for constraint in table.constraints
    )


@requires_postgres
def test_alembic_stepwise_upgrade_and_downgrade_0010() -> None:
    """Explicitly verify 0009 -> 0010 upgrade and 0010 -> 0009 downgrade."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # Downgrade to 0009
    res_down_0009 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_10_01_0009"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_0009.returncode == 0, f"Downgrade to 0009 failed: {res_down_0009.stderr}"

    # Upgrade to 0010
    res_up_0010 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2026_10_01_0010"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up_0010.returncode == 0, f"Upgrade 0009 -> 0010 failed: {res_up_0010.stderr}"

    # Downgrade back to 0009 to test reverse migration
    res_down_back = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_10_01_0009"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_back.returncode == 0, f"Downgrade 0010 -> 0009 failed: {res_down_back.stderr}"

    # Restore to head
    res_head = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_head.returncode == 0, f"Restore to head failed: {res_head.stderr}"


@requires_postgres
def test_alembic_stepwise_upgrade_and_downgrade_0009() -> None:
    """Explicitly verify 0008 -> 0009 upgrade and 0009 -> 0008 downgrade."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # Downgrade to 0008
    res_down_0008 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_10_01_0008"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_0008.returncode == 0, f"Downgrade to 0008 failed: {res_down_0008.stderr}"

    # Upgrade to 0009
    res_up_0009 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2026_10_01_0009"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up_0009.returncode == 0, f"Upgrade 0008 -> 0009 failed: {res_up_0009.stderr}"

    # Downgrade back to 0008 to test reverse migration
    res_down_back = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_10_01_0008"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_back.returncode == 0, f"Downgrade 0009 -> 0008 failed: {res_down_back.stderr}"

    # Restore to head
    res_head = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_head.returncode == 0, f"Restore to head failed: {res_head.stderr}"


@requires_postgres
def test_alembic_stepwise_upgrade_and_downgrade_0008() -> None:
    """Explicitly verify 0007 -> 0008 upgrade and 0008 -> 0007 downgrade."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # Downgrade to 0007
    res_down_0007 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_10_01_0007"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_0007.returncode == 0, f"Downgrade to 0007 failed: {res_down_0007.stderr}"

    # Upgrade to 0008
    res_up_0008 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2026_10_01_0008"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up_0008.returncode == 0, f"Upgrade 0007 -> 0008 failed: {res_up_0008.stderr}"

    # Downgrade back to 0007 to test reverse migration
    res_down_back = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_10_01_0007"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_back.returncode == 0, f"Downgrade 0008 -> 0007 failed: {res_down_back.stderr}"

    # Restore to head
    res_head = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_head.returncode == 0, f"Restore to head failed: {res_head.stderr}"


@requires_postgres
def test_alembic_stepwise_upgrade_and_downgrade_0007() -> None:
    """Explicitly verify 0006 -> 0007 upgrade and 0007 -> 0006 downgrade."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # Downgrade to 0006
    res_down_0006 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0006"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_0006.returncode == 0, f"Downgrade to 0006 failed: {res_down_0006.stderr}"

    # Upgrade to 0007
    res_up_0007 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2026_10_01_0007"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up_0007.returncode == 0, f"Upgrade 0006 -> 0007 failed: {res_up_0007.stderr}"

    # Downgrade back to 0006 to test reverse migration
    res_down_back = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0006"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_back.returncode == 0, f"Downgrade 0007 -> 0006 failed: {res_down_back.stderr}"

    # Restore to head
    res_head = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_head.returncode == 0, f"Restore to head failed: {res_head.stderr}"


@requires_postgres
def test_alembic_stepwise_upgrade_and_downgrade_0006() -> None:
    """Explicitly verify 0005 -> 0006 upgrade and 0006 -> 0005 downgrade."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # Downgrade to 0005
    res_down_0005 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0005"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_0005.returncode == 0, f"Downgrade to 0005 failed: {res_down_0005.stderr}"

    # Upgrade to 0006
    res_up_0006 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2026_09_30_0006"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up_0006.returncode == 0, f"Upgrade 0005 -> 0006 failed: {res_up_0006.stderr}"

    # Downgrade back to 0005 to test reverse migration
    res_down_back = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0005"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_back.returncode == 0, f"Downgrade 0006 -> 0005 failed: {res_down_back.stderr}"

    # Restore to head
    res_head = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_head.returncode == 0, f"Restore to head failed: {res_head.stderr}"


@requires_postgres
def test_alembic_stepwise_upgrade_and_downgrade_0005() -> None:
    """Explicitly verify 0004 -> 0005 upgrade and 0005 -> 0004 downgrade."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # Downgrade to 0004
    res_down_0004 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0004"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_0004.returncode == 0, f"Downgrade to 0004 failed: {res_down_0004.stderr}"

    # Upgrade to 0005
    res_up_0005 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2026_09_30_0005"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up_0005.returncode == 0, f"Upgrade 0004 -> 0005 failed: {res_up_0005.stderr}"

    # Downgrade back to 0004 to test reverse migration
    res_down_back = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0004"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_back.returncode == 0, f"Downgrade 0005 -> 0004 failed: {res_down_back.stderr}"

    # Restore to head
    res_head = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_head.returncode == 0, f"Upgrade to head failed: {res_head.stderr}"


@requires_postgres
def test_alembic_upgrade_downgrade_cycle() -> None:
    """When PostgreSQL is available, execute upgrade head -> downgrade base -> upgrade head."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # 1. Upgrade to head
    res_up1 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up1.returncode == 0, f"Alembic upgrade head failed: {res_up1.stderr}"

    # 2. Downgrade to base
    res_down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down.returncode == 0, f"Alembic downgrade base failed: {res_down.stderr}"

    # 3. Upgrade to head again
    res_up2 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up2.returncode == 0, f"Second Alembic upgrade head failed: {res_up2.stderr}"
