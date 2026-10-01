"""PostgreSQL regressions for durable broadcast recipient delivery."""

import asyncio
from unittest.mock import AsyncMock

from aiogram import Bot
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.database.models.broadcast import BroadcastRecipient, BroadcastStatus, RecipientStatus
from app.database.models.master import MasterClient
from app.services.broadcast_service import BroadcastService
from tests.conftest import requires_postgres
from tests.test_phase_8_scheduler import _create_master, _create_user


@requires_postgres
@pytest.mark.asyncio
async def test_recipient_lock_prevents_second_replica_sending_during_slow_telegram_call(
    pg_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = await _create_user(session, first_name="Broadcast lock owner")
        client = await _create_user(session, first_name="Broadcast lock client")
        master = await _create_master(session, owner.id, "Broadcast lock master")
        session.add(MasterClient(master_id=master.id, user_id=client.id, is_marketing_allowed=True))
        broadcast = await BroadcastService(session).create_broadcast(master.id, "One delivery")
        master_id, broadcast_id = master.id, broadcast.id
        await session.commit()

    started = asyncio.Event()
    release = asyncio.Event()
    bot = AsyncMock(spec=Bot)

    async def slow_send(**kwargs):
        started.set()
        await release.wait()

    bot.send_message.side_effect = slow_send

    async def first_replica() -> None:
        async with sessions() as session:
            await BroadcastService(session).execute_broadcast(master_id, broadcast_id, bot=bot)

    worker = asyncio.create_task(first_replica())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        async with sessions() as session:
            # An expired lease cannot be reclaimed while the original replica
            # still holds the row lock through the Telegram call.
            assert await BroadcastService(session).reclaim_stale_recipients(
                broadcast_id, stale_timeout_minutes=0
            ) == 0
        async with sessions() as session:
            await BroadcastService(session).execute_broadcast(master_id, broadcast_id, bot=bot)
    finally:
        release.set()
        await asyncio.wait_for(worker, timeout=10)

    assert bot.send_message.await_count == 1
    async with sessions() as session:
        rows = (await session.execute(select(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == broadcast_id
        ))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == RecipientStatus.SENT


@requires_postgres
@pytest.mark.asyncio
async def test_active_broadcast_recipient_has_database_uniqueness(pg_engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = await _create_user(session, first_name="Broadcast unique owner")
        client = await _create_user(session, first_name="Broadcast unique client")
        master = await _create_master(session, owner.id, "Broadcast unique master")
        broadcast = await BroadcastService(session).create_broadcast(master.id, "Unique recipient")
        broadcast.status = BroadcastStatus.SENDING
        session.add(BroadcastRecipient(broadcast_id=broadcast.id, user_id=client.id))
        await session.commit()
        broadcast_id, client_id = broadcast.id, client.id

    async with sessions() as session:
        session.add(BroadcastRecipient(broadcast_id=broadcast_id, user_id=client_id))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with sessions() as session:
        count = (await session.execute(select(func.count(BroadcastRecipient.id)).where(
            BroadcastRecipient.broadcast_id == broadcast_id,
            BroadcastRecipient.duplicate_of_id.is_(None),
        ))).scalar_one()
        assert count == 1
