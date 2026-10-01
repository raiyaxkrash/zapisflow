"""Regression tests for webhook startup, Redis deduplication and cache fencing."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
import pytest
from aiogram.types import Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.config.settings import settings
from app.database.models.processed_update import ProcessedWebhookUpdate
from app.database.models.setting import AppSetting
from app.services.bot_registry import BotRegistry
from app.services.update_dedup import (
    DedupState,
    DedupUnavailableError,
    UpdateDeduplicator,
)
from app.web.app import create_app, lifespan
from tests.test_webhook_gateway import make_telegram_update
from tests.conftest import requires_postgres


@pytest.mark.asyncio
async def test_concurrent_replicas_claim_only_one_update_and_retry_after_failure():
    redis = fakeredis.aioredis.FakeRedis()
    replica_a = UpdateDeduplicator(redis, ttl_processing=3)
    replica_b = UpdateDeduplicator(redis, ttl_processing=3)
    try:
        first, second = await asyncio.gather(
            replica_a.acquire(17, 991), replica_b.acquire(17, 991)
        )
        assert {first.state, second.state} == {
            DedupState.RECEIVED, DedupState.PROCESSING
        }
        owner = first.claim or second.claim
        assert owner is not None
        assert (await replica_b.acquire(17, 991)).state == DedupState.PROCESSING

        # A failed handler releases its claim so a Telegram retry can proceed.
        await replica_a.release(owner)
        retried = await replica_b.acquire(17, 991)
        assert retried.state == DedupState.RECEIVED
        assert retried.claim is not None
        await replica_b.complete(retried.claim)
        assert (await replica_a.acquire(17, 991)).state == DedupState.COMPLETED
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_old_owner_cannot_delete_or_complete_successor_claim():
    redis = fakeredis.aioredis.FakeRedis()
    dedup = UpdateDeduplicator(redis, ttl_processing=3)
    try:
        old = (await dedup.acquire_manager(1234)).claim
        assert old is not None
        await redis.delete(old.key)  # Simulate lease expiry before a second replica claims.
        new = (await dedup.acquire_manager(1234)).claim
        assert new is not None
        await dedup.release(old)
        assert (await dedup.acquire_manager(1234)).state == DedupState.PROCESSING
        with pytest.raises(DedupUnavailableError):
            await dedup.complete(old)
        await dedup.complete(new)
        assert (await dedup.acquire_manager(1234)).state == DedupState.COMPLETED
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_long_handler_renews_claim_across_processing_ttl():
    redis = fakeredis.aioredis.FakeRedis()
    dedup = UpdateDeduplicator(redis, ttl_processing=3)
    try:
        claim = (await dedup.acquire(19, 9)).claim
        assert claim is not None
        async with dedup.maintain(claim):
            await asyncio.sleep(4)
            assert (await dedup.acquire(19, 9)).state == DedupState.PROCESSING
        await dedup.complete(claim)
        assert (await dedup.acquire(19, 9)).state == DedupState.COMPLETED
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_dedup_fails_closed_when_redis_is_unavailable():
    dedup = UpdateDeduplicator(None)
    with pytest.raises(DedupUnavailableError):
        await dedup.acquire(1, 42)
    with pytest.raises(DedupUnavailableError):
        await dedup.acquire_manager(42)


@pytest.mark.asyncio
async def test_manager_webhook_returns_retryable_error_during_redis_outage(monkeypatch):
    monkeypatch.setattr(settings, "manager_webhook_secret", "test-secret")
    manager_dp = MagicMock()
    manager_dp.feed_update = AsyncMock()
    app = create_app(
        manager_bot=MagicMock(),
        manager_dp=manager_dp,
        deduplicator=UpdateDeduplicator(None),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/manager-webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=make_telegram_update(715),
        )
    assert response.status_code == 503
    manager_dp.feed_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_manager_webhook_retries_processing_update_then_acknowledges_completed(monkeypatch):
    monkeypatch.setattr(settings, "manager_webhook_secret", "test-secret")
    redis = fakeredis.aioredis.FakeRedis()
    entered = asyncio.Event()
    finish = asyncio.Event()
    manager_dp = MagicMock()

    async def slow_handler(*_args, **_kwargs):
        entered.set()
        await finish.wait()

    manager_dp.feed_update = AsyncMock(side_effect=slow_handler)
    app = create_app(
        manager_bot=MagicMock(),
        manager_dp=manager_dp,
        deduplicator=UpdateDeduplicator(redis),
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
            payload = make_telegram_update(714)
            first_task = asyncio.create_task(
                client.post("/telegram/manager-webhook", headers=headers, json=payload)
            )
            await asyncio.wait_for(entered.wait(), timeout=3)
            concurrent = await client.post(
                "/telegram/manager-webhook", headers=headers, json=payload
            )
            assert concurrent.status_code == 503
            assert manager_dp.feed_update.await_count == 1
            finish.set()
            first = await first_task
            assert first.status_code == 200
            completed = await client.post(
                "/telegram/manager-webhook", headers=headers, json=payload
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "duplicate"
            assert manager_dp.feed_update.await_count == 1
    finally:
        finish.set()
        await redis.aclose()


@pytest.mark.asyncio
async def test_production_lifespan_uses_real_registry_contract(monkeypatch):
    """Fresh webhook startup creates and closes the canonical BotRegistry."""
    import app.web.app as web_module

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "app_mode", "webhook")
    monkeypatch.setattr(settings, "webhook_base_url", "https://example.test")
    monkeypatch.setattr(settings, "manager_bot_token", "123456789:TEST_MANAGER_TOKEN")
    monkeypatch.setattr(settings, "manager_webhook_secret", "s" * 32)
    monkeypatch.setattr(settings, "bot_token_encryption_key", "01" * 32)
    monkeypatch.setattr(settings, "redis_password", "test-redis-password")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://test:test@example.test/zapisflow")
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(web_module, "init_db", AsyncMock())
    monkeypatch.setattr(web_module, "close_db", AsyncMock())

    redis = fakeredis.aioredis.FakeRedis()
    manager_bot = MagicMock()
    manager_bot.session.close = AsyncMock()
    app = create_app(
        redis_client=redis,
        dp=MagicMock(),
        manager_dp=MagicMock(),
        manager_bot=manager_bot,
    )
    try:
        async with lifespan(app):
            assert type(app.state.registry) is BotRegistry
            assert app.state.registry._listener_task is not None
            assert not app.state.registry._listener_task.done()
            assert app.state.deduplicator.redis is redis
        assert app.state.registry._listener_task is None
    finally:
        await redis.aclose()


@requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant:3001", "manager"])
async def test_postgres_ledger_blocks_replay_after_db_commit_before_redis_completion(
    pg_session: AsyncSession, monkeypatch, scope: str
):
    """A lost Redis completion cannot repeat already committed DB effects."""
    import app.bot.middlewares.db_session as middleware_module

    factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    monkeypatch.setattr(middleware_module, "async_session_factory", factory)
    middleware = DbSessionMiddleware()
    update_id = uuid.uuid4().int % 1_000_000_000
    setting_key = f"dedup_{uuid.uuid4().hex[:16]}"
    update = Update.model_validate(make_telegram_update(update_id))
    calls = 0

    async def business_handler(_event, data):
        nonlocal calls
        calls += 1
        data["session"].add(AppSetting(key=setting_key, value={"calls": calls}))

    data = {"webhook_update_scope": scope}
    await middleware(business_handler, update, data)
    # Simulate process death before Redis was changed to COMPLETED: invoke
    # the same update again through a fresh middleware DB session.
    await middleware(business_handler, update, {"webhook_update_scope": scope})

    assert calls == 1
    assert (await pg_session.get(AppSetting, setting_key)).value == {"calls": 1}
    assert await pg_session.get(ProcessedWebhookUpdate, (scope, update_id)) is not None


@requires_postgres
@pytest.mark.asyncio
async def test_post_commit_callbacks_run_only_after_successful_webhook_commit(
    pg_session: AsyncSession, monkeypatch
):
    import app.bot.middlewares.db_session as middleware_module

    factory = async_sessionmaker(
        pg_session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(middleware_module, "async_session_factory", factory)
    middleware = DbSessionMiddleware()
    scope = f"tenant:postcommit:{uuid.uuid4().hex}"
    events = []

    async def successful(_event, data):
        session = data["session"]
        session.add(AppSetting(key=f"postcommit_{uuid.uuid4().hex}", value={"ok": True}))

        async def callback():
            assert not session.in_transaction()
            events.append("after_commit")

        session.info.setdefault("post_commit", []).append(callback)

    await middleware(
        successful,
        Update.model_validate(make_telegram_update(985001)),
        {"webhook_update_scope": scope},
    )
    assert events == ["after_commit"]

    async def failing(_event, data):
        data["session"].info.setdefault("post_commit", []).append(
            lambda: events.append("wrong")
        )
        raise RuntimeError("handler crash")

    with pytest.raises(RuntimeError, match="handler crash"):
        await middleware(
            failing,
            Update.model_validate(make_telegram_update(985002)),
            {"webhook_update_scope": scope},
        )
    assert events == ["after_commit"]
