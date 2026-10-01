"""The committed environment example must parse with the real settings class."""

from pathlib import Path
import importlib

import pytest

from app.config.settings import Settings
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def test_admin_ids_in_env_example_parse_as_json(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    configured = Settings(_env_file=ROOT / ".env.example")
    assert configured.admin_ids == [123456789, 987654321]


def test_database_url_and_pool_settings() -> None:
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://myuser:mypass@db.host:5432/beauty_db",
        DB_POOL_SIZE=15,
        DB_MAX_OVERFLOW=25,
        DB_POOL_TIMEOUT=45,
    )
    assert make_url(configured.database_url).drivername == "postgresql+asyncpg"
    assert make_url(configured.database_url).database == "beauty_db"
    assert make_url(configured.sync_database_url).drivername == "postgresql"
    assert configured.db_pool_size == 15
    assert configured.db_max_overflow == 25
    assert configured.db_pool_timeout == 45
    assert "mypass" not in configured.safe_database_url


def test_asyncpg_is_in_canonical_dependencies() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "asyncpg==" in requirements
    assert "asyncpg>=" in pyproject
    assert importlib.import_module("asyncpg")


def test_redis_password_is_used_and_required_in_production() -> None:
    configured = Settings(_env_file=None, REDIS_PASSWORD="safe_password-1")
    assert configured.redis_url == "redis://:safe_password-1@localhost:6379/0"

    production = Settings(
        _env_file=None,
        APP_ENV="production",
        APP_MODE="webhook",
        REDIS_PASSWORD="",
    )
    with pytest.raises(ValueError, match="REDIS_PASSWORD is required"):
        production.validate_production_configuration()

    invalid = Settings(
        _env_file=None,
        APP_ENV="production",
        APP_MODE="webhook",
        REDIS_PASSWORD="bad password",
    )
    with pytest.raises(ValueError, match="REDIS_PASSWORD must use only URL-safe"):
        invalid.validate_production_configuration()
