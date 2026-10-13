import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.config.settings import Settings, settings
from app.config.url_validation import webhook_origin
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterAdmin, MasterAdminRole
from app.database.models.user import User
from app.manager_bot.keyboards import project_card_keyboard
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.exceptions import AccessDeniedError, ProvisioningWebhookError, TelegramGatewayError
from app.services.telegram_provisioning_gateway import BotIdentity, TelegramProvisioningGateway


@pytest.mark.parametrize("url", ["https://192.0.2.1", "https://[2001:db8::1]", "http://api.example.test", "https://user:pass@api.example.test", "https://api.example.test/path", "https://api.example.test?x=1", "https://api.example.test#x", "https://127.1", "https://localhost"])
def test_production_rejects_invalid_webhook_origin(url):
    config = Settings(_env_file=None, APP_ENV="production", APP_MODE="webhook", PAYMENT_PROVIDER="disabled",
                      WEBHOOK_BASE_URL=url, MANAGER_BOT_TOKEN="123456789:AAFakeOnly",
                      MANAGER_WEBHOOK_SECRET="s" * 32, REDIS_PASSWORD="test_only",
                      BOT_TOKEN_ENCRYPTION_KEY="01" * 32)
    with pytest.raises(ValueError, match="WEBHOOK_BASE_URL"):
        config.validate_production_configuration()


def test_hostname_origin_and_development_localhost():
    assert webhook_origin("https://api.example.test/", production=True) == "https://api.example.test"
    assert webhook_origin("http://127.0.0.1:8000", production=False) == "http://127.0.0.1:8000"


@pytest.mark.asyncio
async def test_production_gateway_keeps_hostname_but_refreshes_dns_delivery_ip(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    resolver = AsyncMock(return_value=[(2, 1, 6, "", ("8.8.4.4", 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    bot = SimpleNamespace(set_webhook=AsyncMock(return_value=True), session=SimpleNamespace(close=AsyncMock()),
                          get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="https://api.example.test/telegram/webhook/public", ip_address="8.8.4.4")))
    gateway = TelegramProvisioningGateway()
    monkeypatch.setattr(gateway, "_create_temp_bot", lambda token: bot)
    assert await gateway.set_webhook("test-token", bot.get_webhook_info.return_value.url, secret_token="test-secret")
    call = bot.set_webhook.call_args.kwargs
    assert call["url"].startswith("https://api.example.test/")
    assert call["ip_address"] == "8.8.4.4"
    assert call["drop_pending_updates"] is False
    assert {"message", "callback_query"} <= set(call["allowed_updates"])
    bot.session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["dns", "private", "mismatch"])
async def test_production_gateway_fails_closed_on_dns_or_verification_error(monkeypatch, reason):
    monkeypatch.setattr(settings, "app_env", "production")
    resolver = AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1" if reason == "private" else "8.8.4.4", 443))])
    if reason == "dns":
        resolver.side_effect = OSError("DNS unavailable")
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    bot = SimpleNamespace(set_webhook=AsyncMock(return_value=True), session=SimpleNamespace(close=AsyncMock()),
                          get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="https://wrong.example.test", ip_address="8.8.4.4")))
    gateway = TelegramProvisioningGateway()
    monkeypatch.setattr(gateway, "_create_temp_bot", lambda token: bot)
    with pytest.raises(TelegramGatewayError):
        await gateway.set_webhook("test-token", "https://api.example.test/telegram/webhook/public")
    if reason != "mismatch":
        bot.set_webhook.assert_not_awaited()
    bot.session.close.assert_awaited_once()


@pytest.fixture
async def connected_bot(pg_session, monkeypatch):
    monkeypatch.setattr(settings, "webhook_base_url", "https://current.example.test")
    monkeypatch.setattr(settings, "mini_app_base_url", "")
    owner = User(telegram_id=88001001, first_name="Owner")
    pg_session.add(owner)
    await pg_session.flush()
    master = Master(owner_user_id=owner.id, display_name="Test")
    pg_session.add(master)
    await pg_session.flush()
    crypto = TokenCrypto("01" * 32)
    instance = BotInstance(master_id=master.id, telegram_bot_id=88001002, is_current=True,
                           encrypted_token=crypto.encrypt("88001002:FakeTestToken", associated_data=88001002),
                           webhook_secret="s" * 32, token_version=3, status=BotInstanceStatus.ACTIVE)
    pg_session.add(instance)
    await pg_session.flush()
    gateway = SimpleNamespace(set_webhook=AsyncMock(return_value=True), get_webhook_info=AsyncMock(
        return_value=SimpleNamespace(url=f"https://current.example.test/telegram/webhook/{instance.public_id}")))
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)
    return owner, master, instance, service, gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED])
