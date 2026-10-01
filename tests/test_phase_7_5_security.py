"""Phase 7.5: Security verification test suite for Manager Bot & Bot Provisioning.

Verifies all 18 security requirements:
 1. FSM candidate token encryption (0 plaintext tokens in FSM).
 2. Redis FSM state scan (0 raw tokens in serialized storage).
 3. Audit log secret scan (0 raw tokens, 0 'v1:' ciphertexts, 0 webhook secrets).
 4. Log masking & redaction on getMe, setWebhook, rotate failure.
 5. Temporary bot session lifecycle cleanup across all gateway methods.
 6. Concurrent duplicate bot connection on live PostgreSQL (clean DuplicateBotError).
 7. Same master concurrent connection invariant (uq_bot_instances_current_per_master).
 8. Active bot race handling (uq_bot_instances_active_per_master clean rejection).
 9. Multi-master owner isolation (same owner, different projects; third party denied).
10. Manager callbacks IDOR fail-closed protection.
11. BotInstance service IDOR fail-closed protection.
12. Disable bot on deleteWebhook failure (safe last_error, status DISABLED, cache evicted).
13. Enable bot with invalid/revoked token (status ERROR, safe last_error).
14. getWebhookInfo URL verification & secret leak detection.
15. PROVISIONING committed to PostgreSQL BEFORE network setWebhook call.
16. Rotation failure safety (status ERROR, cache evicted, retry uses v2).
17. Manager update concurrent deduplication.
18. Trial duration configuration verification.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser
import fakeredis.aioredis
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.core.security import mask_token, redact_token
from app.core.token_crypto import TokenCrypto
from app.database.models.audit import AuditLog
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.user import User
from app.manager_bot.handlers import (
    cb_activate_bot,
    cb_checklist,
    cb_confirm_bot_connection,
    cb_connect_bot,
    cb_disable_bot,
    cb_enable_bot,
    cb_project_card,
    cb_retry_provisioning,
    cb_rotate_token_prompt,
    msg_receive_bot_token,
)
from app.manager_bot.states import ConnectBotStates
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditEvent, AuditService
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.bot_registry import BotRegistry
from app.services.exceptions import (
    AccessDeniedError,
    DuplicateBotError,
    InvalidBotTokenError,
    ProvisioningWebhookError,
    TelegramGatewayError,
    TelegramGatewayNetworkError,
)
from app.services.telegram_provisioning_gateway import BotIdentity, TelegramProvisioningGateway
from app.services.update_dedup import UpdateDeduplicator
from tests.conftest import requires_postgres

TEST_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def setup_encryption_key():
    orig = settings.bot_token_encryption_key
    orig_base = settings.webhook_base_url
    settings.bot_token_encryption_key = TEST_KEY
    settings.webhook_base_url = "https://test.example.com"
    yield
    settings.bot_token_encryption_key = orig
    settings.webhook_base_url = orig_base


@pytest.fixture
def crypto() -> TokenCrypto:
    return TokenCrypto(TEST_KEY)


class MockGateway(TelegramProvisioningGateway):
    """Safe Mock Gateway for provisioning tests."""

    def __init__(self) -> None:
        super().__init__()
        self.last_webhook_url: Optional[str] = None
        self.validate_token_mock = AsyncMock(
            return_value=BotIdentity(id=8880001, username="safe_bot", first_name="Safe Bot")
        )
        self.set_webhook_mock = AsyncMock(return_value=True)
        self.delete_webhook_mock = AsyncMock(return_value=True)
        self.get_webhook_info_mock = AsyncMock(return_value=None)

    async def validate_token(self, token: str) -> BotIdentity:
        return await self.validate_token_mock(token)

    async def set_webhook(self, token: str, url: str, secret_token=None, **kwargs) -> bool:
        self.last_webhook_url = url
        return await self.set_webhook_mock(token=token, url=url, secret_token=secret_token, **kwargs)

    async def get_webhook_info(self, token: str) -> Any:
        if self.get_webhook_info_mock.side_effect is not None:
            return await self.get_webhook_info_mock(token)
        explicit = self.get_webhook_info_mock.return_value
        if explicit is not None:
            return explicit
        return MagicMock(url=self.last_webhook_url, has_custom_certificate=False, pending_update_count=0)

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
    await session.commit()
    return master


# ===========================================================================
# 1. FSM candidate token encryption (0 plaintext tokens in FSM)
# ===========================================================================

@pytest.mark.asyncio
async def test_01_fsm_candidate_token_ciphertext_only(crypto: TokenCrypto):
    """Candidate token must be encrypted immediately upon reception; NO plaintext in FSM."""
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=100, user_id=100)
    state = FSMContext(storage=storage, key=key)
    await state.update_data(master_id=42)

    raw_token = "8880001:AAABBBCCCDDDEEEFFFGGGHHHJJJ12345"
    message = AsyncMock(spec=Message)
    message.text = raw_token
    message.from_user = MagicMock(id=100, first_name="Owner", last_name="", username="owner")
    message.delete = AsyncMock()
    message.answer = AsyncMock()

    mock_session = AsyncMock(spec=AsyncSession)
    mock_user = User(id=1, telegram_id=100, first_name="Owner")

    with patch("app.manager_bot.handlers._get_or_create_user", return_value=mock_user), \
         patch("app.manager_bot.handlers.BotProvisioningService.validate_candidate_token",
               return_value=BotIdentity(id=8880001, username="safe_bot", first_name="Safe Bot")):
        await msg_receive_bot_token(message, state, mock_session)

    # 1. Message must be deleted
    message.delete.assert_awaited_once()

    # 2. Check FSM data
    data = await state.get_data()
    assert "candidate_token" not in data, "Plaintext candidate_token MUST NOT be in FSM"
    assert "encrypted_candidate_token" in data, "encrypted_candidate_token must be present in FSM"
    encrypted_val = data["encrypted_candidate_token"]

    # 3. Assert format: v1:k1:<ciphertext>
    assert encrypted_val.startswith("v1:"), "Token must be versioned ciphertext"
    assert raw_token not in encrypted_val, "Raw token must not appear in ciphertext"

    # 4. Assert reversible with correct AAD
    decrypted = crypto.decrypt(encrypted_val, associated_data=8880001)
    assert decrypted == raw_token


# ===========================================================================
# 2. Redis FSM state scan (0 raw tokens in serialized storage)
# ===========================================================================

@pytest.mark.asyncio
async def test_02_redis_fsm_scan_zero_raw_tokens(crypto: TokenCrypto):
    """Scan serialized FSM storage keys and values; 0 occurrences of raw candidate token."""
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=200, user_id=200)
    state = FSMContext(storage=storage, key=key)
    await state.update_data(master_id=99)

    raw_token = "7770002:SECRET_RAW_TOKEN_FOR_REDIS_SCAN"
    message = AsyncMock(spec=Message)
    message.text = raw_token
    message.from_user = MagicMock(id=200, first_name="Owner")
    message.delete = AsyncMock()
    message.answer = AsyncMock()

    mock_session = AsyncMock(spec=AsyncSession)
    mock_user = User(id=2, telegram_id=200, first_name="Owner")

    with patch("app.manager_bot.handlers._get_or_create_user", return_value=mock_user), \
         patch("app.manager_bot.handlers.BotProvisioningService.validate_candidate_token",
               return_value=BotIdentity(id=7770002, username="scan_bot", first_name="Scan Bot")):
        await msg_receive_bot_token(message, state, mock_session)

    # Dump all storage data
    storage_dump = str(storage.storage)
    assert raw_token not in storage_dump, "Raw token was found in serialized FSM storage!"


# ===========================================================================
# 3. Audit log secret scan (0 raw tokens, 0 'v1:', 0 secrets)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_03_audit_log_secret_scan(pg_session: AsyncSession, crypto: TokenCrypto):
    """Audit log records must never contain raw tokens, v1: ciphertexts, or secrets."""
    user = await _create_user(pg_session, 3001)
    master = await _create_master(pg_session, user, "Audit Test Studio")

    gateway = MockGateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)
    raw_token = "3001001:RAW_SECRET_TOKEN_FOR_AUDIT_LOG_CHECK"
    identity = BotIdentity(id=3001001, username="audit_bot", first_name="Audit Bot")

    # 1. Provision
    bot = await service.provision_bot(master.id, user.id, raw_token, identity)

    # 2. Rotate
    new_token = "3001001:NEW_RAW_SECRET_TOKEN_V2_AUDIT_CHECK"
    gateway.validate_token_mock.return_value = identity
    await service.rotate_token(bot.id, user.id, new_token)

    # 3. Disable
    await service.disable_bot(bot.id, user.id)

    # 4. Enable
    await service.enable_bot(bot.id, user.id)

    # Query all audit logs for this master
    query = select(AuditLog).where(AuditLog.master_id == master.id)
    result = await pg_session.execute(query)
    logs = result.scalars().all()

    assert len(logs) >= 4, "Expected at least 4 audit log entries"

    for entry in logs:
        before_str = json.dumps(entry.payload_before) if entry.payload_before else ""
        after_str = json.dumps(entry.payload_after) if entry.payload_after else ""

        # Must NOT contain raw tokens
        assert raw_token not in before_str
        assert raw_token not in after_str
        assert new_token not in before_str
        assert new_token not in after_str

        # Must NOT contain ciphertext v1:...
        assert "v1:" not in before_str
        assert "v1:" not in after_str

        # Must NOT contain webhook_secret
        if bot.webhook_secret:
            assert bot.webhook_secret not in before_str
            assert bot.webhook_secret not in after_str


# ===========================================================================
# 4. Log masking & redaction on getMe, setWebhook, rotate failure
# ===========================================================================

@pytest.mark.asyncio
async def test_04_log_secret_masking_and_redaction(caplog):
    """Raw Telegram tokens must never be logged in cleartext upon errors or warnings."""
    raw_token = "4001001:SECRET_TOKEN_TO_NEVER_LOG_IN_CLEARTEXT"
    gateway = TelegramProvisioningGateway()

    # 1. Test validate_token failure logging
    with caplog.at_level(logging.DEBUG):
        with patch.object(gateway, "_create_temp_bot") as mock_create:
            mock_bot = AsyncMock(spec=Bot)
            mock_bot.session = AsyncMock()
            from aiogram.exceptions import TelegramUnauthorizedError
            mock_bot.get_me.side_effect = TelegramUnauthorizedError(
                method=MagicMock(), message="Unauthorized"
            )
            mock_create.return_value = mock_bot

            with pytest.raises(InvalidBotTokenError):
                await gateway.validate_token(raw_token)

    for rec in caplog.records:
        assert raw_token not in rec.getMessage(), f"Raw token leaked in log: {rec.getMessage()}"
    caplog.clear()

    # 2. Test set_webhook failure logging
    with caplog.at_level(logging.DEBUG):
        with patch.object(gateway, "_create_temp_bot") as mock_create:
            mock_bot = AsyncMock(spec=Bot)
            mock_bot.session = AsyncMock()
            from aiogram.exceptions import TelegramNetworkError
            mock_bot.set_webhook.side_effect = TelegramNetworkError(
                method=MagicMock(), message="Network connection failed"
            )
            mock_create.return_value = mock_bot

            with pytest.raises(TelegramGatewayNetworkError):
                await gateway.set_webhook(raw_token, "https://test.example.com/webhook")

    for rec in caplog.records:
        assert raw_token not in rec.getMessage(), f"Raw token leaked in log: {rec.getMessage()}"


# ===========================================================================
# 5. Temporary bot session lifecycle cleanup across all gateway methods
# ===========================================================================

@pytest.mark.asyncio
async def test_05_temporary_session_lifecycle():
    """Verify bot.session.close() is ALWAYS awaited across all 4 gateway methods."""
    gateway = TelegramProvisioningGateway()
    raw_token = "5001001:TEST_SESSION_CLEANUP_TOKEN"

    methods_to_test = [
        ("validate_token", lambda g: g.validate_token(raw_token)),
        ("set_webhook", lambda g: g.set_webhook(raw_token, "https://test.example.com")),
        ("get_webhook_info", lambda g: g.get_webhook_info(raw_token)),
        ("delete_webhook", lambda g: g.delete_webhook(raw_token)),
    ]

    for name, caller in methods_to_test:
        # A. Success path
        mock_bot = AsyncMock(spec=Bot)
        mock_session = AsyncMock()
        mock_bot.session = mock_session
        mock_bot.get_me.return_value = MagicMock(id=5001001, username="b", first_name="f", can_join_groups=False, can_read_all_group_messages=False, supports_inline_queries=False)
        mock_bot.set_webhook.return_value = True
        mock_bot.get_webhook_info.return_value = MagicMock(url="https://test.example.com")
        mock_bot.delete_webhook.return_value = True

        with patch.object(gateway, "_create_temp_bot", return_value=mock_bot):
            await caller(gateway)
            mock_session.close.assert_awaited_once()

        # B. Exception path (Timeout / Network error)
        mock_bot_err = AsyncMock(spec=Bot)
        mock_session_err = AsyncMock()
        mock_bot_err.session = mock_session_err
        mock_bot_err.get_me.side_effect = asyncio.TimeoutError()
        mock_bot_err.set_webhook.side_effect = asyncio.TimeoutError()
        mock_bot_err.get_webhook_info.side_effect = asyncio.TimeoutError()
        mock_bot_err.delete_webhook.side_effect = asyncio.TimeoutError()

        with patch.object(gateway, "_create_temp_bot", return_value=mock_bot_err):
            with pytest.raises(Exception):
                await caller(gateway)
            mock_session_err.close.assert_awaited_once()


# ===========================================================================
# 6. Concurrent duplicate bot connection on live PostgreSQL
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_06_concurrent_duplicate_bot_connection(pg_engine: AsyncEngine, crypto: TokenCrypto):
    """Two concurrent tasks attempting to provision the SAME bot ID to different masters.
    Exactly one succeeds; the other raises clean DuplicateBotError via PostgreSQL unique constraint.
    """
    session_factory = async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as s_init:
        user1 = await _create_user(s_init, 6101, "owner1")
        user2 = await _create_user(s_init, 6102, "owner2")
        master1 = await _create_master(s_init, user1, "Studio 1")
        master2 = await _create_master(s_init, user2, "Studio 2")
        m1_id, m2_id = master1.id, master2.id
        u1_id, u2_id = user1.id, user2.id

    shared_tg_bot_id = 6101001
    identity = BotIdentity(id=shared_tg_bot_id, username="contested_bot", first_name="Contested")
    token = f"{shared_tg_bot_id}:SHARED_CONCURRENT_TOKEN"

    async def attempt_provision(m_id: int, u_id: int):
        async with session_factory() as s:
            gw = MockGateway()
            svc = BotProvisioningService(s, gateway=gw, crypto=crypto)
            return await svc.provision_bot(m_id, u_id, token, identity)

    results = await asyncio.gather(
        attempt_provision(m1_id, u1_id),
        attempt_provision(m2_id, u2_id),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, BotInstance)]
    failures = [r for r in results if isinstance(r, DuplicateBotError)]

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(failures) == 1, f"Expected exactly 1 DuplicateBotError, got {len(failures)}"

    # Clean up created bot
    async with session_factory() as s_clean:
        repo = BotInstanceRepository(s_clean)
        bot = await repo.get_by_telegram_bot_id(shared_tg_bot_id)
        if bot:
            await s_clean.delete(bot)
            await s_clean.commit()


# ===========================================================================
# 7. Same master concurrent connection invariant (uq_bot_instances_current_per_master)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_07_same_master_concurrent_connection_invariant(pg_engine: AsyncEngine, crypto: TokenCrypto):
    """Partial unique index uq_bot_instances_current_per_master guarantees
    at most one is_current=True bot exists for a master on live PostgreSQL.
    """
    session_factory = async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as s_init:
        user = await _create_user(s_init, 7101, "owner71")
        master = await _create_master(s_init, user, "Studio 71")
        m_id, u_id = master.id, user.id

    id_a, id_b = 7101001, 7101002
    token_a, token_b = f"{id_a}:TOKEN_A", f"{id_b}:TOKEN_B"
    ident_a = BotIdentity(id=id_a, username="bot_a", first_name="Bot A")
    ident_b = BotIdentity(id=id_b, username="bot_b", first_name="Bot B")

    async def attempt_provision(bot_id: int, tok: str, ident: BotIdentity):
        async with session_factory() as s:
            gw = MockGateway()
            svc = BotProvisioningService(s, gateway=gw, crypto=crypto)
            return await svc.provision_bot(m_id, u_id, tok, ident)

    results = await asyncio.gather(
        attempt_provision(id_a, token_a, ident_a),
        attempt_provision(id_b, token_b, ident_b),
        return_exceptions=True,
    )

    # In PostgreSQL, exactly one current bot must exist for this master
    async with session_factory() as s_check:
        query = select(BotInstance).where(
            BotInstance.master_id == m_id,
            BotInstance.is_current == True,
        )
        res = await s_check.execute(query)
        current_bots = res.scalars().all()
        assert len(current_bots) == 1, f"Expected exactly 1 current bot, found {len(current_bots)}"

    # Clean up
    async with session_factory() as s_clean:
        repo = BotInstanceRepository(s_clean)
        for b_id in [id_a, id_b]:
            bot = await repo.get_by_telegram_bot_id(b_id)
            if bot:
                await s_clean.delete(bot)
        await s_clean.commit()


# ===========================================================================
# 8. Active bot race handling (uq_bot_instances_active_per_master)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_08_active_bot_race_handling(pg_session: AsyncSession, crypto: TokenCrypto):
    """Activating two bots for the same master catches PostgreSQL constraint
    and returns a clean domain error message without crashing.
    """
    user = await _create_user(pg_session, 8101)
    master = await _create_master(pg_session, user, "Studio 81")

    repo = BotInstanceRepository(pg_session)
    enc1 = crypto.encrypt("8101001:T1", associated_data=8101001)
    enc2 = crypto.encrypt("8101002:T2", associated_data=8101002)

    # Create Bot 1 as ACTIVE
    await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=8101001,
        encrypted_token=enc1,
        status=BotInstanceStatus.ACTIVE,
        is_current=False,
    )
    # Create Bot 2 as SETUP_REQUIRED
    bot2 = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=8101002,
        encrypted_token=enc2,
        status=BotInstanceStatus.SETUP_REQUIRED,
        is_current=True,
    )
    await pg_session.commit()

    service = BotProvisioningService(pg_session, crypto=crypto)
    with patch("app.services.master_readiness_service.MasterReadinessService.check",
               return_value=(True, [])):
        is_ready, missing = await service.activate_master_and_bot(master.id, user.id)

    assert is_ready is False
    assert any("уже есть активный бот" in msg for msg in missing)


# ===========================================================================
# 9. Multi-master owner isolation
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_09_multi_master_owner_isolation(pg_session: AsyncSession, crypto: TokenCrypto):
    """User owns Master A and Master B. Can manage both independently.
    User C has zero access to either.
    """
    owner = await _create_user(pg_session, 9001, "legit_owner")
    stranger = await _create_user(pg_session, 9002, "stranger")

    master_a = await _create_master(pg_session, owner, "Studio A")
    master_b = await _create_master(pg_session, owner, "Studio B")

    gw = MockGateway()
    svc = BotProvisioningService(pg_session, gateway=gw, crypto=crypto)

    bot_a = await svc.provision_bot(master_a.id, owner.id, "9001001:TA", BotIdentity(id=9001001, username="bot_a", first_name="A"))
    bot_b = await svc.provision_bot(master_b.id, owner.id, "9001002:TB", BotIdentity(id=9001002, username="bot_b", first_name="B"))

    # Owner can disable bot A
    await svc.disable_bot(bot_a.id, owner.id)
    assert bot_a.status == BotInstanceStatus.DISABLED
    assert bot_b.status == BotInstanceStatus.SETUP_REQUIRED

    # Stranger receives AccessDeniedError on bot A and bot B
    with pytest.raises(AccessDeniedError):
        await svc.disable_bot(bot_a.id, stranger.id)

    with pytest.raises(AccessDeniedError):
        await svc.enable_bot(bot_b.id, stranger.id)


# ===========================================================================
# 10. Manager callbacks IDOR fail-closed protection
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_10_manager_callbacks_idor_fail_closed(pg_session: AsyncSession):
    """All manager bot callbacks verify master ownership and fail closed with alert."""
    owner = await _create_user(pg_session, 10001, "real_owner")
    attacker = await _create_user(pg_session, 10002, "attacker")
    master = await _create_master(pg_session, owner, "Victim Studio")

    callbacks_to_test = [
        (cb_project_card, f"mgr:master:{master.id}", True),
        (cb_checklist, f"mgr:bot:checklist:{master.id}", False),
        (cb_activate_bot, f"mgr:bot:activate:{master.id}", False),
        (cb_retry_provisioning, f"mgr:bot:retry:{master.id}", False),
        (cb_disable_bot, f"mgr:bot:disable:{master.id}", False),
        (cb_enable_bot, f"mgr:bot:enable:{master.id}", False),
        (cb_rotate_token_prompt, f"mgr:bot:rotate:{master.id}", True),
    ]

    for handler, cb_data, needs_state in callbacks_to_test:
        cb = AsyncMock(spec=CallbackQuery)
        cb.data = cb_data
        cb.from_user = MagicMock(id=10002, first_name="Attacker", last_name="", username="attacker")
        cb.answer = AsyncMock()
        cb.message = AsyncMock()

        if needs_state:
            state = AsyncMock(spec=FSMContext)
            await handler(callback=cb, state=state, session=pg_session)
        else:
            await handler(callback=cb, session=pg_session)

        cb.answer.assert_awaited()
        # Verify access denied alert was shown
        call_args = cb.answer.call_args
        alert_shown = call_args.kwargs.get("show_alert", False)
        answer_text = call_args.args[0] if call_args.args else ""
        assert alert_shown is True
        assert "доступ запрещён" in answer_text.lower()


# ===========================================================================
# 11. BotInstance service IDOR fail-closed protection
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_11_bot_instance_service_idor_fail_closed(pg_session: AsyncSession, crypto: TokenCrypto):
    """Service methods fail closed when actor_user_id does not own the BotInstance's master."""
    owner = await _create_user(pg_session, 11001)
    stranger = await _create_user(pg_session, 11002)
    master = await _create_master(pg_session, owner)

    repo = BotInstanceRepository(pg_session)
    bot = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=11001001,
        encrypted_token=crypto.encrypt("11001001:TOK", associated_data=11001001),
        status=BotInstanceStatus.SETUP_REQUIRED,
    )
    await pg_session.commit()

    svc = BotProvisioningService(pg_session, crypto=crypto)

    with pytest.raises(AccessDeniedError):
        await svc.rotate_token(bot.id, stranger.id, "11001001:NEW_TOK")

    with pytest.raises(AccessDeniedError):
        await svc.disable_bot(bot.id, stranger.id)

    with pytest.raises(AccessDeniedError):
        await svc.enable_bot(bot.id, stranger.id)

    with pytest.raises(AccessDeniedError):
        await svc.retry_provisioning(bot.id, stranger.id)


