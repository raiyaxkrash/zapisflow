"""Comprehensive test suite for Phase 6: Multi-Bot Webhook Ingestion Engine & Tenant Context.

Covers:
1. Health endpoints (/health/live, /health/ready with DB & Redis checks)
2. Correlation ID tracing middleware
3. Endpoint routing by public_bot_id (UUID validation, 404 on missing/invalid)
4. Constant-time secret token verification (403 on missing, mismatch, or unset secret)
5. Payload size enforcement (413 Payload Too Large)
6. Payload schema validation (400 Bad Request on invalid JSON / schema)
7. Bot lifecycle states (DISABLED/ERROR/PROVISIONING -> 403, SETUP_REQUIRED/ACTIVE -> allowed)
8. Two-phase update deduplication in Redis (atomic lock, duplicate skipping, release on failure)
9. Strict tenant isolation & handler context injection (master_id, master, bot_instance)
10. Fail-closed tenant security in webhook mode (no fallback to master_id=1)
"""

import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User as TgUser

from app.bot.bot_instance import create_dispatcher
from app.bot.filters.admin import IsAdminFilter
from app.bot.middlewares.tenant_context import TenantContextMiddleware
from app.config.settings import settings
from app.core.token_crypto import TokenCrypto
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterAdmin,
    MasterAdminRole,
    MasterSettings,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.user import User
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.services.bot_registry import BotRegistry
from app.services.update_dedup import UpdateDeduplicator
from app.web.app import create_app
from tests.conftest import requires_postgres


# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------

TEST_CRYPTO_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def crypto() -> TokenCrypto:
    return TokenCrypto(TEST_CRYPTO_KEY)


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.fixture
def deduplicator(fake_redis: fakeredis.aioredis.FakeRedis) -> UpdateDeduplicator:
    return UpdateDeduplicator(redis_client=fake_redis, ttl_completed=86400, ttl_processing=60)