async def test_resync_preserves_instance_token_version_status_and_is_idempotent(pg_session, connected_bot, status):
    owner, master, instance, service, gateway = connected_bot
    instance.status = status
    snapshot = (instance.id, instance.encrypted_token, instance.token_version, instance.webhook_secret)
    count = await pg_session.scalar(select(func.count()).select_from(BotInstance))
    for _ in range(2):
        result = await service.resync_webhook(instance.id, owner.id)
        assert result.status == status
        assert (result.id, result.encrypted_token, result.token_version, result.webhook_secret) == snapshot
    assert await pg_session.scalar(select(func.count()).select_from(BotInstance)) == count
    assert gateway.set_webhook.await_count == 2
    assert gateway.set_webhook.call_args.kwargs["secret_token"] == snapshot[-1]
    assert gateway.set_webhook.call_args.kwargs["drop_pending_updates"] is False
    assert any(button.callback_data == f"mgr:bot:resync:{instance.id}" for row in project_card_keyboard(master, instance).inline_keyboard for button in row)


@pytest.mark.asyncio
async def test_resync_mismatch_preserves_active_state(connected_bot):
    owner, _, instance, service, gateway = connected_bot
    gateway.get_webhook_info.return_value = SimpleNamespace(url="https://obsolete.example.test")
    with pytest.raises(ProvisioningWebhookError, match="несовпадающий"):
        await service.resync_webhook(instance.id, owner.id)
    assert instance.status == BotInstanceStatus.ACTIVE
    assert instance.token_version == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MasterAdminRole.ADMIN, MasterAdminRole.STAFF, None])
async def test_resync_authorizes_tenant_admin_only(pg_session, connected_bot, role):
    _, master, instance, service, gateway = connected_bot
    actor = User(telegram_id=88001003, first_name="Other")
    pg_session.add(actor)
    await pg_session.flush()
    if role:
        pg_session.add(MasterAdmin(master_id=master.id, user_id=actor.id, role=role, is_active=True))
        await pg_session.flush()
    if role == MasterAdminRole.ADMIN:
        await service.resync_webhook(instance.id, actor.id)
    else:
        with pytest.raises(AccessDeniedError):
            await service.resync_webhook(instance.id, actor.id)
        gateway.set_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_healthy_bot_refreshes_current_url_without_status_downgrade(connected_bot):
    owner, _, instance, service, gateway = connected_bot
    await service.retry_provisioning(instance.id, owner.id, commit=False)
    gateway.set_webhook.assert_awaited_once()
    assert gateway.set_webhook.call_args.kwargs["url"] == f"https://current.example.test/telegram/webhook/{instance.public_id}"
    assert instance.status == BotInstanceStatus.ACTIVE


def test_provisioning_source_has_no_literal_ip_webhook():
    source = Path("app/services/bot_provisioning_service.py").read_text(encoding="utf-8")
    assert "185.221.23.193" not in source and "46.38.156.178" not in source
    assert 'f"{base_url}/telegram/webhook/{bot_instance.public_id}"' in source


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["provision", "managed", "retry", "rotate", "enable"])
async def test_all_lifecycle_paths_install_current_hostname_url(pg_session, connected_bot, action):
    owner, master, instance, service, gateway = connected_bot
    calls = []
    async def install(**kwargs):
        calls.append(kwargs)
        gateway.get_webhook_info.return_value = SimpleNamespace(url=kwargs["url"])
        return True
    gateway.set_webhook.side_effect = install
    gateway.validate_token = AsyncMock(return_value=BotIdentity(instance.telegram_bot_id, "test_bot", "Test"))
    gateway.set_my_commands = AsyncMock(return_value=True)
    gateway.set_chat_menu_button = AsyncMock(return_value=True)
    if action in ("provision", "managed"):
        other_master = Master(owner_user_id=owner.id, display_name="Other own project")
        pg_session.add(other_master)
        await pg_session.flush()
        identity = BotIdentity(88001004, "new_bot", "Test")
        if action == "managed":
            result = await service.provision_managed_bot(other_master.id, owner.id, identity, "88001004:FakeTestToken", owner.telegram_id)
        else:
            result = await service.provision_bot(other_master.id, owner.id, "88001004:FakeTestToken", identity)
    elif action == "retry":
        instance.status = BotInstanceStatus.ERROR
        result = await service.retry_provisioning(instance.id, owner.id, commit=False)
    elif action == "rotate":
        result = await service.rotate_token(instance.id, owner.id, "88001002:NewFakeTestToken")
    else:
        instance.status = BotInstanceStatus.DISABLED
        result = await service.enable_bot(instance.id, owner.id, commit=False)
    if action in ("provision", "managed"):
        from aiogram.types import MenuButtonCommands
        assert isinstance(gateway.set_chat_menu_button.call_args.kwargs["menu_button"], MenuButtonCommands)
    assert calls[-1]["url"] == f"https://current.example.test/telegram/webhook/{result.public_id}"
    assert calls[-1]["secret_token"] == result.webhook_secret