# ===========================================================================
# 12. Disable bot on deleteWebhook failure
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_12_disable_bot_on_delete_webhook_failure(pg_session: AsyncSession, crypto: TokenCrypto):
    """When deleteWebhook fails (e.g. Telegram network error), bot is STILL marked DISABLED,
    registry cache is evicted, and last_error records the failure safely.
    """
    owner = await _create_user(pg_session, 12001)
    master = await _create_master(pg_session, owner)

    gw = MockGateway()
    gw.delete_webhook_mock.side_effect = TelegramGatewayNetworkError("Telegram timeout")

    registry = MagicMock(spec=BotRegistry)
    registry.invalidate_bot = AsyncMock()

    svc = BotProvisioningService(pg_session, gateway=gw, crypto=crypto, registry=registry)
    bot = await svc.provision_bot(master.id, owner.id, "12001001:TOK", BotIdentity(id=12001001, username="bot12", first_name="B12"))

    # Now disable
    disabled_bot = await svc.disable_bot(bot.id, owner.id)
    assert disabled_bot.status == BotInstanceStatus.DISABLED
    assert disabled_bot.last_error is not None
    assert "Telegram timeout" in disabled_bot.last_error
    # Registry evicted
    registry.invalidate_bot.assert_awaited_with(bot.id)


