"""Comprehensive test suite for Telegram Managed Bots (Bot API 9.6) flow."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterStatus, SubscriptionStatus
from app.database.models.user import User
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.services.audit_service import AuditEvent, AuditService
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.exceptions import AccessDeniedError, DuplicateBotError, ProvisioningWebhookError
from app.services.managed_bot_service import ManagedBotService
from app.services.telegram_provisioning_gateway import BotIdentity, TelegramProvisioningGateway
from app.manager_bot.keyboards import (
    bot_connect_method_choice_keyboard,
    managed_bot_prepare_keyboard,
    managed_bot_reply_keyboard,
    managed_bot_rotate_keyboard,
    managed_bot_success_keyboard,
)
from tests.conftest import requires_postgres


# ---------------------------------------------------------------------------
# Unit Tests: ManagedBotService
# ---------------------------------------------------------------------------

class TestManagedBotService:
    def test_transliteration_and_normalization(self):
        svc = ManagedBotService()
        
        # Test Cyrillic normalization to valid Telegram bot handle
        assert svc.transliterate_cyrillic("Анна") == "anna"
        from app.services.managed_bot_service import sanitize_base_slug
        assert sanitize_base_slug("Маникюр & Ресницы") == "manikyur_resnitsy"
        
        username, err = svc.normalize_username("beauty_studio")
        assert err is None
        assert username == "beauty_studio_bot"
        
        # Username already ending with bot
        username, err = svc.normalize_username("mycoolbot")
        assert err is None
        assert username == "mycoolbot"
        
        # Validation checks
        _, err = svc.normalize_username("a" * 35)
        assert "от 5 до 32" in err
        
        _, err = svc.normalize_username("bad!name@bot")
        assert "только латинские буквы" in err

    def test_suggest_username_and_name(self):
        svc = ManagedBotService()
        username, name = svc.suggest_username_and_name("Салон Красоты Лилия")
        assert username.endswith("bot")
        assert "liliya" in username or "salon" in username
        assert name == "Салон Красоты Лилия | Запись"
        assert len(username) <= 32

    def test_build_deep_link(self):
        svc = ManagedBotService()
        link = svc.build_managed_bot_deep_link(
            manager_bot_username="zapisflow_mgr_bot",
            suggested_username="anna_beauty_bot",
            suggested_name="Анна Бьюти",
        )
        assert link.startswith("https://t.me/newbot/zapisflow_mgr_bot/anna_beauty_bot?name=")
        assert "%D0%90%D0%BD%D0%BD%D0%B0" in link  # URL-encoded Cyrillic

    def test_generate_variants(self):
        svc = ManagedBotService()
        variants = svc.generate_username_variants("studio")
        assert len(variants) >= 3
        for v in variants:
            assert v.endswith("bot")
            assert len(v) >= 5

    def test_build_reply_button(self):
        svc = ManagedBotService()
        btn = svc.build_request_managed_bot_button(
            text="Создать бота",
            suggested_name="Мастер",
            suggested_username="master_bot",
            request_id=42,
        )
        assert btn.text == "Создать бота"
        assert btn.request_managed_bot.request_id == 42
        assert btn.request_managed_bot.suggested_username == "master_bot"


# ---------------------------------------------------------------------------
# Unit Tests: Keyboards
# ---------------------------------------------------------------------------

class TestManagedBotKeyboards:
    def test_choice_keyboard(self):
        kb = bot_connect_method_choice_keyboard(master_id=10)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert "mgr:bot:mg:prep:10" in callbacks
        assert "mgr:bot:token:start:10" in callbacks
        assert "mgr:master:10" in callbacks

    def test_prepare_keyboard(self):
        kb = managed_bot_prepare_keyboard(
            master_id=10,
            creation_url="https://t.me/newbot/mgr/bot?name=test",
            suggested_username="test_bot",
        )
        urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
        assert "https://t.me/newbot/mgr/bot?name=test" in urls
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert "mgr:bot:mg:reply:10" in callbacks
        assert "mgr:bot:mg:custom:10" in callbacks
        assert "mgr:bot:token:start:10" in callbacks

    def test_reply_keyboard(self):
        kb = managed_bot_reply_keyboard(
            suggested_name="Салон",
            suggested_username="salon_bot",
            request_id=1,
        )
        assert kb.resize_keyboard is True
        assert kb.keyboard[0][0].request_managed_bot is not None
        assert kb.keyboard[0][0].request_managed_bot.suggested_username == "salon_bot"
        assert kb.keyboard[1][0].text == "❌ Отмена"

    def test_rotate_keyboard(self):
        kb = managed_bot_rotate_keyboard(master_id=10)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert "mgr:bot:mg:rot_confirm:10" in callbacks
        assert "mgr:bot:token:start:10" in callbacks


# ---------------------------------------------------------------------------
# Integration Tests: BotProvisioningService (Managed Bot Flow)
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_provision_managed_bot_lifecycle(pg_session: AsyncSession):
    """Test full managed bot provisioning: encryption, DB record, and idempotency."""
    # 1. Setup Master and Owner
    user = User(telegram_id=999001, full_name="Master Owner", username="owner_test")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="Студия Элеганс",
        activity_type="manicure",
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.TRIAL,
    )
    pg_session.add(master)
    await pg_session.commit()

    # 2. Mock Telegram Gateway
    gateway = TelegramProvisioningGateway()
    gateway.set_webhook = AsyncMock(return_value=True)
    webhook_info_mock = MagicMock()
    gateway.get_webhook_info = AsyncMock(return_value=webhook_info_mock)
    gateway.set_my_commands = AsyncMock(return_value=True)
    gateway.set_chat_menu_button = AsyncMock(return_value=True)

    crypto = TokenCrypto()
    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)

    raw_token = "987654321:AAFakeManagedBotTokenSecretABC_xyz"
    bot_identity = BotIdentity(
        id=987654321,
        username="elegance_studio_bot",
        first_name="Студия Элеганс Бот",
    )

    # Make get_webhook_info return the expected URL so validation passes
    async def fake_get_webhook_info(t):
        info = MagicMock()
        # Find public_id
        res = await pg_session.scalar(select(BotInstance).where(BotInstance.telegram_bot_id == 987654321))
        info.url = f"{settings.webhook_base_url.rstrip('/')}/telegram/webhook/{res.public_id}"
        return info

    gateway.get_webhook_info.side_effect = fake_get_webhook_info

    # 3. Provision Managed Bot
    bot_instance = await service.provision_managed_bot(
        master_id=master.id,
        actor_user_id=user.id,
        bot_identity=bot_identity,
        token=raw_token,
        telegram_owner_user_id=user.telegram_id,
    )

    assert bot_instance is not None
    assert bot_instance.telegram_bot_id == 987654321
    assert bot_instance.telegram_username == "elegance_studio_bot"
    assert bot_instance.provisioning_source == "managed_bot"
    assert bot_instance.managed_by_platform is True
    assert bot_instance.telegram_owner_user_id == user.telegram_id
    assert bot_instance.status == BotInstanceStatus.SETUP_REQUIRED
    assert bot_instance.is_current is True

    # Verify token is encrypted (never plaintext in DB)
    assert bot_instance.encrypted_token != raw_token
    decrypted = crypto.decrypt(bot_instance.encrypted_token, associated_data=987654321)
    assert decrypted == raw_token

    # Verify network gateway calls
    gateway.set_webhook.assert_awaited_once()
    gateway.set_my_commands.assert_awaited_once()

    # 4. Test Idempotency: re-provisioning same bot for same master should return existing instance
    duplicate_call_instance = await service.provision_managed_bot(
        master_id=master.id,
        actor_user_id=user.id,
        bot_identity=bot_identity,
        token=raw_token,
        telegram_owner_user_id=user.telegram_id,
    )
    assert duplicate_call_instance.id == bot_instance.id

    # 5. Test Tenant Isolation: provisioning same bot for ANOTHER master must fail with DuplicateBotError
    other_user = User(telegram_id=999002, full_name="Other User")
    pg_session.add(other_user)
    await pg_session.flush()

    other_master = Master(
        owner_user_id=other_user.id,
        display_name="Чужой Салон",
        activity_type="haircut",
    )
    pg_session.add(other_master)
    await pg_session.commit()

    with pytest.raises(DuplicateBotError):
        await service.provision_managed_bot(
            master_id=other_master.id,
            actor_user_id=other_user.id,
            bot_identity=bot_identity,
            token=raw_token,
            telegram_owner_user_id=other_user.telegram_id,
        )

    # 6. Test Non-Owner Rejection
    with pytest.raises(AccessDeniedError):
        await service.provision_managed_bot(
            master_id=master.id,
            actor_user_id=other_user.id,
            bot_identity=bot_identity,
            token=raw_token,
        )


@requires_postgres
@pytest.mark.asyncio
async def test_rotate_managed_bot_token(pg_session: AsyncSession):
    """Test automatic token rotation for Telegram Managed Bot."""
    user = User(telegram_id=999003, full_name="Rotation Owner")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="Ротация Салон",
        activity_type="barbershop",
    )
    pg_session.add(master)
    await pg_session.flush()

    crypto = TokenCrypto()
    token_v1 = "111222333:TokenVersionOneFakeABC"
    enc_token = crypto.encrypt(token_v1, associated_data=111222333)

    bot_repo = BotInstanceRepository(pg_session)
    bot = await bot_repo.create_bot_instance(
        master_id=master.id,
        telegram_bot_id=111222333,
        telegram_username="rotate_test_bot",
        encrypted_token=enc_token,
        webhook_secret="secret_abc",
        status=BotInstanceStatus.SETUP_REQUIRED,
        token_version=1,
        is_current=True,
        provisioning_source="managed_bot",
        managed_by_platform=True,
        telegram_owner_user_id=user.telegram_id,
    )
    await pg_session.commit()

    # Mock gateway
    gateway = TelegramProvisioningGateway()
    token_v2 = "111222333:TokenVersionTwoRotatedNewXYZ"
    gateway.replace_managed_bot_token = AsyncMock(return_value=token_v2)
    gateway.validate_token = AsyncMock(return_value=BotIdentity(id=111222333, username="rotate_test_bot"))
    gateway.set_webhook = AsyncMock(return_value=True)

    async def fake_get_webhook_info(t):
        info = MagicMock()
        info.url = f"{settings.webhook_base_url.rstrip('/')}/telegram/webhook/{bot.public_id}"
        return info

    gateway.get_webhook_info.side_effect = fake_get_webhook_info

    service = BotProvisioningService(session=pg_session, gateway=gateway, crypto=crypto)

    with patch.object(settings, "manager_bot_token", "fake_mgr_token"):
        rotated_bot = await service.rotate_managed_bot_token(
            bot_instance_id=bot.id,
            actor_user_id=user.id,
        )

    assert rotated_bot.token_version == 2
    decrypted = crypto.decrypt(rotated_bot.encrypted_token, associated_data=111222333)
    assert decrypted == token_v2
    gateway.replace_managed_bot_token.assert_awaited_once_with(
        manager_token="fake_mgr_token",
        user_id=user.telegram_id,
    )


# ---------------------------------------------------------------------------
# Unit Tests with Mocks: BotProvisioningService Logic
# ---------------------------------------------------------------------------

TEST_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


class TestBotProvisioningMocked:
    @pytest.mark.asyncio
    async def test_provision_managed_bot_mocked(self):
        session = AsyncMock()
        gateway = AsyncMock(spec=TelegramProvisioningGateway)
        crypto = TokenCrypto(master_key=TEST_KEY)

        master = Master(id=1, owner_user_id=100, display_name="Test Studio")
        session.scalar.return_value = master

        bot_identity = BotIdentity(id=555666, username="test_studio_bot", first_name="Test Studio")
        
        service = BotProvisioningService(session=session, gateway=gateway, crypto=crypto)
        service.bot_repo = AsyncMock()
        service.bot_repo.get_by_telegram_bot_id.return_value = None
        service.bot_repo.get_current_for_master.return_value = None

        created_bot = BotInstance(
            id=10,
            master_id=1,
            telegram_bot_id=555666,
            telegram_username="test_studio_bot",
            telegram_first_name="Test Studio",
            status=BotInstanceStatus.PROVISIONING,
            public_id="pub_test_123",
            webhook_secret="sec_123",
            provisioning_source="managed_bot",
            managed_by_platform=True,
            telegram_owner_user_id=100,
        )
        service.bot_repo.create_bot_instance.return_value = created_bot

        webhook_info_mock = MagicMock()
        webhook_info_mock.url = f"{settings.webhook_base_url.rstrip('/')}/telegram/webhook/pub_test_123"
        gateway.get_webhook_info.return_value = webhook_info_mock
        gateway.set_webhook.return_value = True
        gateway.set_my_commands.return_value = True

        res = await service.provision_managed_bot(
            master_id=1,
            actor_user_id=100,
            bot_identity=bot_identity,
            token="555666:TestToken12345ABC",
            telegram_owner_user_id=100,
        )

        assert res.status == BotInstanceStatus.SETUP_REQUIRED
        assert res.managed_by_platform is True
        assert res.provisioning_source == "managed_bot"
        service.bot_repo.create_bot_instance.assert_awaited_once()
        gateway.set_webhook.assert_awaited_once()
        gateway.set_my_commands.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provision_managed_bot_forbidden_for_non_owner(self):
        session = AsyncMock()
        crypto = TokenCrypto(master_key=TEST_KEY)
        master = Master(id=1, owner_user_id=100, display_name="Test Studio")
        session.scalar.return_value = master

        service = BotProvisioningService(session=session, crypto=crypto)
        bot_identity = BotIdentity(id=555666, username="test_studio_bot", first_name="Test Studio")

        with pytest.raises(AccessDeniedError):
            await service.provision_managed_bot(
                master_id=1,
                actor_user_id=999,  # Wrong owner
                bot_identity=bot_identity,
                token="555666:TestToken",
            )

    @pytest.mark.asyncio
    async def test_rotate_non_managed_bot_rejected(self):
        session = AsyncMock()
        crypto = TokenCrypto(master_key=TEST_KEY)
        bot = BotInstance(id=5, master_id=1, telegram_bot_id=555, managed_by_platform=False)
        session.scalar.return_value = bot

        service = BotProvisioningService(session=session, crypto=crypto)
        with pytest.raises(AccessDeniedError) as exc_info:
            await service.rotate_managed_bot_token(bot_instance_id=5, actor_user_id=100)
        assert "не является управляемым" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Unit Tests for Manager Bot Handlers
# ---------------------------------------------------------------------------

class TestManagerBotHandlersMocked:
    @pytest.mark.asyncio
    async def test_cb_connect_bot_renders_choice_keyboard(self):
        from app.manager_bot.handlers import cb_connect_bot

        callback = AsyncMock()
        callback.data = "mgr:bot:connect:1"
        callback.from_user.id = 100
        callback.from_user.username = "owner"
        callback.from_user.first_name = "Owner"
        callback.from_user.last_name = None

        state = AsyncMock()
        session = AsyncMock()

        master = Master(id=1, owner_user_id=1, display_name="Студия")
        user = User(id=1, telegram_id=100, first_name="Owner")

        with patch("app.manager_bot.handlers._get_or_create_user", AsyncMock(return_value=user)), \
             patch("app.manager_bot.handlers.MasterRepository.get_by_id", AsyncMock(return_value=master)):
            await cb_connect_bot(callback, state, session)

        callback.message.edit_text.assert_awaited_once()
        args, kwargs = callback.message.edit_text.call_args
        assert "Выберите удобный способ подключения" in args[0]
        assert "bot_connect_method_choice_keyboard" in str(type(kwargs["reply_markup"])) or kwargs["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_cb_token_start_preserves_manual_onboarding(self):
        from app.manager_bot.handlers import cb_token_start
        from app.manager_bot.states import ConnectBotStates

        callback = AsyncMock()
        callback.data = "mgr:bot:token:start:1"
        callback.from_user.id = 100

        state = AsyncMock()
        session = AsyncMock()

        master = Master(id=1, owner_user_id=1, display_name="Студия")
        user = User(id=1, telegram_id=100, first_name="Owner")

        with patch("app.manager_bot.handlers._get_or_create_user", AsyncMock(return_value=user)), \
             patch("app.manager_bot.handlers.MasterRepository.get_by_id", AsyncMock(return_value=master)):
            await cb_token_start(callback, state, session)

        state.set_state.assert_awaited_once_with(ConnectBotStates.waiting_for_token)
        state.update_data.assert_awaited_once_with(master_id=1)
        callback.message.edit_text.assert_awaited_once()
        assert "@BotFather" in callback.message.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cb_managed_bot_prepare_renders_deep_link(self):
        from app.manager_bot.handlers import cb_managed_bot_prepare
        from app.manager_bot.states import ManagedBotStates

        callback = AsyncMock()
        callback.data = "mgr:bot:mg:prep:1"
        callback.from_user.id = 100
        callback.bot.get_me = AsyncMock()
        callback.bot.get_me.return_value.username = "zapisflow_mgr_bot"

        state = AsyncMock()
        session = AsyncMock()

        master = Master(id=1, owner_user_id=1, display_name="Студия")
        user = User(id=1, telegram_id=100, first_name="Owner")

        with patch("app.manager_bot.handlers._get_or_create_user", AsyncMock(return_value=user)), \
             patch("app.manager_bot.handlers.MasterRepository.get_by_id", AsyncMock(return_value=master)):
            await cb_managed_bot_prepare(callback, state, session)

        state.set_state.assert_awaited_once_with(ManagedBotStates.waiting_for_creation)
        callback.message.edit_text.assert_awaited_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Создание нового бота для записи клиентов" in text
        assert "Студия" in text