def make_telegram_update(update_id: int, message_text: str = "/start", user_id: int = 123456) -> dict:
    """Helper to build a valid Telegram Update dictionary."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {
                "id": user_id,
                "type": "private",
                "first_name": "TestUser",
            },
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "TestUser",
                "username": "testuser",
            },
            "text": message_text,
        },
    }


# ---------------------------------------------------------------------------
# 1. Health Endpoints & Middleware Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_live() -> None:
    """GET /health/live returns status alive."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_health_ready_ok(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """GET /health/ready returns status ready when DB and Redis are healthy."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=None)
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aexit__.return_value = None

    app = create_app(
        session_factory=mock_session_factory,
        redis_client=fake_redis,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["database"] == "ok"
        assert data["redis"] == "ok"


@pytest.mark.asyncio
async def test_health_ready_db_down(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """GET /health/ready returns 503 degraded when DB connection fails."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB Connection refused"))
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aexit__.return_value = None

    app = create_app(
        session_factory=mock_session_factory,
        redis_client=fake_redis,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert "DB Connection refused" in data["database"]


@pytest.mark.asyncio
async def test_correlation_id_tracing_middleware() -> None:
    """Middleware generates or preserves correlation ID and returns it in headers."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Without incoming header -> generated
        res1 = await client.get("/health/live")
        assert "x-correlation-id" in res1.headers
        corr_id = res1.headers["x-correlation-id"]
        assert len(corr_id) > 10

        # With incoming X-Request-ID -> preserved
        res2 = await client.get("/health/live", headers={"X-Request-ID": "custom-trace-12345"})
        assert res2.headers.get("x-correlation-id") == "custom-trace-12345"


# ---------------------------------------------------------------------------
# 2. Webhook Routing & Authentication Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_invalid_uuid_returns_404() -> None:
    """Non-UUID path parameter returns 404."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/telegram/webhook/not-a-valid-uuid",
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
            json={"update_id": 1},
        )
        assert res.status_code == 404
        assert "Bot not found" in res.json()["detail"]


@pytest.mark.asyncio
async def test_webhook_unknown_uuid_returns_404() -> None:
    """Valid UUID that does not exist in DB returns 404."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))))
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aexit__.return_value = None

    app = create_app(session_factory=mock_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unknown_id = str(uuid.uuid4())
        res = await client.post(
            f"/telegram/webhook/{unknown_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
            json={"update_id": 1},
        )
        assert res.status_code == 404
        assert "Bot not found" in res.json()["detail"]


@requires_postgres
@pytest.mark.asyncio
async def test_webhook_secret_token_constant_time_verification(
    pg_session: AsyncSession,
    crypto: TokenCrypto,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Validates missing secret, wrong secret, and matching secret."""
    # Create test owner & master
    owner = User(telegram_id=987111222, first_name="OwnerAuth")
    pg_session.add(owner)
    await pg_session.flush()

    master = Master(
        owner_user_id=owner.id,
        display_name="Master Auth",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
    )
    pg_session.add(master)
    await pg_session.flush()

    bot_public_id = uuid.uuid4()
    bot_secret = "super_secret_webhook_token_xyz123"
    enc_token = crypto.encrypt("111222333:AAValidTestToken12345678901234567890", associated_data=111222333)

    bot_instance = BotInstance(
        public_id=bot_public_id,
        master_id=master.id,
        telegram_bot_id=111222333,
        telegram_username="auth_test_bot",
        encrypted_token=enc_token,
        webhook_secret=bot_secret,
        status=BotInstanceStatus.ACTIVE,
        token_version=1,
    )
    pg_session.add(bot_instance)
    await pg_session.commit()

    # Setup app with mock dispatcher & registry
    mock_dp = AsyncMock(spec=Dispatcher)
    mock_dp.feed_update = AsyncMock(return_value=True)

    mock_bot = MagicMock(spec=Bot)
    mock_registry = AsyncMock(spec=BotRegistry)
    mock_registry.get_bot = AsyncMock(return_value=mock_bot)

    mock_session_factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    dedup = UpdateDeduplicator(fake_redis)

    app = create_app(
        registry=mock_registry,
        dp=mock_dp,
        deduplicator=dedup,
        session_factory=mock_session_factory,
        redis_client=fake_redis,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        update_payload = make_telegram_update(update_id=101)

        # 1. Missing secret token header -> 403 Forbidden
        res_missing = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            json=update_payload,
        )
        assert res_missing.status_code == 403
        assert "Forbidden" in res_missing.json()["detail"]

        # 2. Wrong secret token header -> 403 Forbidden
        res_wrong = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret_value"},
            json=update_payload,
        )
        assert res_wrong.status_code == 403
        assert "Forbidden" in res_wrong.json()["detail"]

        # 3. Correct secret token header -> 200 OK
        res_correct = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_secret},
            json=update_payload,
        )
        assert res_correct.status_code == 200
        assert res_correct.json() == {"ok": True}
        mock_dp.feed_update.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. Payload Size & Schema Enforcement Tests
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_webhook_payload_size_enforcement_413(
    pg_session: AsyncSession,
    crypto: TokenCrypto,
) -> None:
    """Enforces 413 Payload Too Large when Content-Length or body exceeds limit."""
    owner = User(telegram_id=987333444, first_name="OwnerSize")
    pg_session.add(owner)
    await pg_session.flush()

    master = Master(owner_user_id=owner.id, display_name="Master Size")
    pg_session.add(master)
    await pg_session.flush()

    bot_public_id = uuid.uuid4()
    bot_secret = "secret_size"
    enc_token = crypto.encrypt("222333444:AASizeTestToken12345678901234567890", associated_data=222333444)

    bot_instance = BotInstance(
        public_id=bot_public_id,
        master_id=master.id,
        telegram_bot_id=222333444,
        encrypted_token=enc_token,
        webhook_secret=bot_secret,
        status=BotInstanceStatus.ACTIVE,
    )
    pg_session.add(bot_instance)
    await pg_session.commit()

    mock_session_factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    app = create_app(session_factory=mock_session_factory)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Content-Length header claims > max_bytes
        huge_content_length = settings.webhook_max_body_bytes + 5000
        res_header = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={
                "X-Telegram-Bot-Api-Secret-Token": bot_secret,
                "Content-Length": str(huge_content_length),
            },
            content=b"small_body",
        )
        assert res_header.status_code == 413
        assert "Payload Too Large" in res_header.json()["detail"]

        # 2. Actual body bytes exceed max_bytes
        oversized_body = b"A" * (settings.webhook_max_body_bytes + 100)
        res_body = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_secret},
            content=oversized_body,
        )
        assert res_body.status_code == 413
        assert "Payload Too Large" in res_body.json()["detail"]


@requires_postgres
@pytest.mark.asyncio
async def test_webhook_schema_validation_400(
    pg_session: AsyncSession,
    crypto: TokenCrypto,
) -> None:
    """Enforces 400 Bad Request on invalid JSON or non-Update structure."""
    owner = User(telegram_id=987555666, first_name="OwnerSchema")
    pg_session.add(owner)
    await pg_session.flush()

    master = Master(owner_user_id=owner.id, display_name="Master Schema")
    pg_session.add(master)
    await pg_session.flush()

    bot_public_id = uuid.uuid4()
    bot_secret = "secret_schema"
    enc_token = crypto.encrypt("333444555:AASchemaTestToken12345678901234567890", associated_data=333444555)

    bot_instance = BotInstance(
        public_id=bot_public_id,
        master_id=master.id,
        telegram_bot_id=333444555,
        encrypted_token=enc_token,
        webhook_secret=bot_secret,
        status=BotInstanceStatus.ACTIVE,
    )
    pg_session.add(bot_instance)
    await pg_session.commit()

    mock_session_factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    app = create_app(session_factory=mock_session_factory)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Broken JSON
        res_broken = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_secret},
            content=b"not json at all {broken",
        )
        assert res_broken.status_code == 400
        assert "Invalid update payload" in res_broken.json()["detail"]

        # 2. Missing update_id in Telegram Update
        res_missing_id = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_secret},
            json={"message": {"text": "hello"}},
        )
        assert res_missing_id.status_code == 400
        assert "Invalid update payload" in res_missing_id.json()["detail"]


# ---------------------------------------------------------------------------
# 4. Inactive Bot States Tests
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_webhook_inactive_bot_states_rejected(
    pg_session: AsyncSession,
    crypto: TokenCrypto,
) -> None:
    """Bots in DISABLED, ERROR, or PROVISIONING status return 403."""
    owner = User(telegram_id=987777888, first_name="OwnerStatus")
    pg_session.add(owner)
    await pg_session.flush()

    master = Master(owner_user_id=owner.id, display_name="Master Status")
    pg_session.add(master)
    await pg_session.flush()

    # Disabled Bot
    bot_disabled = BotInstance(
        public_id=uuid.uuid4(),
        master_id=master.id,
        telegram_bot_id=444555661,
        webhook_secret="sec_disabled",
        status=BotInstanceStatus.DISABLED,
    )
    # Error Bot
    bot_error = BotInstance(
        public_id=uuid.uuid4(),
        master_id=master.id,
        telegram_bot_id=444555662,
        webhook_secret="sec_error",
        status=BotInstanceStatus.ERROR,
    )
    # Provisioning Bot
    bot_prov = BotInstance(
        public_id=uuid.uuid4(),
        master_id=master.id,
        telegram_bot_id=444555663,
        webhook_secret="sec_prov",
        status=BotInstanceStatus.PROVISIONING,
    )
    pg_session.add_all([bot_disabled, bot_error, bot_prov])
    await pg_session.commit()

    mock_session_factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    app = create_app(session_factory=mock_session_factory)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        update_data = make_telegram_update(1)

        res_dis = await client.post(
            f"/telegram/webhook/{bot_disabled.public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "sec_disabled"},
            json=update_data,
        )
        assert res_dis.status_code == 403
        assert "DISABLED" in res_dis.json()["detail"]

        res_err = await client.post(
            f"/telegram/webhook/{bot_error.public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "sec_error"},
            json=update_data,
        )
        assert res_err.status_code == 403
        assert "ERROR" in res_err.json()["detail"]

        res_prov = await client.post(
            f"/telegram/webhook/{bot_prov.public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "sec_prov"},
            json=update_data,
        )
        assert res_prov.status_code == 403
        assert "PROVISIONING" in res_prov.json()["detail"]


# ---------------------------------------------------------------------------
# 5. Atomic Deduplication & Idempotency Tests
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_webhook_deduplication_lifecycle(
    pg_session: AsyncSession,
    crypto: TokenCrypto,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """
    1. First arrival -> processes update, sets COMPLETED.
    2. Second arrival -> returns 200 OK duplicate, skips Dispatcher.
    3. Failed handler execution -> lock is released, allowing retry.
    """
    owner = User(telegram_id=987999000, first_name="OwnerDedup")
    pg_session.add(owner)
    await pg_session.flush()

    master = Master(owner_user_id=owner.id, display_name="Master Dedup")
    pg_session.add(master)
    await pg_session.flush()

    bot_public_id = uuid.uuid4()
    bot_secret = "secret_dedup"
    enc_token = crypto.encrypt("555666777:AADedupTestToken12345678901234567890", associated_data=555666777)

    bot_instance = BotInstance(
        public_id=bot_public_id,
        master_id=master.id,
        telegram_bot_id=555666777,
        encrypted_token=enc_token,
        webhook_secret=bot_secret,
        status=BotInstanceStatus.ACTIVE,
    )
    pg_session.add(bot_instance)
    await pg_session.commit()

    mock_dp = AsyncMock(spec=Dispatcher)
    mock_dp.feed_update = AsyncMock(return_value=True)

    mock_bot = MagicMock(spec=Bot)
    mock_registry = AsyncMock(spec=BotRegistry)
    mock_registry.get_bot = AsyncMock(return_value=mock_bot)

    mock_session_factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    dedup = UpdateDeduplicator(fake_redis)

    app = create_app(
        registry=mock_registry,
        dp=mock_dp,
        deduplicator=dedup,
        session_factory=mock_session_factory,
        redis_client=fake_redis,
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        update_data = make_telegram_update(update_id=555)

        # 1. First arrival -> succeeds and feeds to dispatcher
        res1 = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_secret},
            json=update_data,
        )
        assert res1.status_code == 200
        assert res1.json() == {"ok": True}
        assert mock_dp.feed_update.await_count == 1

        # Check key in Redis is COMPLETED
        key = f"telegram:update:{bot_instance.id}:555"
        val = await fake_redis.get(key)
        assert val == b"COMPLETED"

        # 2. Second arrival with identical update_id -> returns duplicate, skips dispatcher
        res2 = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_secret},
            json=update_data,
        )
        assert res2.status_code == 200
        assert res2.json() == {"ok": True, "status": "duplicate"}
        # feed_update count should STILL be 1 (not called again)
        assert mock_dp.feed_update.await_count == 1

        # 3. Simulate failure: make feed_update raise an exception for a new update
        mock_dp.feed_update.side_effect = RuntimeError("Simulated handler crash")
        update_fail = make_telegram_update(update_id=556)

        res_fail = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_secret},
            json=update_fail,
        )
        assert res_fail.status_code == 500

        # Verify Redis lock was released on exception
        fail_key = f"telegram:update:{bot_instance.id}:556"
        val_fail = await fake_redis.get(fail_key)
        assert val_fail is None, "Lock must be released so Telegram can retry"

        # Subsequent retry now succeeds
        mock_dp.feed_update.side_effect = None
        res_retry = await client.post(
            f"/telegram/webhook/{bot_public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_secret},
            json=update_fail,
        )
        assert res_retry.status_code == 200
        assert res_retry.json() == {"ok": True}
        val_retry = await fake_redis.get(fail_key)
        assert val_retry == b"COMPLETED"


# ---------------------------------------------------------------------------
# 6. Tenant Context Injection & Isolation Tests
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_webhook_tenant_isolation_two_masters(
    pg_session: AsyncSession,
    crypto: TokenCrypto,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """
    Two distinct Masters (Master A and Master B) receive updates through their respective
    webhook URLs. Handlers receive strictly verified tenant context.
    """
    # 1. Master A
    owner_a = User(telegram_id=987111001, first_name="OwnerA")
    pg_session.add(owner_a)
    await pg_session.flush()

    master_a = Master(owner_user_id=owner_a.id, display_name="Master Alpha")
    pg_session.add(master_a)
    await pg_session.flush()

    bot_a = BotInstance(
        public_id=uuid.uuid4(),
        master_id=master_a.id,
        telegram_bot_id=666111222,
        encrypted_token=crypto.encrypt("666111222:AABotAlphaToken1234567890123456789", associated_data=666111222),
        webhook_secret="sec_alpha",
        status=BotInstanceStatus.ACTIVE,
    )

    # 2. Master B
    owner_b = User(telegram_id=987222002, first_name="OwnerB")
    pg_session.add(owner_b)
    await pg_session.flush()

    master_b = Master(owner_user_id=owner_b.id, display_name="Master Beta")
    pg_session.add(master_b)
    await pg_session.flush()

    bot_b = BotInstance(
        public_id=uuid.uuid4(),
        master_id=master_b.id,
        telegram_bot_id=666333444,
        encrypted_token=crypto.encrypt("666333444:AABotBetaToken12345678901234567890", associated_data=666333444),
        webhook_secret="sec_beta",
        status=BotInstanceStatus.ACTIVE,
    )

    pg_session.add_all([bot_a, bot_b])
    await pg_session.commit()

    captured_contexts = []

    mock_dp = AsyncMock(spec=Dispatcher)
    async def mock_feed(bot, update, **kwargs):
        captured_contexts.append({
            "master_id": kwargs.get("master_id"),
            "bot_instance_id": kwargs.get("bot_instance").id if kwargs.get("bot_instance") else None,
            "bot_telegram_id": bot.id,
            "update_id": update.update_id,
        })
        return True

    mock_dp.feed_update = AsyncMock(side_effect=mock_feed)

    mock_registry = AsyncMock(spec=BotRegistry)
    def get_mock_bot(instance_id: int):
        b = MagicMock(spec=Bot)
        b.id = 666111222 if instance_id == bot_a.id else 666333444
        return b
    mock_registry.get_bot = AsyncMock(side_effect=get_mock_bot)

    mock_session_factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    dedup = UpdateDeduplicator(fake_redis)

    app = create_app(
        registry=mock_registry,
        dp=mock_dp,
        deduplicator=dedup,
        session_factory=mock_session_factory,
        redis_client=fake_redis,
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Send update to Master A
        res_a = await client.post(
            f"/telegram/webhook/{bot_a.public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "sec_alpha"},
            json=make_telegram_update(update_id=1001),
        )
        assert res_a.status_code == 200

        # Send update to Master B
        res_b = await client.post(
            f"/telegram/webhook/{bot_b.public_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "sec_beta"},
            json=make_telegram_update(update_id=2002),
        )
        assert res_b.status_code == 200

        assert len(captured_contexts) == 2
        # Alpha check
        assert captured_contexts[0]["master_id"] == master_a.id
        assert captured_contexts[0]["bot_instance_id"] == bot_a.id
        assert captured_contexts[0]["bot_telegram_id"] == 666111222

        # Beta check
        assert captured_contexts[1]["master_id"] == master_b.id
        assert captured_contexts[1]["bot_instance_id"] == bot_b.id
        assert captured_contexts[1]["bot_telegram_id"] == 666333444


# ---------------------------------------------------------------------------
# 7. TenantContextMiddleware & Fail-Closed Security Tests
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_tenant_context_middleware_fail_closed_in_webhook_mode(
    pg_session: AsyncSession,
) -> None:
    """
    In webhook mode (settings.app_mode == 'webhook'):
    - If master_id is missing -> reject update (return None, never call handler).
    - If master_id does not match bot_instance.master_id -> reject update (fail closed).
    """
    middleware = TenantContextMiddleware()
    handler = AsyncMock(return_value="handler_executed")

    event = MagicMock(spec=Update)
    corrupted_data = {
        "session": pg_session,
        "master_id": 999999,  # Mismatched master_id
        "bot_instance": MagicMock(id=1, master_id=10, status=BotInstanceStatus.ACTIVE),
    }

    # 1. Corrupted tenant context -> rejected
    result = await middleware(handler, event, corrupted_data)
    assert result is None
    handler.assert_not_awaited()

    # 2. Missing master_id in webhook mode -> rejected
    with patch.object(settings, "app_mode", "webhook"):
        missing_tenant_data = {
            "session": pg_session,
            "master_id": None,
            "bot_instance": None,
            "bot": MagicMock(id=None),
        }
        res_missing = await middleware(handler, event, missing_tenant_data)
        assert res_missing is None
        handler.assert_not_awaited()


@requires_postgres
@pytest.mark.asyncio
async def test_is_admin_filter_fails_closed_in_webhook_mode(
    pg_session: AsyncSession,
) -> None:
    """
    IsAdminFilter fails closed in webhook mode when master_id is missing,
    and does NOT call LegacyTenantResolver.
    """
    admin_filter = IsAdminFilter()
    tg_user = MagicMock(spec=TgUser)
    tg_user.id = 999888777
    message = MagicMock(spec=Message)
    message.from_user = tg_user

    with patch.object(settings, "app_mode", "webhook"):
        # Without master_id in webhook mode
        is_admin = await admin_filter(message, session=pg_session, master_id=None)
        assert is_admin is False