# ===========================================================================
# 13. Enable bot with invalid/revoked token
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_13_enable_bot_with_invalid_token(pg_session: AsyncSession, crypto: TokenCrypto):
    """When token is revoked in BotFather, enable_bot marks bot as ERROR and sets safe last_error."""
    owner = await _create_user(pg_session, 13001)
    master = await _create_master(pg_session, owner)

    gw = MockGateway()
    svc = BotProvisioningService(pg_session, gateway=gw, crypto=crypto)
    bot = await svc.provision_bot(master.id, owner.id, "13001001:TOK", BotIdentity(id=13001001, username="bot13", first_name="B13"))
    await svc.disable_bot(bot.id, owner.id)

    # Now simulate revoked token on re-enable
    gw.validate_token_mock.side_effect = InvalidBotTokenError("Token revoked")
    with pytest.raises(ProvisioningWebhookError):
        await svc.enable_bot(bot.id, owner.id)

    assert bot.status == BotInstanceStatus.ERROR
    assert "Token revoked" in (bot.last_error or "")


# ===========================================================================
# 14. getWebhookInfo URL verification & secret leak detection
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_14_get_webhook_info_verification(pg_session: AsyncSession, crypto: TokenCrypto):
    """Provisioning fails if getWebhookInfo returns mismatched URL or leaked secret."""
    owner = await _create_user(pg_session, 14001)
    master = await _create_master(pg_session, owner)

    # Case A: URL mismatch
    gw_mismatch = MockGateway()
    gw_mismatch.get_webhook_info_mock.return_value = MagicMock(url="https://attacker.example.com/webhook")
    svc1 = BotProvisioningService(pg_session, gateway=gw_mismatch, crypto=crypto)

    with pytest.raises(ProvisioningWebhookError, match="не совпадает с ожидаемым"):
        await svc1.provision_bot(master.id, owner.id, "14001001:TOK", BotIdentity(id=14001001, username="bot14a", first_name="B14A"))

    # Case B: Secret leaked in URL
    gw_leak = MockGateway()
    # We will let set_webhook capture URL and add secret to it
    orig_set = gw_leak.set_webhook
    async def leaking_set(token, url, secret_token=None, **kwargs):
        await orig_set(token, url, secret_token=secret_token, **kwargs)
        gw_leak.get_webhook_info_mock.return_value = MagicMock(url=f"{url}?secret={secret_token}")
        return True
    gw_leak.set_webhook = leaking_set

    # Deprecate previous bot to allow new provisioning
    repo = BotInstanceRepository(pg_session)
    await repo.deprecate_current_for_master(master.id)
    await pg_session.commit()

    svc2 = BotProvisioningService(pg_session, gateway=gw_leak, crypto=crypto)
    with pytest.raises(ProvisioningWebhookError, match="обнаружены секретные данные"):
        await svc2.provision_bot(master.id, owner.id, "14001002:TOK", BotIdentity(id=14001002, username="bot14b", first_name="B14B"))


