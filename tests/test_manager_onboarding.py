"""Comprehensive test suite for Phase 7: Platform Manager Bot & Automated Tenant Onboarding.

Covers:
1. Candidate token validation (syntax, collision, duplicates, getMe).
2. Provisioning lifecycle (DB commit before webhook, setWebhook, status transition).
3. Webhook failure & retry mechanism.
4. Token rotation (same bot ID required, version increment, cache invalidation, mismatch rejection).
5. Disable / enable bot lifecycle.
6. Master readiness checklist & activation.
7. Partial unique index enforcement (at most 1 ACTIVE bot per master).
8. IDOR & multi-tenant access control protections.
9. Customer bot SETUP_REQUIRED gating & deep-link (/start admin) bypass.
10. Manager Webhook Endpoint (/telegram/manager-webhook) secret verification & deduplication.
11. Plaintext token scrubbing from FSM and deletion of user message.
"""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser

from app.bot.bot_instance import create_dispatcher
from app.config.settings import settings
from app.core.token_crypto import TokenCrypto
from app.database.models.audit import AuditLog
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
from app.database.models.schedule import ScheduleTemplate
from app.database.models.service import Service
from app.database.models.user import User
from app.manager_bot.handlers import (
    cb_confirm_bot_connection,
    cb_connect_bot,
    cb_project_card,
    cmd_start,
    msg_receive_bot_token,
)
from app.manager_bot.states import ConnectBotStates
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditEvent
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.bot_registry import BotRegistry
from app.services.exceptions import (
    AccessDeniedError,
    DuplicateBotError,
    InvalidBotTokenError,
    ManagerTokenCollisionError,
    ProvisioningWebhookError,
    TelegramGatewayError,
    TokenRotationBotMismatchError,
)
from app.services.master_readiness_service import MasterReadinessService
from app.services.telegram_provisioning_gateway import BotIdentity, TelegramProvisioningGateway
from app.services.update_dedup import UpdateDeduplicator
from app.web.app import create_app
from tests.conftest import requires_postgres


TEST_CRYPTO_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def setup_encryption_key():
    orig = settings.bot_token_encryption_key
    settings.bot_token_encryption_key = TEST_CRYPTO_KEY
    yield
    settings.bot_token_encryption_key = orig


@pytest.fixture
def crypto() -> TokenCrypto:
    return TokenCrypto(TEST_CRYPTO_KEY)


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.fixture
def deduplicator(fake_redis: fakeredis.aioredis.FakeRedis) -> UpdateDeduplicator:
    return UpdateDeduplicator(redis_client=fake_redis, ttl_completed=86400, ttl_processing=60)


class MockGateway(TelegramProvisioningGateway):
    """Mock gateway preventing any real Telegram network calls during automated testing."""

    def __init__(self) -> None:
        super().__init__()
        self.validate_token_mock = AsyncMock(
            return_value=BotIdentity(id=7770001, username="test_tenant_bot", first_name="Tenant Bot")
        )
        self.set_webhook_mock = AsyncMock(return_value=True)
        self.delete_webhook_mock = AsyncMock(return_value=True)
        self.get_webhook_info_mock = AsyncMock(return_value=MagicMock(url="https://example.com"))

    async def validate_token(self, token: str) -> BotIdentity:
        return await self.validate_token_mock(token)

    async def set_webhook(self, token: str, url: str, secret_token=None, **kwargs) -> bool:
        return await self.set_webhook_mock(token=token, url=url, secret_token=secret_token, **kwargs)

    async def delete_webhook(self, token: str, drop_pending_updates: bool = False) -> bool:
        return await self.delete_webhook_mock(token=token, drop_pending_updates=drop_pending_updates)


async def _create_user(session: AsyncSession, tg_id: int, username: str = "user") -> User:
    repo = UserRepository(session)
    user, _ = await repo.get_or_create(telegram_id=tg_id, first_name="Test", username=username)
    await session.commit()
    return user


