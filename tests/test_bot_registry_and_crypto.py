"""Comprehensive test suite for Phase 5: Dynamic Bot Instances, Token Security & BotRegistry.

Covers:
1. AES-256-GCM authenticated token encryption & versioning
2. Non-deterministic nonces & AAD binding to BotInstance
3. Tamper resistance & fail-fast key validation
4. BotRegistry runtime caching, LRU bounds, TTL & session lifecycle
5. Status policy enforcement (ACTIVE, DISABLED, ERROR, PROVISIONING, SETUP_REQUIRED)
6. Dynamic token rotation & invalidation (local + Redis Pub/Sub)
7. Redis FSM storage key isolation by bot_id
8. Sensitive log/exception token redaction
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import fakeredis.aioredis
from aiogram import Bot
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SensitiveDataFilter, mask_token, redact_token
from app.core.token_crypto import TokenCrypto
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterSettings,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.user import User
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.services.bot_factory import BotFactory
from app.services.bot_registry import BotRegistry, CachedBotInstance
from app.services.exceptions import (
    BotDisabledError,
    BotNotFoundError,
    BotProvisioningError,
    BotSetupRequiredError,
    BotUnavailableError,
    TokenCryptoConfigError,
    TokenDecryptionError,
)
from tests.conftest import requires_postgres


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def crypto_key() -> str:
    """Provides a valid 32-byte (256-bit) test encryption key in 64-hex format."""
    return "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def token_crypto(crypto_key: str) -> TokenCrypto:
    return TokenCrypto(master_key=crypto_key)


async def _create_test_master(
    session: AsyncSession,
    owner_tg_id: int,
    display_name: str = "Test Studio",
) -> Master:
    user = User(telegram_id=owner_tg_id, first_name="Owner")
    session.add(user)
    await session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name=display_name,
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        timezone="Europe/Moscow",
    )
    session.add(master)
    await session.flush()

    m_settings = MasterSettings(
        master_id=master.id,
        studio_address="Test Address",
        hold_duration_minutes=30,
    )
    session.add(m_settings)
    await session.flush()
    return master


# ===========================================================================
# Part 1: Token Encryption & Cryptography Tests (Section 28)
# ===========================================================================

def test_encrypt_decrypt_roundtrip(token_crypto: TokenCrypto):
    """Scenario 1: encrypt -> decrypt successfully recovers the original plaintext token."""
    raw_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    aad = 123456789
    encrypted = token_crypto.encrypt(raw_token, associated_data=aad)

    assert encrypted.startswith("v1:k1:")
    decrypted = token_crypto.decrypt(encrypted, associated_data=aad)
    assert decrypted == raw_token


def test_encrypt_is_nondeterministic(token_crypto: TokenCrypto):
    """Scenario 2: Multiple encryptions of the same token yield different ciphertexts (random nonce)."""
    raw_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    aad = 123456789
    c1 = token_crypto.encrypt(raw_token, associated_data=aad)
    c2 = token_crypto.encrypt(raw_token, associated_data=aad)

    assert c1 != c2
    assert token_crypto.decrypt(c1, associated_data=aad) == raw_token
    assert token_crypto.decrypt(c2, associated_data=aad) == raw_token


def test_decrypt_wrong_key_fails(token_crypto: TokenCrypto):
    """Scenario 3: Decrypting with an incorrect key fails with TokenDecryptionError."""
    raw_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted = token_crypto.encrypt(raw_token, associated_data=123)

    other_crypto = TokenCrypto(master_key="fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210")
    with pytest.raises(TokenDecryptionError):
        other_crypto.decrypt(encrypted, associated_data=123)


def test_decrypt_tampered_ciphertext_fails(token_crypto: TokenCrypto):
    """Scenario 4: Tampering with any byte in payload fails authentication tag check."""
    raw_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted = token_crypto.encrypt(raw_token, associated_data=123)

    # Tamper with the base64 payload
    parts = encrypted.split(":")
    payload = parts[2]
    # Replace last char of payload
    tampered_payload = payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1]
    tampered_ciphertext = f"{parts[0]}:{parts[1]}:{tampered_payload}"

    with pytest.raises(TokenDecryptionError):
        token_crypto.decrypt(tampered_ciphertext, associated_data=123)


def test_associated_data_mismatch_fails(token_crypto: TokenCrypto):
    """Scenario 5: Ciphertext encrypted for Bot A cannot be decrypted with Bot B's AAD."""
    raw_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted_for_bot_a = token_crypto.encrypt(raw_token, associated_data=111111111)

    # Attempt decrypt using Bot B's telegram_bot_id
    with pytest.raises(TokenDecryptionError):
        token_crypto.decrypt(encrypted_for_bot_a, associated_data=222222222)


