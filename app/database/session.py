"""
Database session management and asynchronous engine setup using SQLAlchemy 2.0.
"""

from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import settings
from app.database.models.base import Base

# Asynchronous engine with connection pooling and pre-ping healthcheck
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Thread-safe async session factory
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


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
    Initializes database extensions and creates tables if they don't exist.
    Used for local testing and initial bootstrap.
    """
    async with engine.begin() as conn:
        # Enable btree_gist extension for PostgreSQL exclusion constraints
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist;"))
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """
    Disposes all active engine connection pools.
    """
    await engine.dispose()