async def _create_master(session: AsyncSession, owner_user: User, name: str = "Test Studio") -> Master:
    master = Master(
        owner_user_id=owner_user.id,
        display_name=name,
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.TRIAL,
        timezone="Europe/Moscow",
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    session.add(master)
    await session.flush()

    admin = MasterAdmin(master_id=master.id, user_id=owner_user.id, role=MasterAdminRole.OWNER)
    session.add(admin)

    settings_obj = MasterSettings(
        master_id=master.id,
        bank_card_number="2200111122223333",
        bank_name="TestBank",
        bank_recipient_name="Тест Т.Т.",
    )
    session.add(settings_obj)
    await session.commit()
    return master


# ===========================================================================
# 1. Candidate Token Validation Tests
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_validate_candidate_token_manager_collision(pg_session: AsyncSession, crypto: TokenCrypto):
    """Attempting to connect platform manager bot token is immediately rejected."""
    user = await _create_user(pg_session, 1001)
    gateway = MockGateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)

    with patch.object(settings, "manager_bot_token", "999999:MANAGER_SECRET_TOKEN"):
        with pytest.raises(ManagerTokenCollisionError):
            await service.validate_candidate_token(
                actor_user_id=user.id,
                token="999999:MANAGER_SECRET_TOKEN",
            )


@requires_postgres
@pytest.mark.asyncio
async def test_validate_candidate_token_duplicate_bot_id(pg_session: AsyncSession, crypto: TokenCrypto):
    """Candidate bot ID that already belongs to an ACTIVE bot instance raises DuplicateBotError."""
    user1 = await _create_user(pg_session, 1002)
    master1 = await _create_master(pg_session, user1)

    bot_repo = BotInstanceRepository(pg_session)
    enc = crypto.encrypt("7770001:EXISTING_TOKEN", associated_data=7770001)
    await bot_repo.create_bot_instance(
        master_id=master1.id,
        telegram_bot_id=7770001,
        encrypted_token=enc,
        status=BotInstanceStatus.ACTIVE,
    )
    await pg_session.commit()

    user2 = await _create_user(pg_session, 1003)
    gateway = MockGateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)

    with pytest.raises(DuplicateBotError):
        await service.validate_candidate_token(
            actor_user_id=user2.id,
            token="7770001:SOME_NEW_TOKEN",
        )


# ===========================================================================
# 2. Provisioning Lifecycle & DB Commit Before Webhook
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_provision_bot_happy_path(pg_session: AsyncSession, crypto: TokenCrypto):
    """Happy path: DB commits PROVISIONING -> setWebhook succeeds -> SETUP_REQUIRED."""
    user = await _create_user(pg_session, 2001)
    master = await _create_master(pg_session, user)

    gateway = MockGateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)

    identity = BotIdentity(id=2001001, username="studio_bot", first_name="Studio Bot")
    raw_token = "2001001:VALID_TELEGRAM_TOKEN_AAAAAA"

    bot_instance = await service.provision_bot(
        master_id=master.id,
        actor_user_id=user.id,
        token=raw_token,
        bot_identity=identity,
    )

    assert bot_instance.status == BotInstanceStatus.SETUP_REQUIRED
    assert bot_instance.telegram_bot_id == 2001001
    assert bot_instance.telegram_username == "studio_bot"
    assert bot_instance.token_version == 1
    assert bot_instance.webhook_secret is not None
    assert len(bot_instance.webhook_secret) > 20

    # Ensure token is encrypted with AES-GCM and NOT stored in plaintext
    assert bot_instance.encrypted_token != raw_token
    assert raw_token not in bot_instance.encrypted_token
    decrypted = crypto.decrypt(bot_instance.encrypted_token, associated_data=identity.id)
    assert decrypted == raw_token

    # Verify setWebhook was called with the public_id in URL
    gateway.set_webhook_mock.assert_awaited_once()
    call_args = gateway.set_webhook_mock.call_args
    assert f"/telegram/webhook/{bot_instance.public_id}" in call_args.kwargs["url"]
    assert call_args.kwargs["secret_token"] == bot_instance.webhook_secret

    # Verify AuditLog created without plaintext token
    stmt = select(AuditLog).where(AuditLog.master_id == master.id)
    logs = (await pg_session.scalars(stmt)).all()
    actions = [l.action for l in logs]
    assert AuditEvent.BOT_PROVISION_STARTED in actions
    assert AuditEvent.BOT_CONNECTED in actions
    for l in logs:
        assert raw_token not in str(l.payload_after)