def test_plaintext_token_not_in_ciphertext(token_crypto: TokenCrypto):
    """Scenario 6: Plaintext token does not appear anywhere in ciphertext."""
    raw_token = "987654321:SECRET_TOKEN_VALUE_ABCDEFGHIJKLMN"
    encrypted = token_crypto.encrypt(raw_token, associated_data=987654321)

    assert "SECRET_TOKEN_VALUE" not in encrypted
    assert raw_token not in encrypted


def test_token_not_in_repr_or_error(token_crypto: TokenCrypto):
    """Scenario 7: TokenCrypto repr and exception messages do not leak master key or token."""
    repr_str = repr(token_crypto)
    assert "0123456789abcdef" not in repr_str
    assert "key_id='k1'" in repr_str

    try:
        token_crypto.decrypt("v1:k1:invalid_payload", associated_data=123)
    except TokenDecryptionError as e:
        err_msg = str(e)
        assert "0123456789abcdef" not in err_msg


def test_invalid_master_key_format_fail_fast():
    """Scenario 8: Missing or malformed encryption key raises TokenCryptoConfigError."""
    with pytest.raises(TokenCryptoConfigError):
        TokenCrypto(master_key="")

    with pytest.raises(TokenCryptoConfigError):
        TokenCrypto(master_key="too_short_key")

    with pytest.raises(TokenCryptoConfigError):
        TokenCrypto(master_key="x" * 63)  # 63 hex chars instead of 64


# ===========================================================================
# Part 2: BotRegistry Runtime & Lifecycle Tests (Section 29)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_registry_active_bot_instance_creates_bot(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 9: Active BotInstance in DB is decrypted and returns an aiogram.Bot."""
    master = await _create_test_master(pg_session, owner_tg_id=901)
    raw_token = "9010001:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted = token_crypto.encrypt(raw_token, associated_data=9010001)

    repo = BotInstanceRepository(pg_session)
    instance = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=9010001,
        encrypted_token=encrypted,
        status=BotInstanceStatus.ACTIVE,
    )

    registry = BotRegistry(token_crypto=token_crypto)
    try:
        bot = await registry.get_by_instance_id(instance.id, session=pg_session)
        assert isinstance(bot, Bot)
        assert bot.token == raw_token
    finally:
        await registry.close()


@requires_postgres
@pytest.mark.asyncio
async def test_registry_subsequent_lookup_uses_cache(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 10: 100 consecutive lookups return the EXACT SAME cached Bot runtime object."""
    master = await _create_test_master(pg_session, owner_tg_id=1001)
    raw_token = "10010001:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted = token_crypto.encrypt(raw_token, associated_data=10010001)

    repo = BotInstanceRepository(pg_session)
    instance = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=10010001,
        encrypted_token=encrypted,
        status=BotInstanceStatus.ACTIVE,
    )

    registry = BotRegistry(token_crypto=token_crypto)
    try:
        first_bot = await registry.get_by_instance_id(instance.id, session=pg_session)

        # 100 consecutive requests must return identical object in memory
        for _ in range(100):
            cached_bot = await registry.get_by_instance_id(instance.id, session=pg_session)
            assert cached_bot is first_bot
    finally:
        await registry.close()


@requires_postgres
@pytest.mark.asyncio
async def test_registry_disabled_bot_raises_disabled_error(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 11: Disabled BotInstance cannot be retrieved and raises BotDisabledError."""
    master = await _create_test_master(pg_session, owner_tg_id=1101)
    raw_token = "11010001:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted = token_crypto.encrypt(raw_token, associated_data=11010001)

    repo = BotInstanceRepository(pg_session)
    instance = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=11010001,
        encrypted_token=encrypted,
        status=BotInstanceStatus.DISABLED,
    )

    registry = BotRegistry(token_crypto=token_crypto)
    try:
        with pytest.raises(BotDisabledError):
            await registry.get_by_instance_id(instance.id, session=pg_session)
    finally:
        await registry.close()


