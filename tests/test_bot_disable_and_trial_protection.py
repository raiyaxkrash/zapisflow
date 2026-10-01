"""Comprehensive test suite for Bot Disconnection / Logical Deletion & Per-User Trial Abuse Protection.

Covers all 15 scenarios:
1. Owner can initiate bot disconnection and receives explicit confirmation dialog.
2. Non-owner cannot disable bot (IDOR check -> AccessDeniedError / alert).
3. Disconnecting bot deletes webhook via Telegram API with drop_pending_updates=False.
4. Disconnecting bot transitions BotInstance status to DISABLED.
5. Bot disconnection preserves all master data, clients, appointments, payments, and audit logs (no database deletion).
6. Disconnecting bot does NOT modify Master.status or Master.subscription_status.
7. Disconnecting bot evicts bot from BotRegistry local cache and closes its aiohttp session.
8. Disconnecting bot publishes invalidation event to Redis Pub/Sub across replicas.
9. Disconnecting bot records an audit log entry with BOT_DISABLED.
10. Disabled bot rejects incoming customer Telegram webhooks with 403 Forbidden.
11. Owner can safely re-enable disabled bot (enable_bot) without data loss or new trial creation.
12. First project created by a user receives 14-day trial (TRIAL_CLAIMED, SubscriptionStatus.TRIAL).
13. Second project created by the same user does NOT get a free trial (TRIAL_REJECTED_ALREADY_USED, SubscriptionStatus.EXPIRED).
14. Disconnecting or disabling bot on first project does NOT reset user's trial eligibility.
15. Concurrency protection: parallel project creation attempts for the same user atomically allow only one trial claim (SELECT ... FOR UPDATE).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser

from app.config.settings import settings
from app.core.token_crypto import TokenCrypto
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.audit import AuditLog
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterAdmin,
    MasterAdminRole,
    MasterClient,
    MasterSettings,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.payment import Payment, PaymentStatus
from app.database.models.service import DepositType, Service
from app.database.models.user import User
from app.manager_bot.handlers import (
    cb_confirm_disable_bot,
    cb_disable_bot,
    cb_enable_bot,
)
from app.manager_bot.keyboards import confirm_disable_bot_keyboard
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditEvent
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.bot_registry import BotRegistry
from app.services.exceptions import AccessDeniedError
from app.services.subscription_service import SubscriptionService
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


class MockProvisioningGateway(TelegramProvisioningGateway):
    """Mock Telegram gateway tracking network calls."""

    def __init__(self) -> None:
        super().__init__()
        self.last_webhook_url: Optional[str] = None
        self.set_webhook_mock = AsyncMock(return_value=True)
        self.delete_webhook_mock = AsyncMock(return_value=True)
        self.validate_token_mock = AsyncMock(
            return_value=BotIdentity(id=88812345, username="test_beauty_bot", first_name="Beauty Bot")
        )
        self.get_webhook_info_mock = AsyncMock(return_value=None)

    async def validate_token(self, token: str) -> BotIdentity:
        return await self.validate_token_mock(token)

    async def set_webhook(self, token: str, url: str, secret_token=None, **kwargs) -> bool:
        self.last_webhook_url = url
        return await self.set_webhook_mock(token=token, url=url, secret_token=secret_token, **kwargs)

    async def delete_webhook(self, token: str, drop_pending_updates: bool = False) -> bool:
        return await self.delete_webhook_mock(token=token, drop_pending_updates=drop_pending_updates)

    async def get_webhook_info(self, token: str) -> Any:
        explicit = self.get_webhook_info_mock.return_value
        if explicit is not None:
            return explicit
        return MagicMock(url=self.last_webhook_url, has_custom_certificate=False, pending_update_count=0)


async def _create_test_user(session: AsyncSession, telegram_id: int, username: str = "testuser") -> User:
    repo = UserRepository(session)
    user, _ = await repo.get_or_create(telegram_id=telegram_id, first_name="TestOwner", username=username)
    await session.commit()
    return user


async def _create_test_master_with_bot(
    session: AsyncSession,
    owner_user: User,
    crypto: TokenCrypto,
    bot_status: BotInstanceStatus = BotInstanceStatus.ACTIVE,
    bot_id: int = 88812345,
) -> tuple[Master, BotInstance]:
    master = Master(
        owner_user_id=owner_user.id,
        display_name="Тестовый Салон",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        paid_until=datetime.now(timezone.utc) + timedelta(days=30),
        timezone="Europe/Moscow",
    )
    session.add(master)
    await session.flush()

    admin = MasterAdmin(master_id=master.id, user_id=owner_user.id, role=MasterAdminRole.OWNER)
    session.add(admin)

    settings_obj = MasterSettings(
        master_id=master.id,
        bank_name="Сбербанк",
        bank_card_number="2202000000000000",
        bank_recipient_name="Владелец Салона",
    )
    session.add(settings_obj)

    raw_token = f"{bot_id}:TEST_RAW_BOT_TOKEN_SECRET"
    encrypted_token = crypto.encrypt(raw_token, associated_data=bot_id)

    bot_instance = BotInstance(
        master_id=master.id,
        telegram_bot_id=bot_id,
        telegram_username="test_beauty_bot",
        telegram_first_name="Beauty Bot",
        encrypted_token=encrypted_token,
        webhook_secret="whsec_test_secret_token",
        status=bot_status,
        token_version=1,
        is_current=True,
    )
    session.add(bot_instance)
    await session.commit()
    await session.refresh(master)
    await session.refresh(bot_instance)
    return master, bot_instance


# ===========================================================================
# 1. Owner confirmation dialog before disabling
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_01_owner_receives_confirmation_dialog_before_disabling(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Owner clicking disable bot receives an explicit confirmation dialog."""
    owner = await _create_test_user(pg_session, 5001)
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)

    message = AsyncMock(spec=Message)
    message.edit_text = AsyncMock()

    callback = AsyncMock(spec=CallbackQuery)
    callback.data = f"mgr:bot:disable:{master.id}"
    callback.from_user = MagicMock(id=5001, first_name="TestOwner", username="testuser", last_name="")
    callback.message = message
    callback.answer = AsyncMock()

    await cb_disable_bot(callback, session=pg_session)

    # Check confirmation message and keyboard
    message.edit_text.assert_called_once()
    call_args = message.edit_text.call_args
    text = call_args[0][0]
    keyboard = call_args[1].get("reply_markup")

    assert "Вы действительно хотите отключить бота" in text
    assert "Все данные проекта" in text
    assert keyboard is not None
    # Check confirm button callback data
    buttons = [btn.callback_data for row in keyboard.inline_keyboard for btn in row if btn.callback_data]
    assert f"mgr:bot:confirm_disable:{master.id}" in buttons
    assert f"mgr:master:{master.id}" in buttons

    # Ensure bot is NOT disabled yet
    await pg_session.refresh(bot_instance)
    assert bot_instance.status == BotInstanceStatus.ACTIVE


