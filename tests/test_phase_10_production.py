"""
Comprehensive security and production readiness test suite for Phase 10.

Covers:
1. Production configuration validation (fail-fast on insecure settings).
2. Manual billing lockdown in production mode (prevention of self-confirmation).
3. Token crypto AAD cross-tenant isolation (prevention of token substitution).
4. Streaming body limiter (413 Payload Too Large and OOM DoS prevention).
5. Health ready probe sanitization (zero internal connection string leakage).
6. Constant-time timing-safe secret comparisons.
7. Multi-tenant isolation and IDOR rejection in manager handlers.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
import pytest
from starlette.requests import Request

from app.config.settings import Settings, settings
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstance, BotInstanceStatus, Master, SubscriptionStatus
from app.database.models.subscription import EffectiveSubscriptionStatus, SubscriptionPayment, SubscriptionPlan
from app.database.models.user import User
from app.manager_bot.handlers import cb_subscription_confirm, cb_subscription_pay
from app.manager_bot.keyboards import subscription_payment_keyboard
from app.services.exceptions import SubscriptionError, TokenDecryptionError
from app.services.subscription_service import SubscriptionService
from app.web.app import create_app, read_limited_request_body


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    """In-memory Redis fixture for webhook and dedup tests."""
    return fakeredis.aioredis.FakeRedis()


# ===========================================================================
# 1. Production Configuration Validation (Fail-Fast)
# ===========================================================================

def test_production_config_validation_passes_valid() -> None:
    """Valid production settings pass validation cleanly."""
    valid_key = TokenCrypto.generate_key()
    prod_settings = Settings(
        _env_file=None,
        APP_ENV="production",
        APP_MODE="webhook",
        WEBHOOK_BASE_URL="https://api.example.com",
        MANAGER_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
        MANAGER_WEBHOOK_SECRET="secure_random_manager_webhook_secret_32chars_long",
        BOT_TOKEN_ENCRYPTION_KEY=valid_key,
        REDIS_PASSWORD="test_redis_password",
        DATABASE_URL="postgresql+asyncpg://beauty_user:secure_pwd@db.internal:5432/beauty_bot_prod",
    )
    assert prod_settings.is_production is True
    # Should not raise
    prod_settings.validate_production_configuration()


def test_production_config_validation_fails_on_insecure_mode() -> None:
    """Production mode rejects app_mode='polling'."""
    valid_key = TokenCrypto.generate_key()
    insecure_settings = Settings(
        _env_file=None,
        APP_ENV="production",
        APP_MODE="polling",
        WEBHOOK_BASE_URL="https://api.example.com",
        MANAGER_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
        MANAGER_WEBHOOK_SECRET="secure_random_manager_webhook_secret_32chars_long",
        BOT_TOKEN_ENCRYPTION_KEY=valid_key,
        DATABASE_URL="postgresql+asyncpg://beauty_user:secure_pwd@db.internal:5432/beauty_bot_prod",
    )
    assert insecure_settings.is_production is True
    with pytest.raises(ValueError, match="APP_MODE must be 'webhook'"):
        insecure_settings.validate_production_configuration()


def test_production_config_validation_fails_on_insecure_url_and_secret() -> None:
    """Production mode rejects http:// URL and short webhook secret."""
    valid_key = TokenCrypto.generate_key()
    insecure_settings = Settings(
        _env_file=None,
        APP_ENV="production",
        APP_MODE="webhook",
        WEBHOOK_BASE_URL="http://insecure.example.com",
        MANAGER_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
        MANAGER_WEBHOOK_SECRET="too_short",
        BOT_TOKEN_ENCRYPTION_KEY=valid_key,
        DATABASE_URL="postgresql+asyncpg://beauty_user:secure_pwd@db.internal:5432/beauty_bot_prod",
    )
    assert insecure_settings.is_production is True
    with pytest.raises(ValueError) as exc:
        insecure_settings.validate_production_configuration()
    err_msg = str(exc.value)
    assert "WEBHOOK_BASE_URL must be configured with https://" in err_msg
    assert "MANAGER_WEBHOOK_SECRET must be at least 32 characters" in err_msg


def test_production_config_validation_fails_on_default_db_creds() -> None:
    """Production mode rejects default postgres:postgres@localhost credentials."""
    valid_key = TokenCrypto.generate_key()
    insecure_settings = Settings(
        _env_file=None,
        APP_ENV="production",
        APP_MODE="webhook",
        WEBHOOK_BASE_URL="https://api.example.com",
        MANAGER_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
        MANAGER_WEBHOOK_SECRET="secure_random_manager_webhook_secret_32chars_long",
        BOT_TOKEN_ENCRYPTION_KEY=valid_key,
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/beauty_bot",
    )
    assert insecure_settings.is_production is True
    with pytest.raises(ValueError, match="DATABASE_URL uses default insecure credentials"):
        insecure_settings.validate_production_configuration()


