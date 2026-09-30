"""Tests for Alembic migrations structure, revisions graph and upgrade/downgrade execution."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

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
    # 0003 is head, 0001 is base
    assert rev_ids[0] == "2026_09_30_0003"
    assert rev_ids[-1] == "2026_09_30_0001"


@requires_postgres
def test_alembic_stepwise_upgrade_and_downgrade_0003() -> None:
    """Explicitly verify 0002 -> 0003 upgrade and 0003 -> 0002 downgrade."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # Downgrade to 0002
    res_down_0002 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0002"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_0002.returncode == 0, f"Downgrade to 0002 failed: {res_down_0002.stderr}"

    # Upgrade to 0003
    res_up_0003 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2026_09_30_0003"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up_0003.returncode == 0, f"Upgrade 0002 -> 0003 failed: {res_up_0003.stderr}"

    # Downgrade back to 0002 to test reverse migration
    res_down_back = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0002"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down_back.returncode == 0, f"Downgrade 0003 -> 0002 failed: {res_down_back.stderr}"

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