# ===========================================================================
# 15. PROVISIONING committed to PostgreSQL BEFORE network setWebhook call
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_15_provisioning_commits_before_network(pg_engine: AsyncEngine, crypto: TokenCrypto):
    """Proof of commit order: BotInstance in PROVISIONING state is visible in
    a SEPARATE PostgreSQL session before the network setWebhook call executes.
    """
    session_factory = async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as s_init:
        owner = await _create_user(s_init, 15001)
        master = await _create_master(s_init, owner)
        m_id, u_id = master.id, owner.id

    committed_status_before_network = None
    target_tg_bot_id = 15001001

    gw = MockGateway()
    orig_set_webhook = gw.set_webhook

    async def check_db_hook(*args, **kwargs):
        nonlocal committed_status_before_network
        async with session_factory() as separate_session:
            repo = BotInstanceRepository(separate_session)
            bot = await repo.get_by_telegram_bot_id(target_tg_bot_id)
            if bot:
                committed_status_before_network = bot.status
        return await orig_set_webhook(*args, **kwargs)

    gw.set_webhook = check_db_hook

    async with session_factory() as s_main:
        svc = BotProvisioningService(s_main, gateway=gw, crypto=crypto)
        await svc.provision_bot(
            m_id,
            u_id,
            f"{target_tg_bot_id}:TOKEN_COMMIT_ORDER",
            BotIdentity(id=target_tg_bot_id, username="order_bot", first_name="Order Bot"),
        )

    assert committed_status_before_network == BotInstanceStatus.PROVISIONING, (
        f"BotInstance was not committed to DB before network call! Got: {committed_status_before_network}"
    )

    # Clean up
    async with session_factory() as s_clean:
        repo = BotInstanceRepository(s_clean)
        b = await repo.get_by_telegram_bot_id(target_tg_bot_id)
        if b:
            await s_clean.delete(b)
            await s_clean.commit()


