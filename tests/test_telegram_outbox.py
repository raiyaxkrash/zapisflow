"""PostgreSQL crash-window and multi-replica tests for tenant Telegram outbox."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import uuid

from aiogram import Bot
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterStatus
from app.database.models.telegram_outbox import TelegramOutbox, TelegramOutboxStatus
from app.database.models.user import User
from app.scheduler.jobs.telegram_outbox_worker import dispatch_telegram_outbox
from app.services.telegram_outbox import (
    bound_bot_instance_id,
    enqueue_telegram_edit,
    enqueue_telegram_message,
)
from tests.conftest import requires_postgres


@pytest_asyncio.fixture(autouse=True)
async def clean_outbox(pg_engine: AsyncEngine):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        await session.execute(delete(TelegramOutbox))
        await session.commit()
    yield
    async with sessions() as session:
        await session.execute(delete(TelegramOutbox))
        await session.commit()


async def _tenant(session):
    unique = uuid.uuid4().int
    user = User(telegram_id=unique % 10**12 + 10**12, first_name="Outbox owner")
    session.add(user)
    await session.flush()
    master = Master(owner_user_id=user.id, display_name="Outbox tenant", status=MasterStatus.ACTIVE)
    session.add(master)
    await session.flush()
    bot_instance = BotInstance(
        master_id=master.id,
        telegram_bot_id=unique % 10**12 + 2 * 10**12,
        encrypted_token="opaque-test-ciphertext",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
        token_version=1,
    )
    session.add(bot_instance)
    await session.flush()
    return master, bot_instance


def _registry(bot):
    registry = AsyncMock()
    registry.get_by_instance_id.return_value = bot
    return registry


@requires_postgres
@pytest.mark.asyncio
async def test_outbox_is_transactional_idempotent_and_rejects_secret_payload(pg_engine: AsyncEngine):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        master, instance = await _tenant(session)
        master_id, instance_id = master.id, instance.id
        await session.commit()

    key = f"outbox:test:{uuid.uuid4().hex}"
    async with sessions() as session:
        session.info.update(trusted_master_id=master_id, bot_instance_id=instance_id)
        assert bound_bot_instance_id(session, master_id) == instance_id
        with pytest.raises(ValueError):
            bound_bot_instance_id(session, master_id + 1)
        await enqueue_telegram_message(
            session, master_id=master_id, bot_instance_id=instance_id,
            chat_id=123456, text="Rolled back", idempotency_key=key,
        )
        await session.rollback()

    async with sessions() as session:
        assert (await session.execute(select(func.count(TelegramOutbox.id)).where(
            TelegramOutbox.idempotency_key == key
        ))).scalar_one() == 0

    async with sessions() as session:
        first = await enqueue_telegram_message(
            session, master_id=master_id, bot_instance_id=instance_id,
            chat_id=123456, text="Committed", idempotency_key=key,
        )
        await session.commit()
        first_id = first.id
    async with sessions() as session:
        replay = await enqueue_telegram_message(
            session, master_id=master_id, bot_instance_id=instance_id,
            chat_id=123456, text="Committed", idempotency_key=key,
        )
        assert replay.id == first_id
        with pytest.raises(ValueError):
            await enqueue_telegram_message(
                session, master_id=master_id, bot_instance_id=instance_id,
                chat_id=123456, text="Different content", idempotency_key=key,
            )
        with pytest.raises(ValueError):
            await enqueue_telegram_message(
                session, master_id=master_id, bot_instance_id=instance_id,
                chat_id=123456, text="123456789:AA0123456789012345678901234567890123",
                idempotency_key=key + ":secret",
            )


@requires_postgres
@pytest.mark.asyncio
async def test_two_replicas_do_not_send_same_outbox_row_concurrently(pg_engine: AsyncEngine):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        master, instance = await _tenant(session)
        row = await enqueue_telegram_message(
            session, master_id=master.id, bot_instance_id=instance.id,
            chat_id=123456, text="Send once", idempotency_key=f"outbox:test:{uuid.uuid4().hex}",
        )
        row_id = row.id
        await session.commit()

    started = asyncio.Event()
    release = asyncio.Event()
    bot = AsyncMock(spec=Bot)

    async def slow_send(**kwargs):
        # A separate PostgreSQL transaction can update this row while the
        # Telegram call is in flight. The worker must not hold a row lock or
        # an open business transaction across the external API call.
        async with sessions() as probe_session:
            await probe_session.execute(text("SET LOCAL statement_timeout = 1000"))
            await probe_session.execute(
                update(TelegramOutbox)
                .where(TelegramOutbox.id == row_id)
                .values(last_error="concurrent probe")
            )
            await probe_session.commit()
        started.set()
        await release.wait()

    bot.send_message.side_effect = slow_send
    registry = _registry(bot)
    first = asyncio.create_task(dispatch_telegram_outbox(
        registry=registry, session_maker=sessions, batch_size=1, lease_seconds=1,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(1.05)
        second_result = await dispatch_telegram_outbox(
            registry=registry, session_maker=sessions, batch_size=1, lease_seconds=1,
        )
        assert second_result == 0
    finally:
        release.set()
        assert await asyncio.wait_for(first, timeout=5) == 1
    assert bot.send_message.await_count == 1
    async with sessions() as session:
        assert (await session.get(TelegramOutbox, row_id)).status == TelegramOutboxStatus.SENT


@requires_postgres
@pytest.mark.asyncio
async def test_crash_after_telegram_acceptance_can_retry_notification(pg_engine: AsyncEngine):
    """Business/outbox row remains one; Telegram can see two sends after ambiguous crash."""
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        master, instance = await _tenant(session)
        key = f"outbox:test:{uuid.uuid4().hex}"
        row = await enqueue_telegram_message(
            session, master_id=master.id, bot_instance_id=instance.id,
            chat_id=123456, text="At least once", idempotency_key=key,
        )
        row_id = row.id
        await session.commit()

    class SimulatedProcessCrash(BaseException):
        pass

    async def crash_after_send(_row_id):
        raise SimulatedProcessCrash()

    bot = AsyncMock(spec=Bot)
    registry = _registry(bot)
    with pytest.raises(SimulatedProcessCrash):
        await dispatch_telegram_outbox(
            registry=registry, session_maker=sessions, batch_size=1, lease_seconds=1,
            after_send=crash_after_send,
        )
    assert bot.send_message.await_count == 1
    async with sessions() as session:
        row = await session.get(TelegramOutbox, row_id)
        assert row.status == TelegramOutboxStatus.PROCESSING
        row.claimed_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        await session.commit()

    assert await dispatch_telegram_outbox(
        registry=registry, session_maker=sessions, batch_size=1, lease_seconds=1,
    ) == 1
    assert bot.send_message.await_count == 2
    async with sessions() as session:
        row = await session.get(TelegramOutbox, row_id)
        assert row.status == TelegramOutboxStatus.SENT
        assert row.attempts == 2
        assert (await session.execute(select(func.count(TelegramOutbox.id)).where(
            TelegramOutbox.idempotency_key == key
        ))).scalar_one() == 1


@requires_postgres
@pytest.mark.asyncio
async def test_edit_uses_existing_message_id_after_commit(pg_engine: AsyncEngine):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        master, instance = await _tenant(session)
        row = await enqueue_telegram_edit(
            session, master_id=master.id, bot_instance_id=instance.id,
            chat_id=123456, message_id=42, text="Updated card",
            idempotency_key=f"outbox:edit:{uuid.uuid4().hex}",
        )
        row_id = row.id
        await session.commit()
    bot = AsyncMock(spec=Bot)
    assert await dispatch_telegram_outbox(registry=_registry(bot), session_maker=sessions, batch_size=1) == 1
    bot.edit_message_text.assert_awaited_once()
    assert bot.edit_message_text.await_args.kwargs["message_id"] == 42
    async with sessions() as session:
        assert (await session.get(TelegramOutbox, row_id)).status == TelegramOutboxStatus.SENT


@requires_postgres
@pytest.mark.asyncio
async def test_concurrent_enqueue_uses_one_database_unique_delivery(pg_engine: AsyncEngine):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        master, instance = await _tenant(session)
        master_id, instance_id = master.id, instance.id
        await session.commit()
    key = f"outbox:concurrent:{uuid.uuid4().hex}"

    async def enqueue_once():
        async with sessions() as session:
            row = await enqueue_telegram_message(
                session, master_id=master_id, bot_instance_id=instance_id,
                chat_id=123456, text="One durable row", idempotency_key=key,
            )
            await session.commit()
            return row.id

    first_id, second_id = await asyncio.gather(enqueue_once(), enqueue_once())
    assert first_id == second_id
    async with sessions() as session:
        assert (await session.execute(select(func.count(TelegramOutbox.id)).where(
            TelegramOutbox.idempotency_key == key
        ))).scalar_one() == 1


@requires_postgres
@pytest.mark.asyncio
async def test_transient_delivery_failure_uses_backoff_then_retries(pg_engine: AsyncEngine):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        master, instance = await _tenant(session)
        row = await enqueue_telegram_message(
            session, master_id=master.id, bot_instance_id=instance.id,
            chat_id=123456, text="Retry", idempotency_key=f"outbox:retry:{uuid.uuid4().hex}",
        )
        row_id = row.id
        await session.commit()
    bot = AsyncMock(spec=Bot)
    bot.send_message.side_effect = [RuntimeError("temporary"), None]
    registry = _registry(bot)

    assert await dispatch_telegram_outbox(registry=registry, session_maker=sessions, batch_size=1) == 0
    async with sessions() as session:
        row = await session.get(TelegramOutbox, row_id)
        assert row.status == TelegramOutboxStatus.PENDING
        assert row.next_attempt_at > datetime.now(timezone.utc)
        assert row.last_error == "RuntimeError"
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    assert await dispatch_telegram_outbox(registry=registry, session_maker=sessions, batch_size=1) == 1
    assert bot.send_message.await_count == 2
    async with sessions() as session:
        row = await session.get(TelegramOutbox, row_id)
        assert row.status == TelegramOutboxStatus.SENT
        assert row.attempts == 2


@pytest.mark.asyncio
@requires_postgres
async def test_manager_payment_notice_is_durable_and_dispatches_without_tenant_bot(pg_engine, monkeypatch):
    from app.config.settings import settings
    from app.services.telegram_outbox import enqueue_manager_message
    from unittest.mock import patch
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        master, instance = await _tenant(session)
        owner = await session.get(User, master.owner_user_id)
        owner_chat = owner.telegram_id
        master_id = master.id
        row = await enqueue_manager_message(session, master_id=master.id,
            chat_id=owner_chat, text="Оплата успешно обработана", idempotency_key=f"manager-test:{uuid.uuid4()}")
        row_id = row.id
        assert row.bot_instance_id is None
    bot = AsyncMock()
    monkeypatch.setattr(settings, "manager_bot_token", "123456:fake-test-token")
    with patch("app.scheduler.jobs.telegram_outbox_worker.Bot", return_value=bot):
        assert await dispatch_telegram_outbox(registry=AsyncMock(), session_maker=sessions) == 1
    bot.send_message.assert_awaited_once()
    bot.session.close.assert_awaited_once()
    async with sessions() as session:
        assert (await session.get(TelegramOutbox, row_id)).status == TelegramOutboxStatus.SENT
    async with sessions.begin() as session:
        with pytest.raises(ValueError, match="tenant owner"):
            await enqueue_manager_message(session, master_id=master_id, chat_id=owner_chat+1,
                text="Wrong tenant", idempotency_key=f"manager-test:{uuid.uuid4()}")