@requires_postgres
@pytest.mark.asyncio
async def test_provision_bot_webhook_failure_marks_error(pg_session: AsyncSession, crypto: TokenCrypto):
    """When setWebhook fails, BotInstance is committed in DB with ERROR status and error description."""
    user = await _create_user(pg_session, 2002)
    master = await _create_master(pg_session, user)

    gateway = MockGateway()
    gateway.set_webhook_mock = AsyncMock(side_effect=TelegramGatewayError("Bad webhook response"))
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)

    identity = BotIdentity(id=2002001, username="error_bot", first_name="Error Bot")
    raw_token = "2002001:VALID_TELEGRAM_TOKEN_BBBBBB"

    with pytest.raises(ProvisioningWebhookError):
        await service.provision_bot(
            master_id=master.id,
            actor_user_id=user.id,
            token=raw_token,
            bot_identity=identity,
        )

    # In DB, BotInstance should exist in ERROR status
    repo = BotInstanceRepository(pg_session)
    instance = await repo.get_by_telegram_bot_id(2002001)
    assert instance is not None
    assert instance.status == BotInstanceStatus.ERROR
    assert "Bad webhook response" in (instance.last_error or "")


@requires_postgres
@pytest.mark.asyncio
async def test_retry_provisioning_success(pg_session: AsyncSession, crypto: TokenCrypto):
    """Retrying provisioning on an ERROR bot instance re-attempts webhook and transitions to SETUP_REQUIRED."""
    user = await _create_user(pg_session, 2003)
    master = await _create_master(pg_session, user)

    gateway = MockGateway()
    # First fail
    gateway.set_webhook_mock = AsyncMock(side_effect=TelegramGatewayError("Telegram down"))
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)

    identity = BotIdentity(id=2003001, username="retry_bot", first_name="Retry Bot")
    raw_token = "2003001:VALID_TELEGRAM_TOKEN_CCCCCC"

    with pytest.raises(ProvisioningWebhookError):
        await service.provision_bot(master.id, user.id, raw_token, identity)

    repo = BotInstanceRepository(pg_session)
    instance = await repo.get_by_telegram_bot_id(2003001)
    assert instance.status == BotInstanceStatus.ERROR

    # Now Telegram recovers
    gateway.set_webhook_mock = AsyncMock(return_value=True)
    service.gateway = gateway

    retried = await service.retry_provisioning(instance.id, user.id)
    assert retried.status == BotInstanceStatus.SETUP_REQUIRED
    assert retried.last_error is None


# ===========================================================================
# 3. Token Rotation Tests
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_rotate_token_success(pg_session: AsyncSession, crypto: TokenCrypto):
    """Token rotation with the same bot ID advances version and updates ciphertext."""
    user = await _create_user(pg_session, 3001)
    master = await _create_master(pg_session, user)

    gateway = MockGateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)

    raw_token1 = "3001001:OLD_TOKEN_111111111111111111"
    raw_token2 = "3001001:NEW_ROTATED_TOKEN_2222222222"

    gateway.validate_token_mock = AsyncMock(
        return_value=BotIdentity(id=3001001, username="rotated_bot", first_name="Rotated")
    )

    bot_instance = await service.provision_bot(
        master.id, user.id, raw_token1, BotIdentity(3001001, "rotated_bot", "Rotated")
    )
    assert bot_instance.token_version == 1

    rotated = await service.rotate_token(bot_instance.id, user.id, raw_token2)
    assert rotated.token_version == 2

    # Verify new token decrypted
    decrypted = crypto.decrypt(rotated.encrypted_token, associated_data=3001001)
    assert decrypted == raw_token2


@requires_postgres
@pytest.mark.asyncio
async def test_rotate_token_mismatch_bot_id_rejected(pg_session: AsyncSession, crypto: TokenCrypto):
    """Rotating token with a different bot ID is rejected with TokenRotationBotMismatchError."""
    user = await _create_user(pg_session, 3002)
    master = await _create_master(pg_session, user)

    gateway = MockGateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)

    raw_token1 = "3002001:OLD_TOKEN_111111111111111111"
    raw_token2 = "9999999:DIFFERENT_BOT_TOKEN_222222"

    gateway.validate_token_mock = AsyncMock(
        return_value=BotIdentity(id=3002001, username="bot_3002", first_name="Bot 3002")
    )
    bot_instance = await service.provision_bot(
        master.id, user.id, raw_token1, BotIdentity(3002001, "bot_3002", "Bot 3002")
    )

    # Next getMe returns completely different bot ID
    gateway.validate_token_mock = AsyncMock(
        return_value=BotIdentity(id=9999999, username="attacker_bot", first_name="Attacker Bot")
    )

    with pytest.raises(TokenRotationBotMismatchError):
        await service.rotate_token(bot_instance.id, user.id, raw_token2)

    # Verify token version was NOT changed
    await pg_session.refresh(bot_instance)
    assert bot_instance.token_version == 1