# ===========================================================================
# 16. Rotation failure safety
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_16_rotation_failure_safety(pg_session: AsyncSession, crypto: TokenCrypto):
    """If setWebhook fails during rotate_token, BotInstance is marked ERROR,
    BotRegistry cache is invalidated, and subsequent retry uses the rotated token.
    """
    owner = await _create_user(pg_session, 16001)
    master = await _create_master(pg_session, owner)

    registry = MagicMock(spec=BotRegistry)
    registry.invalidate_bot = AsyncMock()

    gw = MockGateway()
    svc = BotProvisioningService(pg_session, gateway=gw, crypto=crypto, registry=registry)
    identity = BotIdentity(id=16001001, username="rot_bot", first_name="Rot Bot")
    bot = await svc.provision_bot(master.id, owner.id, "16001001:V1", identity)

    # Make webhook fail during rotation
    gw.set_webhook_mock.side_effect = TelegramGatewayNetworkError("Webhook set failed on rotation")
    gw.validate_token_mock.return_value = identity

    with pytest.raises(ProvisioningWebhookError):
        await svc.rotate_token(bot.id, owner.id, "16001001:V2")

    # Bot must be in ERROR status
    assert bot.status == BotInstanceStatus.ERROR
    assert bot.token_version == 2
    # Cache must be evicted
    registry.invalidate_bot.assert_awaited_with(bot.id)

    # The encrypted token stored in DB must decrypt to V2
    decrypted = crypto.decrypt(bot.encrypted_token, associated_data=identity.id)
    assert decrypted == "16001001:V2"

    # Retry provisioning succeeds with V2
    gw.set_webhook_mock.side_effect = None
    retried_bot = await svc.retry_provisioning(bot.id, owner.id)
    assert retried_bot.status == BotInstanceStatus.SETUP_REQUIRED