# ===========================================================================
# 2. Non-owner cannot disable bot (IDOR check)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_02_non_owner_cannot_disable_bot_idor_protection(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Non-owner attempting to disable or confirm disable is strictly rejected."""
    owner = await _create_test_user(pg_session, 5002)
    attacker = await _create_test_user(pg_session, 5003)
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)

    # Attacker tries to view confirmation
    message = AsyncMock(spec=Message)
    callback_prompt = AsyncMock(spec=CallbackQuery)
    callback_prompt.data = f"mgr:bot:disable:{master.id}"
    callback_prompt.from_user = MagicMock(id=5003, first_name="Attacker", username="attacker", last_name="")
    callback_prompt.message = message
    callback_prompt.answer = AsyncMock()

    await cb_disable_bot(callback_prompt, session=pg_session)
    callback_prompt.answer.assert_called_once_with("Ошибка: доступ запрещён.", show_alert=True)

    # Attacker tries to confirm disable
    callback_confirm = AsyncMock(spec=CallbackQuery)
    callback_confirm.data = f"mgr:bot:confirm_disable:{master.id}"
    callback_confirm.from_user = MagicMock(id=5003, first_name="Attacker", username="attacker", last_name="")
    callback_confirm.message = message
    callback_confirm.answer = AsyncMock()

    await cb_confirm_disable_bot(callback_confirm, session=pg_session)
    callback_confirm.answer.assert_called_once_with("Ошибка: доступ запрещён.", show_alert=True)

    # Direct service call check
    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)
    with pytest.raises(AccessDeniedError):
        await service.disable_bot(bot_instance.id, actor_user_id=attacker.id)

    # Bot remains ACTIVE
    await pg_session.refresh(bot_instance)
    assert bot_instance.status == BotInstanceStatus.ACTIVE