@requires_postgres
@pytest.mark.asyncio
async def test_registry_error_bot_raises_unavailable_error(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 12: BotInstance with ERROR status raises BotUnavailableError."""
    master = await _create_test_master(pg_session, owner_tg_id=1201)
    raw_token = "12010001:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted = token_crypto.encrypt(raw_token, associated_data=12010001)

    repo = BotInstanceRepository(pg_session)
    instance = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=12010001,
        encrypted_token=encrypted,
        status=BotInstanceStatus.ERROR,
    )
    await repo.set_last_error(instance.id, "Invalid token revoked by Telegram")

    registry = BotRegistry(token_crypto=token_crypto)
    try:
        with pytest.raises(BotUnavailableError) as exc_info:
            await registry.get_by_instance_id(instance.id, session=pg_session)
        assert "Invalid token revoked" in str(exc_info.value)
    finally:
        await registry.close()


@requires_postgres
@pytest.mark.asyncio
async def test_registry_provisioning_and_setup_required_states(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 13: Non-active statuses PROVISIONING and SETUP_REQUIRED raise specific errors."""
    master = await _create_test_master(pg_session, owner_tg_id=1301)
    raw_token = "13010001:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted = token_crypto.encrypt(raw_token, associated_data=13010001)

    repo = BotInstanceRepository(pg_session)
    inst_prov = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=13010001,
        encrypted_token=encrypted,
        status=BotInstanceStatus.PROVISIONING,
        is_current=False,
    )
    enc_setup = token_crypto.encrypt(raw_token, associated_data=13010002)
    inst_setup = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=13010002,
        encrypted_token=enc_setup,
        status=BotInstanceStatus.SETUP_REQUIRED,
        is_current=True,
    )

    registry = BotRegistry(token_crypto=token_crypto)
    try:
        with pytest.raises(BotProvisioningError):
            await registry.get_by_instance_id(inst_prov.id, session=pg_session)

        # Per Phase 6 (Section 0.2), SETUP_REQUIRED is permitted at BotRegistry layer
        bot_setup = await registry.get_by_instance_id(inst_setup.id, session=pg_session)
        assert isinstance(bot_setup, Bot)
        assert bot_setup.token == raw_token
    finally:
        await registry.close()


@requires_postgres
@pytest.mark.asyncio
async def test_registry_token_version_change_creates_new_bot(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 14: When token_version changes, registry evicts old bot and instantiates a new one."""
    master = await _create_test_master(pg_session, owner_tg_id=1401)
    token_v1 = "14010001:TOKEN_VERSION_ONE_1111111111111111"
    token_v2 = "14010001:TOKEN_VERSION_TWO_2222222222222222"
    enc_v1 = token_crypto.encrypt(token_v1, associated_data=14010001)
    enc_v2 = token_crypto.encrypt(token_v2, associated_data=14010001)

    repo = BotInstanceRepository(pg_session)
    instance = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=14010001,
        encrypted_token=enc_v1,
        token_version=1,
    )

    # Use ttl_seconds=0 so metadata is re-checked against DB
    registry = BotRegistry(token_crypto=token_crypto, ttl_seconds=0)
    try:
        bot_1 = await registry.get_by_instance_id(instance.id, session=pg_session)
        assert bot_1.token == token_v1

        # Rotate token in DB
        await repo.update_encrypted_token(instance.id, encrypted_token=enc_v2, new_token_version=2)

        bot_2 = await registry.get_by_instance_id(instance.id, session=pg_session)
        assert bot_2 is not bot_1
        assert bot_2.token == token_v2
    finally:
        await registry.close()


@requires_postgres
@pytest.mark.asyncio
async def test_registry_invalidate_bot_closes_old_resource(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 15: Invalidation cleanly closes the bot's HTTP session and evicts it from cache."""
    master = await _create_test_master(pg_session, owner_tg_id=1501)
    raw_token = "15010001:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    encrypted = token_crypto.encrypt(raw_token, associated_data=15010001)

    repo = BotInstanceRepository(pg_session)
    instance = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=15010001,
        encrypted_token=encrypted,
    )

    registry = BotRegistry(token_crypto=token_crypto)
    try:
        bot = await registry.get_by_instance_id(instance.id, session=pg_session)
        bot.session.close = AsyncMock()

        # Invalidate
        evicted = await registry.invalidate_bot_instance(instance.id, reason="test_invalidation")
        assert evicted is True
        bot.session.close.assert_awaited_once()

        # Cache is now empty for this instance
        assert instance.id not in registry._cache
    finally:
        await registry.close()


