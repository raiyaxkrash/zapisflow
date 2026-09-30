"""
Database session management and asynchronous engine setup using SQLAlchemy 2.0 and asyncpg.
"""

from typing import AsyncGenerator
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Asynchronous PostgreSQL engine with configurable connection pooling and pre-ping healthcheck
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=settings.db_pool_pre_ping,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
)

# Thread-safe async session factory
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
async_session_maker = async_session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency generator for acquiring and releasing an async database session.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Fail-fast startup connectivity check and extension verification.
    Raises RuntimeError loudly if database is unreachable or btree_gist is missing.
    No silent fallback to SQLite.
    """
    try:
        async with engine.connect() as conn:
            # 1. Connectivity check
            await conn.execute(text("SELECT 1"))
            # 2. btree_gist extension check
            result = await conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
            )
            if not result.scalar():
                raise RuntimeError(
                    "PostgreSQL extension 'btree_gist' is missing! "
                    "Run 'CREATE EXTENSION IF NOT EXISTS btree_gist;' or 'alembic upgrade head'."
                )
    except Exception as exc:
        logger.critical(
            "Database connectivity check failed for %s: %s",
            settings.safe_database_url,
            exc,
        )
        raise RuntimeError(
            f"Failed to connect to PostgreSQL at {settings.safe_database_url}: {exc}"
        ) from exc


async def close_db() -> None:
    """
    Disposes all active engine connection pools.
    """
    await engine.dispose()