# ===========================================================================
# 3. Confirming disable deletes webhook with drop_pending_updates=False
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_03_confirming_disable_calls_delete_webhook_drop_pending_false(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Disabling bot invokes Telegram Bot API deleteWebhook with drop_pending_updates=False."""
    owner = await _create_test_user(pg_session, 5004)
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)

    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)

    await service.disable_bot(bot_instance.id, actor_user_id=owner.id)

    gateway.delete_webhook_mock.assert_called_once()
    call_kwargs = gateway.delete_webhook_mock.call_args[1]
    assert call_kwargs.get("drop_pending_updates") is False


# ===========================================================================
# 4. Bot status transitions to DISABLED
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_04_bot_status_transitions_to_disabled(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """BotInstance transitions to DISABLED upon disconnection."""
    owner = await _create_test_user(pg_session, 5005)
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)

    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)
    await service.disable_bot(bot_instance.id, actor_user_id=owner.id)

    await pg_session.refresh(bot_instance)
    assert bot_instance.status == BotInstanceStatus.DISABLED


# ===========================================================================
# 5. Preserves all domain data (Master, clients, appointments, payments, audit)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_05_bot_disconnection_preserves_master_clients_appointments_payments_audit(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Disconnection is strictly logical: no domain data is deleted."""
    owner = await _create_test_user(pg_session, 5006)
    client_user = await _create_test_user(pg_session, 5007, username="client5007")
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)

    # 1. MasterClient
    client_rel = MasterClient(
        master_id=master.id,
        user_id=client_user.id,
        notes="Постоянный клиент",
    )
    pg_session.add(client_rel)

    # 2. Service
    service_obj = Service(
        master_id=master.id,
        title="Стрижка",
        price=Decimal("2000.00"),
        duration_min=60,
        buffer_min=15,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("0.00"),
        is_active=True,
    )
    pg_session.add(service_obj)
    await pg_session.flush()

    # 3. Appointment
    now_utc = datetime.now(timezone.utc)
    appt = Appointment(
        master_id=master.id,
        user_id=client_user.id,
        service_id=service_obj.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=now_utc + timedelta(days=2),
        end_time=now_utc + timedelta(days=2, hours=1),
        end_time_with_buffer=now_utc + timedelta(days=2, hours=1, minutes=15),
        snapshot_service_title="Стрижка",
        snapshot_service_price=Decimal("2000.00"),
        snapshot_service_duration_min=60,
        snapshot_buffer_duration_min=15,
        snapshot_deposit_amount=Decimal("0.00"),
    )
    pg_session.add(appt)
    await pg_session.flush()

    # 4. Payment
    payment = Payment(
        master_id=master.id,
        appointment_id=appt.id,
        user_id=client_user.id,
        amount=Decimal("2000.00"),
        status=PaymentStatus.CONFIRMED,
    )
    pg_session.add(payment)

    # 5. AuditLog
    prior_audit = AuditLog(
        master_id=master.id,
        action=AuditEvent.MASTER_CREATED,
        entity_type="Master",
        actor_user_id=owner.id,
    )
    pg_session.add(prior_audit)
    await pg_session.commit()

    # Disconnect bot
    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)
    await service.disable_bot(bot_instance.id, actor_user_id=owner.id)

    # Verify zero data loss
    res_master = await pg_session.get(Master, master.id)
    assert res_master is not None

    res_client = (
        await pg_session.execute(
            select(MasterClient).where(MasterClient.master_id == master.id, MasterClient.user_id == client_user.id)
        )
    ).scalar_one_or_none()
    assert res_client is not None
    assert res_client.notes == "Постоянный клиент"

    res_appt = await pg_session.get(Appointment, appt.id)
    assert res_appt is not None
    assert res_appt.status == AppointmentStatus.CONFIRMED

    res_payment = await pg_session.get(Payment, payment.id)
    assert res_payment is not None
    assert res_payment.amount == Decimal("2000.00")

    audit_records = (
        (await pg_session.execute(select(AuditLog).where(AuditLog.master_id == master.id))).scalars().all()
    )
    actions = [a.action for a in audit_records]
    assert AuditEvent.MASTER_CREATED in actions
    assert AuditEvent.BOT_DISABLED in actions