# ===========================================================================
# 4. Disable and Enable Bot Lifecycle
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_disable_and_enable_bot(pg_session: AsyncSession, crypto: TokenCrypto):
    """Disabling removes webhook and marks DISABLED; enabling restores webhook and marks SETUP_REQUIRED."""
    user = await _create_user(pg_session, 4001)
    master = await _create_master(pg_session, user)

    gateway = MockGateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)

    raw_token = "4001001:LIFECYCLE_TOKEN_444444444"
    identity = BotIdentity(id=4001001, username="lifecycle_bot", first_name="Lifecycle Bot")
    gateway.validate_token_mock = AsyncMock(return_value=identity)

    bot_instance = await service.provision_bot(master.id, user.id, raw_token, identity)

    # Disable
    disabled = await service.disable_bot(bot_instance.id, user.id)
    assert disabled.status == BotInstanceStatus.DISABLED
    gateway.delete_webhook_mock.assert_awaited_once()

    # Enable
    enabled = await service.enable_bot(bot_instance.id, user.id)
    assert enabled.status == BotInstanceStatus.SETUP_REQUIRED


# ===========================================================================
# 5. Master Readiness Checklist & Activation
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_master_readiness_checklist(pg_session: AsyncSession):
    """ReadinessService correctly identifies missing services, schedules, and requisites."""
    user = await _create_user(pg_session, 5001)
    master = await _create_master(pg_session, user)

    readiness = MasterReadinessService(pg_session)
    is_ready, missing = await readiness.check(master.id)
    assert is_ready is False
    assert any("услуг" in m.lower() for m in missing)
    assert any("расписан" in m.lower() for m in missing)

    # Add a service
    service = Service(
        master_id=master.id,
        title="Маникюр",
        duration_min=60,
        price=1500,
        deposit_value=0,
        is_active=True,
    )
    pg_session.add(service)

    # Add a schedule day
    schedule = ScheduleTemplate(
        master_id=master.id,
        day_of_week=0,  # Monday
        work_start=time(10, 0),
        work_end=time(19, 0),
        is_day_off=False,
    )
    pg_session.add(schedule)
    await pg_session.commit()

    is_ready, missing = await readiness.check(master.id)
    assert is_ready is True
    assert missing == []


@requires_postgres
@pytest.mark.asyncio
async def test_activate_master_and_bot_success(pg_session: AsyncSession, crypto: TokenCrypto):
    """When readiness passes, activate_master_and_bot transitions both Master and BotInstance to ACTIVE."""
    user = await _create_user(pg_session, 5002)
    master = await _create_master(pg_session, user)

    # Add service and schedule
    service = Service(master_id=master.id, title="Стрижка", duration_min=45, price=2000, deposit_value=0, is_active=True)
    schedule = ScheduleTemplate(master_id=master.id, day_of_week=1, work_start=time(9, 0), work_end=time(18, 0), is_day_off=False)
    pg_session.add_all([service, schedule])

    # Provision bot
    gateway = MockGateway()
    service_prov = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)
    identity = BotIdentity(id=5002001, username="active_bot", first_name="Active Bot")
    gateway.validate_token_mock = AsyncMock(return_value=identity)
    bot_instance = await service_prov.provision_bot(master.id, user.id, "5002001:ACTIVE_TOKEN", identity)
    assert bot_instance.status == BotInstanceStatus.SETUP_REQUIRED

    # Activate
    success, missing = await service_prov.activate_master_and_bot(master.id, user.id)
    assert success is True
    assert missing == []

    await pg_session.refresh(master)
    await pg_session.refresh(bot_instance)
    assert master.status == MasterStatus.ACTIVE
    assert bot_instance.status == BotInstanceStatus.ACTIVE