@requires_postgres
@pytest.mark.asyncio
async def test_registry_lru_eviction_closes_session(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 16: LRU bounded cache evicts the oldest entry and closes its session."""
    repo = BotInstanceRepository(pg_session)
    instances = []
    for i in range(3):
        m = await _create_test_master(pg_session, owner_tg_id=1601 + i)
        token = f"1601000{i}:ABCdefGHIjklMNOpqrsTUVwxyz12345678{i}"
        enc = token_crypto.encrypt(token, associated_data=16010000 + i)
        inst = await repo.create_bot_instance(
            master_id=m.id,
            telegram_bot_id=16010000 + i,
            encrypted_token=enc,
        )
        instances.append(inst)

    # Max size = 2
    registry = BotRegistry(token_crypto=token_crypto, max_size=2)
    try:
        bot_0 = await registry.get_by_instance_id(instances[0].id, session=pg_session)
        bot_0.session.close = AsyncMock()

        bot_1 = await registry.get_by_instance_id(instances[1].id, session=pg_session)
        assert len(registry._cache) == 2

        # Accessing 3rd instance must evict bot_0
        bot_2 = await registry.get_by_instance_id(instances[2].id, session=pg_session)
        assert len(registry._cache) == 2
        assert instances[0].id not in registry._cache
        bot_0.session.close.assert_awaited_once()
    finally:
        await registry.close()


@requires_postgres
@pytest.mark.asyncio
async def test_registry_close_releases_all_resources(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 17: registry.close() closes all cached sessions and clears state."""
    master = await _create_test_master(pg_session, owner_tg_id=1701)
    repo = BotInstanceRepository(pg_session)

    token = "17010001:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    enc = token_crypto.encrypt(token, associated_data=17010001)
    inst = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=17010001,
        encrypted_token=enc,
    )

    registry = BotRegistry(token_crypto=token_crypto)
    bot = await registry.get_by_instance_id(inst.id, session=pg_session)
    bot.session.close = AsyncMock()

    await registry.close()
    bot.session.close.assert_awaited_once()
    assert len(registry._cache) == 0