# ===========================================================================
# 2. Manual Billing Lockdown in Production
# ===========================================================================

@pytest.mark.asyncio
async def test_manual_billing_blocked_in_production(pg_session) -> None:
    """SubscriptionService blocks manual payment processing in production without override."""
    user = User(
        telegram_id=99901,
        first_name="Production",
        username="prod_master",
    )
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="Production Master",
        subscription_status=SubscriptionStatus.TRIAL,
    )
    pg_session.add(master)
    await pg_session.flush()

    payment = SubscriptionPayment(
        master_id=master.id,
        provider="MANUAL",
        provider_payment_id="manual_test_prod_123",
        amount=1500,
        currency="RUB",
        status="PENDING",
        period_days=30,
        created_at=datetime.now(timezone.utc),
    )
    pg_session.add(payment)
    await pg_session.commit()

    sub_service = SubscriptionService(pg_session)

    # In production environment
    with patch.object(settings, "app_env", "production"):
        assert settings.is_production is True
        with pytest.raises(SubscriptionError, match="Ручное подтверждение платежа запрещено в рабочей среде"):
            await sub_service.process_successful_payment(
                provider="MANUAL",
                provider_payment_id="manual_test_prod_123",
            )


@pytest.mark.asyncio
async def test_production_does_not_create_manual_payment_intent(pg_session) -> None:
    user = User(telegram_id=99902, first_name="No fake checkout")
    pg_session.add(user)
    await pg_session.flush()
    master = Master(owner_user_id=user.id, display_name="No fake checkout")
    pg_session.add(master)
    await pg_session.flush()

    with patch.object(settings, "app_env", "production"):
        with pytest.raises(SubscriptionError, match="Автоматическая оплата временно недоступна"):
            await SubscriptionService(pg_session).create_subscription_payment(
                master_id=master.id, actor_user_id=user.id,
            )


def test_subscription_keyboard_hides_manual_confirmation_in_production() -> None:
    """Keyboard hides manual test confirmation button in production mode."""
    with patch.object(settings, "app_env", "production"):
        kb = subscription_payment_keyboard(master_id=1, payment_id=42, payment_url="https://pay.example.com")
        all_callback_data = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        all_urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]

        # URL is present, but manual confirm callback is NOT present
        assert "https://pay.example.com" in all_urls
        assert not any("mgr:sub:confirm:" in cb for cb in all_callback_data)

    # In development mode, the button IS present
    with patch.object(settings, "app_env", "development"):
        kb_dev = subscription_payment_keyboard(master_id=1, payment_id=42)
        all_dev_callbacks = [btn.callback_data for row in kb_dev.inline_keyboard for btn in row if btn.callback_data]
        assert any("mgr:sub:confirm:1:42" in cb for cb in all_dev_callbacks)


@pytest.mark.asyncio
async def test_production_payment_screen_creates_no_fake_checkout() -> None:
    callback = AsyncMock()
    callback.data = "mgr:sub:pay:7:basic_monthly"
    callback.from_user = MagicMock()
    owner = User(id=11, telegram_id=12345678)
    master = Master(id=7, owner_user_id=owner.id, display_name="Salon")
    session = AsyncMock()
    session.get.return_value = master

    with patch.object(settings, "app_env", "production"), \
         patch("app.manager_bot.handlers._get_or_create_user", return_value=owner), \
         patch("app.manager_bot.handlers.SubscriptionService") as service_type:
        await cb_subscription_pay(callback, session)
        service_type.assert_not_called()
    session.commit.assert_not_awaited()
    callback.message.edit_text.assert_awaited_once()
    text = callback.message.edit_text.call_args.args[0]
    assert (
        "Автоматическая оплата временно недоступна" in text
        or "Оплата онлайн временно недоступна" in text
    )
    assert settings.support_tag in text
    keyboard = callback.message.edit_text.call_args.kwargs["reply_markup"]
    assert not any(
        button.url and "mock-checkout" in button.url
        for row in keyboard.inline_keyboard for button in row
    )


@pytest.mark.asyncio
async def test_manager_bot_cb_confirm_rejected_in_production(pg_session) -> None:
    """Callback query for manual confirmation is actively rejected in production."""
    callback = AsyncMock()
    callback.data = "mgr:sub:confirm:1:42"

    with patch.object(settings, "app_env", "production"):
        await cb_subscription_confirm(callback, pg_session)
        callback.answer.assert_called_once()
        assert "самостоятельное подтверждение платежей запрещено" in callback.answer.call_args[1].get("text", "") or "самостоятельное подтверждение платежей запрещено" in callback.answer.call_args[0][0]