# ===========================================================================
# 6. Strict IDOR Protection
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_idor_protection_other_user_cannot_manage(pg_session: AsyncSession, crypto: TokenCrypto):
    """User B cannot rotate, disable, or activate User A's bot/master."""
    user_a = await _create_user(pg_session, 6001, "user_a")
    user_b = await _create_user(pg_session, 6002, "user_b")
    master_a = await _create_master(pg_session, user_a, "Master A")

    gateway = MockGateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)
    identity = BotIdentity(id=6001001, username="bot_a", first_name="Bot A")
    gateway.validate_token_mock = AsyncMock(return_value=identity)

    bot_a = await service.provision_bot(master_a.id, user_a.id, "6001001:TOKEN_A", identity)

    # User B attempts to rotate
    with pytest.raises(AccessDeniedError):
        await service.rotate_token(bot_a.id, user_b.id, "6001001:NEW_TOKEN")

    # User B attempts to disable
    with pytest.raises(AccessDeniedError):
        await service.disable_bot(bot_a.id, user_b.id)

    # User B attempts to activate
    with pytest.raises(AccessDeniedError):
        await service.activate_master_and_bot(master_a.id, user_b.id)


# ===========================================================================
# 7. Customer Bot SETUP_REQUIRED Gating & Deep-Link
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_customer_bot_setup_required_gating(pg_session: AsyncSession):
    """When bot is in SETUP_REQUIRED status, regular clients receive setup notice."""
    from app.bot.handlers.client.start import cmd_start as client_cmd_start

    user = await _create_user(pg_session, 7001)
    master = await _create_master(pg_session, user)

    bot_repo = BotInstanceRepository(pg_session)
    instance = await bot_repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=7001001,
        encrypted_token="enc",
        status=BotInstanceStatus.SETUP_REQUIRED,
    )

    client_user = await _create_user(pg_session, 7002, "client_user")

    # Client /start
    message = AsyncMock(spec=Message)
    message.from_user = MagicMock(id=7002)
    message.answer = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    await client_cmd_start(
        message=message,
        state=state,
        db_user=client_user,
        is_admin=False,
        command=None,
        bot_instance=instance,
        session=pg_session,
    )

    message.answer.assert_awaited_once()
    sent_text = message.answer.call_args.kwargs.get("text") or (message.answer.call_args[0][0] if message.answer.call_args[0] else "")
    assert "пока настраивается мастером" in sent_text


@requires_postgres
@pytest.mark.asyncio
async def test_customer_bot_setup_required_admin_bypass(pg_session: AsyncSession):
    """Master owner / admin bypasses SETUP_REQUIRED gating and accesses admin panel."""
    from app.bot.handlers.client.start import cmd_start as client_cmd_start
    from aiogram.filters import CommandObject

    user = await _create_user(pg_session, 7003, "owner_user")
    master = await _create_master(pg_session, user)

    bot_repo = BotInstanceRepository(pg_session)
    instance = await bot_repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=7003001,
        encrypted_token="enc",
        status=BotInstanceStatus.SETUP_REQUIRED,
    )

    message = AsyncMock(spec=Message)
    message.from_user = MagicMock(id=7003)
    message.answer = AsyncMock()
    state = AsyncMock(spec=FSMContext)
    command = CommandObject(prefix="/", command="start", args="admin")

    with patch("app.bot.handlers.admin.dashboard.cmd_admin_dashboard", new_callable=AsyncMock) as mock_admin_dashboard:
        await client_cmd_start(
            message=message,
            state=state,
            db_user=user,
            is_admin=True,
            command=command,
            bot_instance=instance,
            session=pg_session,
        )
        mock_admin_dashboard.assert_awaited_once()


# ===========================================================================
# 8. Manager Webhook Endpoint Tests (/telegram/manager-webhook)
# ===========================================================================

@pytest.mark.asyncio
async def test_manager_webhook_secret_verification(fake_redis: fakeredis.aioredis.FakeRedis, deduplicator: UpdateDeduplicator):
    """Manager webhook rejects requests with invalid secret token (403)."""
    app = create_app(
        deduplicator=deduplicator,
        redis_client=fake_redis,
        manager_dp=AsyncMock(),
        manager_bot=AsyncMock(),
    )

    with patch.object(settings, "manager_webhook_secret", "SuperSecretManagerSecret123"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Missing header
            resp = await client.post("/telegram/manager-webhook", json={"update_id": 1})
            assert resp.status_code == 403

            # 2. Invalid secret
            resp = await client.post(
                "/telegram/manager-webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "WrongSecret"},
                json={"update_id": 1},
            )
            assert resp.status_code == 403


@pytest.mark.asyncio
async def test_manager_webhook_deduplication(fake_redis: fakeredis.aioredis.FakeRedis, deduplicator: UpdateDeduplicator):
    """Manager webhook deduplicates updates using manager:update:{update_id}."""
    mock_dp = AsyncMock(spec=Dispatcher)
    mock_bot = AsyncMock(spec=Bot)

    app = create_app(
        deduplicator=deduplicator,
        redis_client=fake_redis,
        manager_dp=mock_dp,
        manager_bot=mock_bot,
    )

    secret = "TestManagerSecretXYZ"
    payload = {
        "update_id": 888001,
        "message": {
            "message_id": 1,
            "date": 1600000000,
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 12345, "is_bot": False, "first_name": "Boss"},
            "text": "/start",
        },
    }

    with patch.object(settings, "manager_webhook_secret", secret):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # First delivery
            resp1 = await client.post(
                "/telegram/manager-webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": secret},
                json=payload,
            )
            assert resp1.status_code == 200
            assert resp1.json() == {"ok": True}
            assert mock_dp.feed_update.await_count == 1

            # Duplicate delivery
            resp2 = await client.post(
                "/telegram/manager-webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": secret},
                json=payload,
            )
            assert resp2.status_code == 200
            assert resp2.json() == {"ok": True, "status": "duplicate"}
            # feed_update was NOT called a second time
            assert mock_dp.feed_update.await_count == 1


