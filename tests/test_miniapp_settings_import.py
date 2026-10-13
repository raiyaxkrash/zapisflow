"""Configuration import regression with Mini App enabled."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config.url_validation import miniapp_origin


@pytest.mark.parametrize("value", [
    "https://app.zapisflow.su", "https://example.com",
    "http://localhost:3000", "http://127.0.0.1:5173",
])
def test_valid_miniapp_origin(value):
    assert miniapp_origin(value) == value


@pytest.mark.parametrize("value", [
    "http://app.zapisflow.su", "https://user:pass@app.zapisflow.su",
    "https://app.zapisflow.su/path", "https://app.zapisflow.su/?x=1",
])
def test_invalid_miniapp_origin(value):
    with pytest.raises(ValueError):
        miniapp_origin(value)


@pytest.mark.parametrize("module", [
    "app.config.settings", "app.services.slot_engine",
    "app.services.miniapp_auth", "app.main",
])
def test_fresh_production_import_with_miniapp(module, tmp_path):
    # Empty cwd and fresh interpreter prevent .env and cached modules masking
    # configuration initialization defects. No live credentials are needed.
    env = os.environ.copy()
    env.update(APP_ENV="production", MINI_APP_BASE_URL="https://app.zapisflow.su",
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; from app.config.settings import Settings; assert Settings(_env_file=None).mini_app_base_url == 'https://app.zapisflow.su'"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stderr