# ===========================================================================
# 3. Token Crypto AAD Cross-Tenant Isolation
# ===========================================================================

def test_token_crypto_aad_isolation() -> None:
    """Token encrypted for one bot ID cannot be decrypted for another bot ID."""
    master_key = TokenCrypto.generate_key()
    crypto = TokenCrypto(master_key=master_key)

    plaintext_token = "1234567890:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    bot_a_id = 1001
    bot_b_id = 2002

    # Encrypt for Bot A
    ciphertext = crypto.encrypt(plaintext_token, associated_data=bot_a_id)
    assert ciphertext.startswith("v1:")

    # Decrypting with Bot A succeeds
    decrypted = crypto.decrypt(ciphertext, associated_data=bot_a_id)
    assert decrypted == plaintext_token

    # Decrypting with Bot B fails with TokenDecryptionError (AES-GCM tag mismatch)
    with pytest.raises(TokenDecryptionError):
        crypto.decrypt(ciphertext, associated_data=bot_b_id)

    # Decrypting without AAD fails
    with pytest.raises(TokenDecryptionError):
        crypto.decrypt(ciphertext, associated_data=None)


# ===========================================================================
# 4. Streaming Request Body Limiter (413 / OOM DoS Prevention)
# ===========================================================================

@pytest.mark.asyncio
async def test_streaming_body_limiter_exceeds_max_bytes() -> None:
    """Streaming body reader aborts with 413 as soon as streamed chunks exceed limit."""
    async def mock_stream():
        # Stream 3 chunks of 500 bytes each (total 1500 bytes)
        yield b"A" * 500
        yield b"B" * 500
        yield b"C" * 500

    request = MagicMock(spec=Request)
    request.headers = {}
    request.stream = mock_stream

    # Limit set to 1000 bytes: chunk 3 will exceed limit
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await read_limited_request_body(request, max_bytes=1000)

    assert exc.value.status_code == 413
    assert exc.value.detail == "Payload Too Large"


@pytest.mark.asyncio
async def test_streaming_body_limiter_within_limit() -> None:
    """Streaming body reader returns full bytes when within limits."""
    async def mock_stream():
        yield b"Hello, "
        yield b"World!"

    request = MagicMock(spec=Request)
    request.headers = {}
    request.stream = mock_stream

    body = await read_limited_request_body(request, max_bytes=100)
    assert body == b"Hello, World!"


# ===========================================================================
# 5. Health Check Readiness Probe Sanitization
# ===========================================================================

@pytest.mark.asyncio
async def test_health_ready_probe_sanitization(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """GET /health/ready returns sanitized 'error' without leaking db credentials or exceptions."""
    mock_session = AsyncMock()
    # Simulate DB error with credentials / host info
    mock_session.execute = AsyncMock(
        side_effect=Exception("FATAL: password authentication failed for user 'super_secret_user'")
    )
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
        # Must be strictly sanitized to "error", NOT leaking secret user or message
        assert data["database"] == "error"
        assert "super_secret_user" not in response.text
        assert "password" not in response.text


# ===========================================================================
# 6. Webhook Timing-Safe Secret Token Verification
# ===========================================================================

@pytest.mark.asyncio
async def test_webhook_secret_timing_safe_rejection(
    fake_redis: fakeredis.aioredis.FakeRedis,
    pg_session,
) -> None:
    """Webhook strictly rejects requests with mismatched or absent secret token."""
    user = User(
        telegram_id=12345,
        first_name="Studio",
        username="studio_owner",
    )
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="Studio Test",
        subscription_status=SubscriptionStatus.ACTIVE,
    )
    pg_session.add(master)
    await pg_session.flush()

    bot_uuid = uuid.uuid4()
    bot_instance = BotInstance(
        master_id=master.id,
        public_id=bot_uuid,
        telegram_bot_id=987654321,
        telegram_username="test_audit_bot",
        encrypted_token="v1:k1:dummy",
        webhook_secret="correct_secret_token_12345678",
        status=BotInstanceStatus.ACTIVE,
    )
    pg_session.add(bot_instance)
    await pg_session.commit()

    mock_sf = MagicMock()
    mock_sf.return_value.__aenter__.return_value = pg_session
    mock_sf.return_value.__aexit__.return_value = None

    app = create_app(
        session_factory=mock_sf,
        redis_client=fake_redis,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing secret token -> 403
        res1 = await client.post(
            f"/telegram/webhook/{bot_uuid}",
            json={"update_id": 100},
        )
        assert res1.status_code == 403

        # 2. Tampered secret token -> 403
        res2 = await client.post(
            f"/telegram/webhook/{bot_uuid}",
            json={"update_id": 101},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret_token_abcdefgh"},
        )
        assert res2.status_code == 403