# ===========================================================================
# 9. Plaintext Token Scrubbing & Message Deletion
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_token_message_deleted_and_candidate_scrubbed_from_fsm(pg_session: AsyncSession, crypto: TokenCrypto):
    """User message with token is deleted immediately and candidate_token is cleared after provisioning."""
    user = await _create_user(pg_session, 9001)
    master = await _create_master(pg_session, user)

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=9001, user_id=9001)
    state = FSMContext(storage=storage, key=key)

    await state.set_state(ConnectBotStates.waiting_for_token)
    await state.update_data(master_id=master.id)

    raw_token = "9001001:SECRET_TOKEN_TO_BE_DELETED"
    message = AsyncMock(spec=Message)
    message.text = raw_token
    message.from_user = MagicMock(id=9001, first_name="Master", username="master", last_name="")
    message.delete = AsyncMock()
    message.answer = AsyncMock()

    mock_gateway = MockGateway()
    mock_gateway.validate_token_mock = AsyncMock(
        return_value=BotIdentity(id=9001001, username="scrubbed_bot", first_name="Scrubbed Bot")
    )

    with patch("app.manager_bot.handlers.TelegramProvisioningGateway", return_value=mock_gateway):
        await msg_receive_bot_token(message, state, pg_session)

    # 1. Message was deleted immediately
    message.delete.assert_awaited_once()

    # 2. State moved to confirm_connect
    current_state = await state.get_state()
    assert current_state == ConnectBotStates.confirm_connect.state

    # 3. Simulate confirmation callback
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = MagicMock(id=9001, first_name="Master", username="master", last_name="")
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    with patch("app.manager_bot.handlers.TelegramProvisioningGateway", return_value=mock_gateway):
        await cb_confirm_bot_connection(callback, state, pg_session)

    # 4. Plaintext token is wiped from FSM
    fsm_data = await state.get_data()
    assert fsm_data.get("candidate_token") is None
    assert await state.get_state() is None


# ===========================================================================
# 10. Database Partial Unique Index Enforcement
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_partial_unique_index_active_bot(pg_session: AsyncSession, crypto: TokenCrypto):
    """Database rejects multiple ACTIVE bot instances for the same master."""
    from sqlalchemy.exc import IntegrityError
    user = await _create_user(pg_session, 9101)
    master = await _create_master(pg_session, user)

    repo = BotInstanceRepository(pg_session)
    enc1 = crypto.encrypt("9101001:TOKEN_1", associated_data=9101001)
    enc2 = crypto.encrypt("9101002:TOKEN_2", associated_data=9101002)

    await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=9101001,
        encrypted_token=enc1,
        status=BotInstanceStatus.ACTIVE,
    )
    await pg_session.commit()

    with pytest.raises(IntegrityError):
        await repo.create_bot_instance(
            master_id=master.id,
            telegram_bot_id=9101002,
            encrypted_token=enc2,
            status=BotInstanceStatus.ACTIVE,
        )
        await pg_session.commit()
    await pg_session.rollback()


# ===========================================================================
# 11. Manager Webhook Endpoint: Size & Malformed Body Handling
# ===========================================================================