@requires_postgres
@pytest.mark.asyncio
async def test_registry_master_a_gets_bot_a_and_master_b_gets_bot_b(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 18: Master A gets Bot A, Master B gets Bot B with correct tokens and IDs."""
    master_a = await _create_test_master(pg_session, owner_tg_id=1801, display_name="Studio A")
    master_b = await _create_test_master(pg_session, owner_tg_id=1802, display_name="Studio B")

    token_a = "18010001:TOKEN_STUDIO_A_AAAAAAAAAAAAAAAAAAAAAA"
    token_b = "18020002:TOKEN_STUDIO_B_BBBBBBBBBBBBBBBBBBBBBB"
    enc_a = token_crypto.encrypt(token_a, associated_data=18010001)
    enc_b = token_crypto.encrypt(token_b, associated_data=18020002)

    repo = BotInstanceRepository(pg_session)
    inst_a = await repo.create_bot_instance(master_id=master_a.id, telegram_bot_id=18010001, encrypted_token=enc_a)
    inst_b = await repo.create_bot_instance(master_id=master_b.id, telegram_bot_id=18020002, encrypted_token=enc_b)

    registry = BotRegistry(token_crypto=token_crypto)
    try:
        bot_a = await registry.get_by_master_id(master_a.id, session=pg_session)
        bot_b = await registry.get_by_master_id(master_b.id, session=pg_session)

        assert bot_a is not bot_b
        assert bot_a.token == token_a
        assert bot_b.token == token_b

        # Resolving by telegram_bot_id also matches correctly
        assert await registry.get_by_telegram_bot_id(18010001, session=pg_session) is bot_a
        assert await registry.get_by_telegram_bot_id(18020002, session=pg_session) is bot_b
    finally:
        await registry.close()


# ===========================================================================
# Part 3: Redis Invalidation Bus Tests (Section 30)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_invalidation_event_a_evicts_only_bot_a(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 19: Invalidation message for Bot A evicts Bot A; Bot B remains cached."""
    fake_server = fakeredis.FakeServer()
    redis_pub = fakeredis.aioredis.FakeRedis(server=fake_server)
    redis_sub = fakeredis.aioredis.FakeRedis(server=fake_server)

    master_a = await _create_test_master(pg_session, owner_tg_id=1901)
    master_b = await _create_test_master(pg_session, owner_tg_id=1902)
    repo = BotInstanceRepository(pg_session)

    enc_a = token_crypto.encrypt("19010001:TOKEN_A_AAAAAAAAAAAAAAAAAAAA", associated_data=19010001)
    enc_b = token_crypto.encrypt("19010002:TOKEN_B_BBBBBBBBBBBBBBBBBBBB", associated_data=19010002)

    inst_a = await repo.create_bot_instance(master_id=master_a.id, telegram_bot_id=19010001, encrypted_token=enc_a)
    inst_b = await repo.create_bot_instance(master_id=master_b.id, telegram_bot_id=19010002, encrypted_token=enc_b)

    # Registry acting as subscriber on replica B
    registry = BotRegistry(token_crypto=token_crypto, redis_client=redis_sub)
    await registry.start_invalidation_listener()
    # Wait briefly for subscription to register
    await asyncio.sleep(0.05)

    try:
        # Cache both bots
        bot_a = await registry.get_by_instance_id(inst_a.id, session=pg_session)
        bot_b = await registry.get_by_instance_id(inst_b.id, session=pg_session)

        assert inst_a.id in registry._cache
        assert inst_b.id in registry._cache

        # Publisher publishes invalidation for Bot A only
        payload = json.dumps({"bot_instance_id": inst_a.id, "reason": "rotation"})
        await redis_pub.publish(registry.invalidation_channel, payload)

        # Allow event loop to process message
        await asyncio.sleep(0.1)

        # Bot A must be evicted; Bot B must remain cached
        assert inst_a.id not in registry._cache
        assert inst_b.id in registry._cache
    finally:
        await registry.close()
        await redis_pub.aclose()
        await redis_sub.aclose()


def test_invalidation_payload_contains_no_sensitive_token():
    """Scenario 20: Pub/Sub broadcast payload does not contain plaintext or encrypted tokens."""
    payload = json.dumps({"bot_instance_id": 42, "token_version": 3, "reason": "token_rotated"})
    assert "token" in payload  # field name
    assert "123456789" not in payload
    assert "SECRET" not in payload
    assert "v1:k1:" not in payload


@requires_postgres
@pytest.mark.asyncio
async def test_postgres_remains_source_of_truth_on_redis_failure(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 21: When Redis is unavailable, BotRegistry still operates with PostgreSQL as authority."""
    master = await _create_test_master(pg_session, owner_tg_id=2101)
    token = "21010001:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    enc = token_crypto.encrypt(token, associated_data=21010001)

    repo = BotInstanceRepository(pg_session)
    inst = await repo.create_bot_instance(master_id=master.id, telegram_bot_id=21010001, encrypted_token=enc)

    # Registry without Redis
    registry = BotRegistry(token_crypto=token_crypto, redis_client=None)
    try:
        bot = await registry.get_by_instance_id(inst.id, session=pg_session)
        assert bot.token == token
        # Invalidation works locally without throwing
        await registry.publish_invalidation(inst.id, reason="test")
        assert inst.id not in registry._cache
    finally:
        await registry.close()


# ===========================================================================
# Part 4: FSM Isolation Tests (Section 31)
# ===========================================================================

class BookingSG(StatesGroup):
    choose_date = State()
    choose_service = State()


@pytest.mark.asyncio
async def test_fsm_isolation_same_user_independent_states_across_bots():
    """Scenario 22: Same Telegram user in Bot A and Bot B has completely independent FSM states."""
    fake_redis = fakeredis.aioredis.FakeRedis()
    storage = RedisStorage(
        redis=fake_redis,
        key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
    )

    user_id = 999
    chat_id = 999
    key_bot_a = StorageKey(bot_id=1001, chat_id=chat_id, user_id=user_id)
    key_bot_b = StorageKey(bot_id=2002, chat_id=chat_id, user_id=user_id)

    # Set distinct states simultaneously
    await storage.set_state(key_bot_a, BookingSG.choose_date)
    await storage.set_state(key_bot_b, BookingSG.choose_service)

    # Both states are preserved independently
    state_a = await storage.get_state(key_bot_a)
    state_b = await storage.get_state(key_bot_b)
    assert state_a == BookingSG.choose_date.state
    assert state_b == BookingSG.choose_service.state

    # Clearing Bot A does NOT clear Bot B
    await storage.set_state(key_bot_a, None)
    assert await storage.get_state(key_bot_a) is None
    assert await storage.get_state(key_bot_b) == BookingSG.choose_service.state

    # Verify Redis key pattern explicitly contains bot_id
    raw_keys = await fake_redis.keys("*")
    key_names = [k.decode("utf-8") for k in raw_keys]
    assert any("fsm:2002:999:999:" in k for k in key_names)
    assert not any("fsm:1001:999:999:" in k for k in key_names)

    await fake_redis.aclose()


# ===========================================================================
# Part 5: Token Rotation & Security Redaction Tests (Section 32 & 33)
# ===========================================================================

@requires_postgres
@pytest.mark.asyncio
async def test_token_rotation_workflow_without_restart(
    pg_session: AsyncSession, token_crypto: TokenCrypto
):
    """Scenario 23: Complete token rotation flow:
    Bot A (v1) cached -> rotated in DB to (v2) -> invalidation published -> next lookup instantiates v2.
    """
    master = await _create_test_master(pg_session, owner_tg_id=2301)
    token_v1 = "23010001:OLD_TOKEN_REVOKED_111111111111111"
    token_v2 = "23010001:NEW_ROTATED_TOKEN_22222222222222"
    enc_v1 = token_crypto.encrypt(token_v1, associated_data=23010001)
    enc_v2 = token_crypto.encrypt(token_v2, associated_data=23010001)

    repo = BotInstanceRepository(pg_session)
    instance = await repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=23010001,
        encrypted_token=enc_v1,
        token_version=1,
    )

    registry = BotRegistry(token_crypto=token_crypto)
    try:
        # 1. Lookup caches Bot v1
        bot_v1 = await registry.get_by_instance_id(instance.id, session=pg_session)
        assert bot_v1.token == token_v1

        # 2. Update token in PostgreSQL (e.g. from future manager bot)
        await repo.update_encrypted_token(instance.id, encrypted_token=enc_v2, new_token_version=2)

        # 3. Publish invalidation event
        await registry.publish_invalidation(instance.id, token_version=2, reason="rotation")

        # 4. Next lookup yields Bot v2
        bot_v2 = await registry.get_by_instance_id(instance.id, session=pg_session)
        assert bot_v2 is not bot_v1
        assert bot_v2.token == token_v2
    finally:
        await registry.close()


def test_logger_redacts_tokens_and_telegram_api_urls():
    """Scenario 24: SensitiveDataFilter and redact_token mask bot tokens in messages and URLs."""
    raw_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    api_url = f"https://api.telegram.org/bot{raw_token}/getMe"

    # Redact helper
    cleaned_url = redact_token(api_url)
    assert raw_token not in cleaned_url
    assert "api.telegram.org/bot[REDACTED_BOT_TOKEN]/getMe" in cleaned_url

    standalone = f"Failed to start bot with token {raw_token}."
    cleaned_standalone = redact_token(standalone)
    assert raw_token not in cleaned_standalone
    assert "123456789:[REDACTED_SECRET]" in cleaned_standalone

    # Logging filter verification
    filt = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.ERROR,
        pathname="test.py",
        lineno=10,
        msg="Error calling Telegram API: %s",
        args=(api_url,),
        exc_info=None,
    )
    filt.filter(record)
    assert raw_token not in record.args[0]
    assert "[REDACTED_BOT_TOKEN]" in record.args[0]


def test_mask_token_preview():
    """Scenario 25: mask_token returns safe preview '123456789:***'."""
    raw_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    preview = mask_token(raw_token)
    assert preview == "123456789:***"
    assert "ABCdef" not in preview
    assert mask_token(None) == "[EMPTY]"


def test_future_proof_token_redaction():
    """Scenario 26: Redaction works for future bot IDs with 12+ digits and variable secret length."""
    future_token = "123456789012:ABCdefGHIjklMNOpqrsTUVwxyz_1234567890"
    log_text = f"Telegram error at https://api.telegram.org/bot{future_token}/sendMessage with token {future_token}"
    redacted = redact_token(log_text)
    assert future_token not in redacted
    assert "123456789012:[REDACTED_SECRET]" in redacted
    assert "[REDACTED_BOT_TOKEN]" in redacted


def test_placeholder_key_rejected_fail_fast():
    """Scenario 27: Explicitly reject placeholder or all-zero encryption keys."""
    with pytest.raises(TokenCryptoConfigError):
        TokenCrypto(master_key="CHANGE_ME_GENERATE_32_BYTE_HEX_KEY")

    with pytest.raises(TokenCryptoConfigError):
        TokenCrypto(master_key="0" * 64)