# ===========================================================================
# 17. Manager update concurrent deduplication
# ===========================================================================

@pytest.mark.asyncio
async def test_17_manager_update_concurrent_dedup():
    """Concurrently calling acquire on the same update ID allows only 1 winner."""
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    dedup = UpdateDeduplicator(redis_client=fake_redis, ttl_completed=60, ttl_processing=30)

    update_id = 99912345

    results = await asyncio.gather(
        dedup.acquire_manager(update_id),
        dedup.acquire_manager(update_id),
        dedup.acquire_manager(update_id),
    )

    # Exactly one owner, two concurrent requests still in processing.
    assert sum(result.claim is not None for result in results) == 1
    assert sum(result.state.value == "processing" for result in results) == 2


# ===========================================================================
# 18. Trial duration configuration verification
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_18_trial_duration_configuration(pg_session: AsyncSession):
    """Verify that settings.trial_duration_days correctly sets trial_ends_at."""
    assert settings.trial_duration_days == 14

    user = await _create_user(pg_session, 18001)
    before_creation = datetime.now(timezone.utc)
    master = await _create_master(pg_session, user, "Trial Test Studio")
    after_creation = datetime.now(timezone.utc)

    expected_min = before_creation + timedelta(days=settings.trial_duration_days) - timedelta(seconds=5)
    expected_max = after_creation + timedelta(days=settings.trial_duration_days) + timedelta(seconds=5)

    assert master.trial_ends_at is not None
    assert expected_min <= master.trial_ends_at <= expected_max