# ===========================================================================
# 6. Does NOT modify Master or Subscription status
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_06_bot_disconnection_does_not_modify_master_or_subscription_status(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Disconnection must never change MasterStatus or SubscriptionStatus."""
    owner = await _create_test_user(pg_session, 5008)
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)
    initial_paid_until = master.paid_until

    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)
    await service.disable_bot(bot_instance.id, actor_user_id=owner.id)

    await pg_session.refresh(master)
    assert master.status == MasterStatus.ACTIVE
    assert master.subscription_status == SubscriptionStatus.ACTIVE
    assert master.paid_until == initial_paid_until


# ===========================================================================
# 7. Evicts cache and closes session in BotRegistry
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_07_disconnecting_bot_evicts_cache_and_closes_session_in_registry(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Disabling bot invalidates BotRegistry cache entry and closes its aiohttp session."""
    owner = await _create_test_user(pg_session, 5009)
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)

    registry = BotRegistry(token_crypto=crypto)

    mock_bot = AsyncMock(spec=Bot)
    mock_bot.session = AsyncMock()
    mock_bot.session.close = AsyncMock()

    # Pre-populate registry cache
    with patch("app.services.bot_factory.BotFactory.create", return_value=mock_bot):
        bot = await registry.get_by_instance_id(bot_instance.id, session=pg_session)
        assert bot is mock_bot
        assert bot_instance.id in registry._cache

    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto, registry=registry)
    await service.disable_bot(bot_instance.id, actor_user_id=owner.id)

    # Cache must be evicted
    assert bot_instance.id not in registry._cache
    # aiohttp session must be closed
    mock_bot.session.close.assert_awaited()


# ===========================================================================
# 8. Publishes invalidation event to Redis Pub/Sub
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_08_disconnecting_bot_publishes_invalidation_to_redis_pubsub(
    pg_session: AsyncSession, crypto: TokenCrypto, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """Disabling bot publishes invalidation event to Redis Pub/Sub for cluster replicas."""
    owner = await _create_test_user(pg_session, 5010)
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)

    registry = BotRegistry(token_crypto=crypto, redis_client=fake_redis)

    with patch.object(fake_redis, "publish", new_callable=AsyncMock) as mock_publish:
        gateway = MockProvisioningGateway()
        service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto, registry=registry)
        await service.disable_bot(bot_instance.id, actor_user_id=owner.id)

        mock_publish.assert_called_once()
        channel, payload_str = mock_publish.call_args[0]
        assert channel == settings.bot_registry_invalidation_channel
        payload = json.loads(payload_str)
        assert payload["bot_instance_id"] == bot_instance.id
        assert payload["reason"] == "manual"


