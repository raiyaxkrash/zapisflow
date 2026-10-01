"""Session-level PostgreSQL locks for an in-flight external delivery.

These locks coordinate replicas without keeping a database transaction open
while awaiting Telegram. They cannot make Telegram and PostgreSQL atomic.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


REMINDER_LOCK_NAMESPACE = 24701
BROADCAST_LOCK_NAMESPACE = 24702
OUTBOX_LOCK_NAMESPACE = 24703


@asynccontextmanager
async def delivery_lock(engine: AsyncEngine, namespace: int, item_id: int) -> AsyncIterator[bool]:
    """Try a session lock, releasing it even when delivery raises.

    A dedicated checked-out connection is required: returning it to the pool
    while the lock is held could transfer lock ownership to another worker.
    """
    async with engine.connect() as connection:
        acquired = bool((await connection.execute(
            text("SELECT pg_try_advisory_lock(:namespace, :item_id)"),
            {"namespace": namespace, "item_id": item_id},
        )).scalar_one())
        await connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :item_id)"),
                    {"namespace": namespace, "item_id": item_id},
                )
                await connection.commit()


async def delivery_is_in_flight(connection, namespace: int, item_id: int) -> bool:
    """Return whether another PostgreSQL connection owns the send lock."""
    acquired = bool((await connection.execute(
        text("SELECT pg_try_advisory_xact_lock(:namespace, :item_id)"),
        {"namespace": namespace, "item_id": item_id},
    )).scalar_one())
    return not acquired
