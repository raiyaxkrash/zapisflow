"""PostgreSQL regressions for durable broadcast recipient delivery."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
import pytest
from sqlalchemy import func, select
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.database.models.broadcast import Broadcast, BroadcastRecipient, BroadcastStatus, RecipientStatus
from app.database.models.master import MasterClient
from app.database.models.master import SubscriptionStatus
from app.services.broadcast_service import BroadcastService
from app.services.exceptions import SubscriptionExpiredError
from app.scheduler.jobs.broadcast_worker import due_campaigns_query, resume_broadcasts
from app.utils.delivery_lock import BROADCAST_LOCK_NAMESPACE, delivery_lock
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


@requires_postgres
@pytest.mark.asyncio
async def test_broadcast_accepted_then_worker_crashes_before_sent_is_retried(
    pg_engine: AsyncEngine,
) -> None:
    """A Telegram-accepted send can repeat after crash; the recipient row stays unique."""
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = await _create_user(session, first_name="Broadcast crash owner")
        client = await _create_user(session, first_name="Broadcast crash client")
        master = await _create_master(session, owner.id, "Broadcast crash master")
        session.add(MasterClient(master_id=master.id, user_id=client.id, is_marketing_allowed=True))
        campaign = await BroadcastService(session).create_broadcast(master.id, "Crash window")
        master_id, campaign_id = master.id, campaign.id
        await session.commit()

    accepted = 0
    bot = AsyncMock(spec=Bot)

    async def accepted_then_cancel(**kwargs):
        nonlocal accepted
        accepted += 1
        # Model Telegram accepting the send and the process dying before the
        # next PostgreSQL write can mark the recipient SENT.
        asyncio.current_task().cancel()
        return MagicMock()

    bot.send_message.side_effect = accepted_then_cancel
    async def crashed_worker():
        async with sessions() as session:
            await BroadcastService(session).execute_broadcast(master_id, campaign_id, bot=bot)

    worker = asyncio.create_task(crashed_worker())
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert accepted == 1

    async with sessions() as session:
        recipient = (await session.execute(select(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == campaign_id,
            BroadcastRecipient.duplicate_of_id.is_(None),
        ))).scalar_one()
        assert recipient.status == RecipientStatus.PROCESSING
        recipient.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        await session.commit()

    recovering_bot = AsyncMock(spec=Bot)
    recovering_bot.send_message.return_value = MagicMock()
    async with sessions() as session:
        result = await BroadcastService(session).execute_broadcast(
            master_id, campaign_id, bot=recovering_bot, worker_id="recovery",
        )
        assert result.status == BroadcastStatus.COMPLETED
    recovering_bot.send_message.assert_awaited_once()
    async with sessions() as session:
        rows = (await session.execute(select(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == campaign_id,
            BroadcastRecipient.duplicate_of_id.is_(None),
        ))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == RecipientStatus.SENT
        assert rows[0].attempt_count == 2


@requires_postgres
@pytest.mark.asyncio
async def test_broadcast_queue_is_invisible_until_commit_then_resumer_delivers(
    pg_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = await _create_user(session, first_name="Broadcast queued owner")
        client = await _create_user(session, first_name="Broadcast queued client")
        master = await _create_master(session, owner.id, "Broadcast queued master")
        session.add(MasterClient(master_id=master.id, user_id=client.id, is_marketing_allowed=True))
        campaign = await BroadcastService(session).create_broadcast(master.id, "Queued delivery")
        master_id, campaign_id = master.id, campaign.id
        await session.commit()

    bot = AsyncMock(spec=Bot)
    bot.send_message.return_value = MagicMock()
    registry = MagicMock()
    registry.get_by_master_id = AsyncMock(return_value=bot)
    async with sessions() as session:
        queued = await BroadcastService(session).queue_broadcast(master_id, campaign_id)
        assert queued.status == BroadcastStatus.SENDING
        async with sessions() as observer:
            visible_status = (await observer.execute(select(Broadcast.status).where(
                Broadcast.id == campaign_id
            ))).scalar_one()
            assert visible_status == BroadcastStatus.DRAFT
        await resume_broadcasts(registry=registry, session_maker=sessions)
        assert not any(call.kwargs.get("chat_id") == client.telegram_id
                       for call in bot.send_message.await_args_list)
        await session.commit()

    assert await resume_broadcasts(registry=registry, session_maker=sessions) >= 1
    assert sum(call.kwargs.get("chat_id") == client.telegram_id
               for call in bot.send_message.await_args_list) == 1
    async with sessions() as session:
        rows = (await session.execute(select(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == campaign_id,
            BroadcastRecipient.duplicate_of_id.is_(None),
        ))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == RecipientStatus.SENT


@requires_postgres
@pytest.mark.asyncio
async def test_broadcast_transient_failure_has_durable_backoff(pg_engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = await _create_user(session, first_name="Broadcast retry owner")
        client = await _create_user(session, first_name="Broadcast retry client")
        master = await _create_master(session, owner.id, "Broadcast retry master")
        session.add(MasterClient(master_id=master.id, user_id=client.id, is_marketing_allowed=True))
        campaign = await BroadcastService(session).create_broadcast(master.id, "Retry delivery")
        master_id, campaign_id = master.id, campaign.id
        await session.commit()

    bot = AsyncMock(spec=Bot)
    bot.send_message.side_effect = RuntimeError("temporary transport failure")
    async with sessions() as session:
        result = await BroadcastService(session).execute_broadcast(master_id, campaign_id, bot=bot)
        assert result.status == BroadcastStatus.SENDING
    bot.send_message.assert_awaited_once()
    async with sessions() as session:
        recipient = (await session.execute(select(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == campaign_id,
            BroadcastRecipient.duplicate_of_id.is_(None),
        ))).scalar_one()
        assert recipient.status == RecipientStatus.PENDING
        assert recipient.next_attempt_at > datetime.now(timezone.utc)
        recipient.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    recovering_bot = AsyncMock(spec=Bot)
    recovering_bot.send_message.return_value = MagicMock()
    registry = MagicMock()
    registry.get_by_master_id = AsyncMock(return_value=recovering_bot)
    assert await resume_broadcasts(registry=registry, session_maker=sessions) >= 1
    assert sum(call.kwargs.get("chat_id") == client.telegram_id
               for call in recovering_bot.send_message.await_args_list) == 1
    async with sessions() as session:
        rows = (await session.execute(select(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == campaign_id,
            BroadcastRecipient.duplicate_of_id.is_(None),
        ))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == RecipientStatus.SENT
        assert rows[0].attempt_count == 2


@requires_postgres
@pytest.mark.asyncio
async def test_expired_subscription_cannot_queue_existing_draft(pg_engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = await _create_user(session, first_name="Broadcast expiry owner")
        master = await _create_master(session, owner.id, "Broadcast expiry master")
        campaign = await BroadcastService(session).create_broadcast(master.id, "Draft before expiry")
        campaign_id, master_id = campaign.id, master.id
        master.subscription_status = SubscriptionStatus.EXPIRED
        master.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
        master.paid_until = None
        await session.commit()

    async with sessions() as session:
        with pytest.raises(SubscriptionExpiredError):
            await BroadcastService(session).queue_broadcast(master_id, campaign_id)
        await session.rollback()
    async with sessions() as session:
        campaign = await session.get(Broadcast, campaign_id)
        assert campaign.status == BroadcastStatus.DRAFT


@requires_postgres
@pytest.mark.asyncio
async def test_delivery_lock_holds_no_open_transaction_during_external_call(
    pg_engine: AsyncEngine,
) -> None:
    item_id = 1234567
    async with delivery_lock(pg_engine, BROADCAST_LOCK_NAMESPACE, item_id) as acquired:
        assert acquired
        async with pg_engine.connect() as observer:
            xact_starts = (await observer.execute(text(
                "SELECT a.xact_start FROM pg_locks l "
                "JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE l.locktype = 'advisory' AND l.granted "
                "AND l.classid::integer = :namespace AND l.objid::integer = :item_id"
            ), {"namespace": BROADCAST_LOCK_NAMESPACE, "item_id": item_id})).scalars().all()
        assert len(xact_starts) == 1
        assert xact_starts[0] is None
        async with delivery_lock(pg_engine, BROADCAST_LOCK_NAMESPACE, item_id) as second:
            assert second is False


@requires_postgres
@pytest.mark.asyncio
async def test_broadcast_resumer_skips_future_retry_before_due_campaign(
    pg_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with sessions() as session:
        owner = await _create_user(session, first_name="Broadcast fairness owner")
        waiting_client = await _create_user(session, first_name="Broadcast waiting client")
        due_client = await _create_user(session, first_name="Broadcast due client")
        master = await _create_master(session, owner.id, "Broadcast fairness master")
        waiting = await BroadcastService(session).create_broadcast(master.id, "Waiting retry")
        due = await BroadcastService(session).create_broadcast(master.id, "Due delivery")
        waiting.status = BroadcastStatus.SENDING
        due.status = BroadcastStatus.SENDING
        await session.flush()
        session.add_all([
            BroadcastRecipient(
                broadcast_id=waiting.id, user_id=waiting_client.id,
                status=RecipientStatus.PENDING,
                next_attempt_at=now + timedelta(hours=1),
            ),
            BroadcastRecipient(
                broadcast_id=due.id, user_id=due_client.id,
                status=RecipientStatus.PENDING,
            ),
        ])
        waiting_id, due_id = waiting.id, due.id
        await session.commit()

    async with sessions() as session:
        selected = (await session.execute(
            due_campaigns_query(now).where(Broadcast.id.in_((waiting_id, due_id))).limit(1)
        )).all()
        assert selected == [(due_id, master.id)]