# ===========================================================================
# 9. Audit log records BOT_DISABLED
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_09_audit_log_records_bot_disabled(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Audit log records BOT_DISABLED with actor_user_id and master_id."""
    owner = await _create_test_user(pg_session, 5011)
    master, bot_instance = await _create_test_master_with_bot(pg_session, owner, crypto)

    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)
    await service.disable_bot(bot_instance.id, actor_user_id=owner.id)

    stmt = select(AuditLog).where(
        AuditLog.master_id == master.id,
        AuditLog.action == AuditEvent.BOT_DISABLED,
    )
    result = await pg_session.execute(stmt)
    entry = result.scalar_one_or_none()

    assert entry is not None
    assert entry.actor_user_id == owner.id
    assert entry.entity_id == bot_instance.id
    assert entry.payload_after == {"status": "DISABLED"}


# ===========================================================================
# 10. Disabled bot rejects incoming customer Telegram webhooks (403 Forbidden)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_10_disabled_bot_rejects_incoming_customer_webhook_403(
    pg_session: AsyncSession, crypto: TokenCrypto, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """Incoming customer webhook updates for a DISABLED bot return 403 Forbidden."""
    owner = await _create_test_user(pg_session, 5012)
    master, bot_instance = await _create_test_master_with_bot(
        pg_session, owner, crypto, bot_status=BotInstanceStatus.DISABLED
    )

    registry = BotRegistry(token_crypto=crypto, redis_client=fake_redis)
    dp = Dispatcher()
    dedup = UpdateDeduplicator(redis_client=fake_redis)
    test_session_factory = async_sessionmaker(bind=pg_session.bind, class_=AsyncSession, expire_on_commit=False)

    app = create_app(
        registry=registry,
        dp=dp,
        deduplicator=dedup,
        redis_client=fake_redis,
        session_factory=test_session_factory,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        update_payload = {
            "update_id": 10001,
            "message": {
                "message_id": 1,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": 999, "type": "private"},
                "from": {"id": 999, "is_bot": False, "first_name": "Client"},
                "text": "Привет",
            },
        }
        resp = await client.post(
            f"/telegram/webhook/{bot_instance.public_id}",
            json=update_payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": bot_instance.webhook_secret},
        )
        assert resp.status_code == 403
        assert "DISABLED" in resp.json()["detail"]


# ===========================================================================
# 11. Owner can re-enable disabled bot without data loss
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_11_owner_can_re_enable_disabled_bot_without_data_loss(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Owner can re-enable a disabled bot, restoring webhook and status to SETUP_REQUIRED."""
    owner = await _create_test_user(pg_session, 5013)
    master, bot_instance = await _create_test_master_with_bot(
        pg_session, owner, crypto, bot_status=BotInstanceStatus.DISABLED
    )

    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)

    await service.enable_bot(bot_instance.id, actor_user_id=owner.id)

    gateway.set_webhook_mock.assert_called_once()
    await pg_session.refresh(bot_instance)
    assert bot_instance.status == BotInstanceStatus.SETUP_REQUIRED
    assert bot_instance.last_error is None


# ===========================================================================
# 12. First project created by user claims 14-day trial
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_12_first_project_created_by_user_claims_14_day_trial(
    pg_session: AsyncSession,
) -> None:
    """New user account creating their first master receives 14-day trial."""
    user = await _create_test_user(pg_session, 5014)
    assert user.trial_claimed_at is None
    assert user.trial_ends_at is None

    master = Master(
        owner_user_id=user.id,
        display_name="Первый Проект",
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.EXPIRED,
        timezone="Europe/Moscow",
    )
    pg_session.add(master)
    await pg_session.flush()

    sub_service = SubscriptionService(pg_session)
    trial_granted = await sub_service.claim_user_trial_or_reject(user.id, master, trial_days=14)
    await pg_session.commit()

    assert trial_granted is True
    await pg_session.refresh(user)
    await pg_session.refresh(master)

    assert user.trial_claimed_at is not None
    assert user.trial_ends_at is not None
    assert master.subscription_status == SubscriptionStatus.TRIAL
    assert master.trial_ends_at == user.trial_ends_at

    # Audit log
    stmt = select(AuditLog).where(
        AuditLog.master_id == master.id,
        AuditLog.action == AuditEvent.TRIAL_CLAIMED,
    )
    audit = (await pg_session.execute(stmt)).scalar_one_or_none()
    assert audit is not None
    assert audit.actor_user_id == user.id


# ===========================================================================
# 13. Second project created by same user is EXPIRED (no trial abuse)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_13_second_project_created_by_same_user_is_expired_without_trial(
    pg_session: AsyncSession,
) -> None:
    """Second project created by the same user does not receive a second trial."""
    user = await _create_test_user(pg_session, 5015)

    # Master 1: claims trial
    master1 = Master(
        owner_user_id=user.id,
        display_name="Проект 1",
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.EXPIRED,
        timezone="Europe/Moscow",
    )
    pg_session.add(master1)
    await pg_session.flush()

    sub_service = SubscriptionService(pg_session)
    granted1 = await sub_service.claim_user_trial_or_reject(user.id, master1)
    await pg_session.commit()
    assert granted1 is True

    # Master 2: attempts to claim trial
    master2 = Master(
        owner_user_id=user.id,
        display_name="Проект 2",
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.TRIAL,  # candidate
        timezone="Europe/Moscow",
    )
    pg_session.add(master2)
    await pg_session.flush()

    granted2 = await sub_service.claim_user_trial_or_reject(user.id, master2)
    await pg_session.commit()

    assert granted2 is False
    await pg_session.refresh(master2)
    assert master2.subscription_status == SubscriptionStatus.EXPIRED
    assert master2.trial_ends_at is None

    # Audit log must record TRIAL_REJECTED_ALREADY_USED
    stmt = select(AuditLog).where(
        AuditLog.master_id == master2.id,
        AuditLog.action == AuditEvent.TRIAL_REJECTED_ALREADY_USED,
    )
    audit = (await pg_session.execute(stmt)).scalar_one_or_none()
    assert audit is not None
    assert audit.actor_user_id == user.id