@pytest.mark.asyncio
async def test_manager_webhook_payload_too_large(fake_redis: fakeredis.aioredis.FakeRedis, deduplicator: UpdateDeduplicator):
    """Manager webhook rejects oversized bodies (413 Payload Too Large)."""
    app = create_app(
        deduplicator=deduplicator,
        redis_client=fake_redis,
        manager_dp=AsyncMock(),
        manager_bot=AsyncMock(),
    )

    secret = "TestSecret999"
    with patch.object(settings, "manager_webhook_secret", secret):
        with patch.object(settings, "webhook_max_body_bytes", 50):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/telegram/manager-webhook",
                    headers={"X-Telegram-Bot-Api-Secret-Token": secret},
                    content=b"A" * 100,
                )
                assert resp.status_code == 413


@pytest.mark.asyncio
async def test_manager_webhook_malformed_json(fake_redis: fakeredis.aioredis.FakeRedis, deduplicator: UpdateDeduplicator):
    """Manager webhook rejects malformed JSON payloads (400 Bad Request)."""
    app = create_app(
        deduplicator=deduplicator,
        redis_client=fake_redis,
        manager_dp=AsyncMock(),
        manager_bot=AsyncMock(),
    )

    secret = "TestSecret999"
    with patch.object(settings, "manager_webhook_secret", secret):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/telegram/manager-webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": secret},
                content=b"Not a json",
            )
            assert resp.status_code == 400


# ===========================================================================
# 12. Manager Bot Handlers: Start & Project Card IDOR
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_manager_cmd_start_renders_projects(pg_session: AsyncSession):
    """Manager Bot /start renders welcome for new user and project list for existing owner."""
    user = await _create_user(pg_session, 9201, "new_manager_user")

    message = AsyncMock(spec=Message)
    message.from_user = MagicMock(id=9201, first_name="Owner", username="new_manager_user", last_name="")
    message.answer = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    # 1. No projects yet -> welcome message
    await cmd_start(message, state, pg_session)
    message.answer.assert_awaited_once()
    welcome_text = message.answer.call_args[0][0]
    assert "Добро пожаловать в Beauty Bot Manager" in welcome_text

    # 2. After creating a project -> project list
    await _create_master(pg_session, user, "Studio Alpha")
    message.answer.reset_mock()
    await cmd_start(message, state, pg_session)
    message.answer.assert_awaited_once()
    list_text = message.answer.call_args[0][0]
    assert "Выберите проект для управления" in list_text


@requires_postgres
@pytest.mark.asyncio
async def test_manager_project_card_access_denied_for_non_owner(pg_session: AsyncSession):
    """Manager Bot project card rejects unauthorized users attempting to access another master."""
    user_owner = await _create_user(pg_session, 9301, "real_owner")
    user_stranger = await _create_user(pg_session, 9302, "stranger")
    master = await _create_master(pg_session, user_owner, "Private Salon")

    callback = AsyncMock(spec=CallbackQuery)
    callback.data = f"mgr:master:{master.id}"
    callback.from_user = MagicMock(id=9302, first_name="Stranger", username="stranger", last_name="")
    callback.answer = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    await cb_project_card(callback, state, pg_session)
    callback.answer.assert_awaited_once()
    answer_text = callback.answer.call_args[0][0]
    assert "доступ запрещён" in answer_text.lower()


# ===========================================================================
# 13. Readiness Checklist: Missing Bank Requisites when Deposit is Required
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_readiness_checklist_flags_missing_bank_requisites(pg_session: AsyncSession):
    """Readiness checklist flags missing card/bank requisites if any service requires a deposit."""
    user = await _create_user(pg_session, 9401)
    master = await _create_master(pg_session, user)

    # Add schedule
    schedule = ScheduleTemplate(
        master_id=master.id,
        day_of_week=0,
        work_start=time(10, 0),
        work_end=time(18, 0),
        is_day_off=False,
    )
    pg_session.add(schedule)

    # Add service requiring deposit
    service = Service(
        master_id=master.id,
        title="Окрашивание",
        duration_min=120,
        price=5000,
        deposit_value=1000,  # Deposit required!
        is_active=True,
    )
    pg_session.add(service)

    # Clear bank requisites in MasterSettings
    settings_obj = await pg_session.get(MasterSettings, master.id)
    settings_obj.bank_card_number = None
    settings_obj.bank_name = None
    await pg_session.commit()

    readiness = MasterReadinessService(pg_session)
    is_ready, missing = await readiness.check(master.id)
    assert is_ready is False
    assert any("карты/счета" in m.lower() or "предоплат" in m.lower() for m in missing)

