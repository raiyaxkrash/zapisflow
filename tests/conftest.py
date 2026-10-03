"""Test configuration and fixtures for PostgreSQL integration and unit testing."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("PAYMENT_PROVIDER", "manual")

ROOT = Path(__file__).resolve().parents[1]

def _get_test_db_url() -> str:
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    try:
        from app.config.settings import settings
        if settings.database_url:
            from sqlalchemy.engine import make_url
            u = make_url(settings.database_url)
            return u.set(database="beauty_bot_test").render_as_string(hide_password=False)
    except Exception:
        pass
    return "postgresql+asyncpg://postgres:postgres_secure_password@localhost:5432/beauty_bot_test"



TEST_DATABASE_URL = _get_test_db_url()



@pytest.fixture(autouse=True)
def _ensure_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import settings
    if os.environ.get("APP_ENV", "test") != "production":
        monkeypatch.setattr(settings, "app_env", "test")
    if os.environ.get("PAYMENT_PROVIDER", "manual") != "yookassa_web":
        monkeypatch.setattr(settings, "payment_provider", "manual")



def is_postgres_available() -> bool:
    """Synchronous probe to check if PostgreSQL test server is reachable."""
    import socket

    try:
        from sqlalchemy.engine import make_url

        url = make_url(TEST_DATABASE_URL)
        host = url.host or "localhost"
        port = url.port or 5432
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except Exception:
        return False


POSTGRES_AVAILABLE = is_postgres_available()

requires_postgres = pytest.mark.skipif(
    not POSTGRES_AVAILABLE,
    reason="Live PostgreSQL is not available (run 'docker-compose up -d postgres' or set TEST_DATABASE_URL)",
)


@pytest.fixture(scope="session")
def postgres_url() -> str:
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def pg_engine(postgres_url: str) -> AsyncGenerator[AsyncEngine, None]:
    """Provides a connected AsyncEngine for the test PostgreSQL database."""
    if not POSTGRES_AVAILABLE:
        pytest.skip("PostgreSQL test server unavailable")

    engine = create_async_engine(postgres_url, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Provides an isolated transaction-rolled-back AsyncSession for tests."""
    connection = await pg_engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
    await transaction.rollback()
    await connection.close()