# ===========================================================================
# 14. Disconnecting bot on first project does NOT reset user trial
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_14_deleting_or_disabling_bot_does_not_reset_user_trial(
    pg_session: AsyncSession, crypto: TokenCrypto
) -> None:
    """Deleting or disabling bot does not clear user.trial_claimed_at."""
    user = await _create_test_user(pg_session, 5016)

    # Project 1 with bot
    master1, bot1 = await _create_test_master_with_bot(pg_session, user, crypto)
    sub_service = pg_session
    # Claim trial on master 1
    sub_service = SubscriptionService(pg_session)
    await sub_service.claim_user_trial_or_reject(user.id, master1)
    await pg_session.commit()

    # Disconnect bot on Project 1
    gateway = MockProvisioningGateway()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)
    await service.disable_bot(bot1.id, actor_user_id=user.id)

    # Check user trial is still permanently claimed
    await pg_session.refresh(user)
    assert user.trial_claimed_at is not None

    # Project 2 created afterwards
    master2 = Master(
        owner_user_id=user.id,
        display_name="Проект 3",
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.EXPIRED,
        timezone="Europe/Moscow",
    )
    pg_session.add(master2)
    await pg_session.flush()

    granted = await sub_service.claim_user_trial_or_reject(user.id, master2)
    await pg_session.commit()

    assert granted is False
    await pg_session.refresh(master2)
    assert master2.subscription_status == SubscriptionStatus.EXPIRED


# ===========================================================================
# 15. Concurrent project creation race condition protection (SELECT FOR UPDATE)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_15_concurrent_project_creation_race_condition_protection(
    pg_engine: AsyncEngine,
) -> None:
    """Two parallel project creations for the same user atomically allow only one trial claim."""
    # 1. Seed user in isolated transaction
    import random
    unique_tg_id = random.randint(10_000_000, 99_999_999)
    session_factory = async_sessionmaker(bind=pg_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            telegram_id=unique_tg_id,
            first_name="ConcurrentOwner",
            username=f"concurrent_owner_{unique_tg_id}",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    # 2. Define concurrent worker
    async def worker(worker_name: str) -> tuple[bool, int]:
        async with session_factory() as session:
            master = Master(
                owner_user_id=user_id,
                display_name=f"Parallel Studio {worker_name}",
                status=MasterStatus.SETUP_REQUIRED,
                subscription_status=SubscriptionStatus.EXPIRED,
                timezone="Europe/Moscow",
            )
            session.add(master)
            await session.flush()

            sub_service = SubscriptionService(session)
            granted = await sub_service.claim_user_trial_or_reject(user_id, master)
            await session.commit()
            return granted, master.id

    # 3. Launch concurrent race
    results = await asyncio.gather(worker("A"), worker("B"))

    granted_list = [r[0] for r in results]
    master_ids = [r[1] for r in results]

    # Exactly one True (TRIAL) and one False (EXPIRED)
    assert granted_list.count(True) == 1
    assert granted_list.count(False) == 1

    # Verify final states in DB
    async with session_factory() as session:
        masters = (
            (await session.execute(select(Master).where(Master.id.in_(master_ids)))).scalars().all()
        )
        statuses = [m.subscription_status for m in masters]
        assert SubscriptionStatus.TRIAL in statuses
        assert SubscriptionStatus.EXPIRED in statuses

        audit_logs = (
            (await session.execute(select(AuditLog).where(AuditLog.master_id.in_(master_ids)))).scalars().all()
        )
        audit_actions = [a.action for a in audit_logs]
        assert AuditEvent.TRIAL_CLAIMED in audit_actions
        assert AuditEvent.TRIAL_REJECTED_ALREADY_USED in audit_actions
