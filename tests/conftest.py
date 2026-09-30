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

ROOT = Path(__file__).resolve().parents[1]

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres_secure_password@localhost:5432/beauty_bot_test",
)


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
