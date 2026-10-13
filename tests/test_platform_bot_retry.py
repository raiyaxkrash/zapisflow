"""Retry-safe Platform Admin BotInstance state transitions on PostgreSQL."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Update
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.config.settings import settings
from app.core.token_crypto import TokenCrypto
from app.database.models.audit import AuditLog
from app.database.models.master import BotInstance, BotInstanceStatus, Master
from app.database.models.processed_update import ProcessedWebhookUpdate
from app.database.models.user import User
from app.manager_bot.handlers import _answer_bot_callback, cb_admin_bot_set_state, cb_admin_bot_toggle
from app.manager_bot.keyboards import admin_bot_detail_keyboard
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.exceptions import AccessDeniedError
from app.services.platform_admin_service import PlatformAdminService
from app.services.telegram_provisioning_gateway import BotIdentity, TelegramProvisioningGateway
from tests.conftest import requires_postgres


KEY = "0123456789abcdef" * 4


class Gateway:
    def __init__(self):
        self.set_calls = 0
        self.delete_calls = 0
        self.url = ""

    async def validate_token(self, token):
        return BotIdentity(id=int(token.split(":")[0]), username="safe_bot", first_name="Safe")

    async def set_webhook(self, token, url, secret_token, **kwargs):
        self.set_calls += 1
        self.url = url

    async def get_webhook_info(self, token):
        return SimpleNamespace(url=self.url)

    async def delete_webhook(self, token):
        self.delete_calls += 1


async def seed(session: AsyncSession):
    unique = uuid4().int % 1_000_000_000
    owner = User(telegram_id=7_000_000_000 + unique, first_name="Owner")
    other = User(telegram_id=8_000_000_000 + unique, first_name="Other")
    session.add_all([owner, other])
    await session.flush()
    master = Master(owner_user_id=owner.id, display_name=f"Bot retry {unique}")
    session.add(master)
    await session.flush()
    telegram_bot_id = 9_000_000_000 + unique
    bot = BotInstance(
        master_id=master.id,
        telegram_bot_id=telegram_bot_id,
        encrypted_token=TokenCrypto(KEY).encrypt(f"{telegram_bot_id}:TEST_TOKEN", associated_data=telegram_bot_id),
        webhook_secret="safe_test_webhook_secret",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    session.add(bot)
    await session.flush()
    return owner, other, master, bot


@requires_postgres
@pytest.mark.asyncio
async def test_explicit_state_is_idempotent_and_owner_scoped(pg_session: AsyncSession):
    owner, other, _, bot = await seed(pg_session)
    gateway = Gateway()
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=TokenCrypto(KEY))
    with pytest.raises(AccessDeniedError):
        await service.disable_bot(bot.id, other.id, commit=False)
    await service.disable_bot(bot.id, owner.id, commit=False)
    await service.disable_bot(bot.id, owner.id, commit=False)
    assert bot.status == BotInstanceStatus.DISABLED
    assert gateway.delete_calls == 1

    await service.enable_bot(bot.id, owner.id, commit=False)
    await service.enable_bot(bot.id, owner.id, commit=False)
    assert bot.status == BotInstanceStatus.SETUP_REQUIRED
    assert gateway.set_calls == 1


@requires_postgres
@pytest.mark.asyncio
async def test_details_after_mutation_are_scalar_loaded(pg_session: AsyncSession):
    owner, _, _, bot = await seed(pg_session)
    gateway = Gateway()
    await BotProvisioningService(pg_session, gateway=gateway, crypto=TokenCrypto(KEY)).disable_bot(
        bot.id, owner.id, commit=False
    )
    await pg_session.flush()
    bot_id = bot.id
    pg_session.expire(bot)
    details = await PlatformAdminService(pg_session).get_bot_details(bot_id)
    assert details["status"] == "DISABLED"
    assert details["updated_at"] is not None
    rows, _, _ = await PlatformAdminService(pg_session).list_bots()
    assert any(row["id"] == bot_id and row["status"] == "DISABLED" for row in rows)


@requires_postgres
@pytest.mark.asyncio
async def test_delete_uses_caller_transaction(pg_session: AsyncSession):
    owner, _, master, bot = await seed(pg_session)
    gateway = Gateway()
    savepoint = await pg_session.begin_nested()
    await BotProvisioningService(pg_session, gateway=gateway, crypto=TokenCrypto(KEY)).delete_bot(
        bot.id, owner.id, commit=False
    )
    assert bot.status == BotInstanceStatus.DISABLED and bot.is_current is False
    await savepoint.rollback()
    await pg_session.refresh(bot)
    await pg_session.refresh(master)
    assert bot.status == BotInstanceStatus.ACTIVE and bot.is_current is True


def test_new_buttons_use_desired_state_and_legacy_toggle_is_not_generated():
    active = admin_bot_detail_keyboard(7, True)
    disabled = admin_bot_detail_keyboard(7, False)
    assert active.inline_keyboard[1][0].callback_data == "mgr:admin:bot:disable:7"
    assert disabled.inline_keyboard[1][0].callback_data == "mgr:admin:bot:enable:7"


@pytest.mark.asyncio
async def test_legacy_toggle_is_read_only():
    callback = SimpleNamespace(answer=AsyncMock())
    with patch("app.manager_bot.handlers._ensure_platform_admin", new=AsyncMock(return_value=(True, SimpleNamespace(id=1)))):
        await cb_admin_bot_toggle(callback, AsyncMock())
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_platform_admin_cannot_change_bot():
    callback = SimpleNamespace(data="mgr:admin:bot:disable:123", answer=AsyncMock())
    with patch("app.manager_bot.handlers._ensure_platform_admin", new=AsyncMock(return_value=(False, None))):
        with patch("app.manager_bot.handlers.BotProvisioningService") as service_class:
            await cb_admin_bot_set_state(callback, AsyncMock())
    service_class.assert_not_called()


@pytest.mark.asyncio
async def test_callback_answer_only_ignores_expired_query():
    callback = SimpleNamespace(answer=AsyncMock(side_effect=TelegramBadRequest(
        method=MagicMock(), message="Bad Request: chat not found"
    )))
    with pytest.raises(TelegramBadRequest, match="chat not found"):
        await _answer_bot_callback(callback)


@requires_postgres
@pytest.mark.asyncio
async def test_crash_replay_and_completed_ledger(pg_session: AsyncSession, monkeypatch):
    import app.bot.middlewares.db_session as middleware_module

    owner, _, _, bot = await seed(pg_session)
    gateway = Gateway()
    monkeypatch.setattr(settings, "bot_token_encryption_key", KEY)
    monkeypatch.setattr(
        middleware_module, "async_session_factory",
        async_sessionmaker(pg_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"),
    )
    with patch.object(TelegramProvisioningGateway, "delete_webhook", new=AsyncMock(side_effect=gateway.delete_webhook)):
        callback = SimpleNamespace(
            data=f"mgr:admin:bot:disable:{bot.id}",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        update = Update(update_id=uuid4().int % 1_000_000_000)
        middleware = DbSessionMiddleware()
        calls = 0

        async def handler(_event, data):
            nonlocal calls
            calls += 1
            await cb_admin_bot_set_state(callback, data["session"])

        async def crash(_event, data):
            await handler(_event, data)
            raise RuntimeError("crash before completed ledger")

        with patch("app.manager_bot.handlers._ensure_platform_admin", new=AsyncMock(return_value=(True, owner))):
            with pytest.raises(RuntimeError, match="crash before"):
                await middleware(crash, update, {"webhook_update_scope": "manager"})
            await pg_session.refresh(bot)
            assert bot.status == BotInstanceStatus.ACTIVE
            assert await pg_session.get(ProcessedWebhookUpdate, ("manager", update.update_id)) is None
            await middleware(handler, update, {"webhook_update_scope": "manager"})
            await middleware(handler, update, {"webhook_update_scope": "manager"})
        await pg_session.refresh(bot)
        assert bot.status == BotInstanceStatus.DISABLED
        assert calls == 2
        assert gateway.delete_calls == 2  # External Telegram calls may repeat after rollback.
        assert await pg_session.get(ProcessedWebhookUpdate, ("manager", update.update_id)) is not None


@requires_postgres
@pytest.mark.asyncio
async def test_expired_callback_answer_does_not_rollback(pg_session: AsyncSession, monkeypatch):
    import app.bot.middlewares.db_session as middleware_module

    owner, _, _, bot = await seed(pg_session)
    gateway = Gateway()
    monkeypatch.setattr(settings, "bot_token_encryption_key", KEY)
    monkeypatch.setattr(
        middleware_module, "async_session_factory",
        async_sessionmaker(pg_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"),
    )
    callback = SimpleNamespace(
        data=f"mgr:admin:bot:disable:{bot.id}",
        answer=AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: query is too old")),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )
    update = Update(update_id=uuid4().int % 1_000_000_000)
    with patch("app.manager_bot.handlers._ensure_platform_admin", new=AsyncMock(return_value=(True, owner))):
        with patch.object(TelegramProvisioningGateway, "delete_webhook", new=AsyncMock(side_effect=gateway.delete_webhook)):
            async def handler(_event, data):
                await cb_admin_bot_set_state(callback, data["session"])
            await DbSessionMiddleware()(handler, update, {"webhook_update_scope": "manager"})
    await pg_session.refresh(bot)
    assert bot.status == BotInstanceStatus.DISABLED
    assert await pg_session.get(ProcessedWebhookUpdate, ("manager", update.update_id)) is not None


@requires_postgres
@pytest.mark.asyncio
async def test_two_parallel_manager_updates_disable_once(pg_engine, monkeypatch):
    import app.bot.middlewares.db_session as middleware_module

    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        owner, other, master, bot = await seed(session)
        owner_id, other_id, master_id, bot_id = owner.id, other.id, master.id, bot.id
    gateway = Gateway()
    monkeypatch.setattr(settings, "bot_token_encryption_key", KEY)
    monkeypatch.setattr(middleware_module, "async_session_factory", sessions)
    callback = SimpleNamespace(
        data=f"mgr:admin:bot:disable:{bot_id}",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )
    update = Update(update_id=uuid4().int % 1_000_000_000)
    calls = 0

    async def handler(_event, data):
        nonlocal calls
        calls += 1
        await cb_admin_bot_set_state(callback, data["session"])

    async def worker():
        await DbSessionMiddleware()(handler, update, {"webhook_update_scope": "manager"})

    try:
        with patch("app.manager_bot.handlers._ensure_platform_admin", new=AsyncMock(return_value=(True, SimpleNamespace(id=owner_id)))):
            with patch.object(TelegramProvisioningGateway, "delete_webhook", new=AsyncMock(side_effect=gateway.delete_webhook)):
                await asyncio.gather(worker(), worker())
        async with sessions() as session:
            status = await session.scalar(select(BotInstance.status).where(BotInstance.id == bot_id))
        assert status == BotInstanceStatus.DISABLED
        assert calls == 1
        assert gateway.delete_calls == 1
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(ProcessedWebhookUpdate).where(
                ProcessedWebhookUpdate.scope == "manager", ProcessedWebhookUpdate.update_id == update.update_id
            ))
            await session.execute(delete(AuditLog).where(AuditLog.master_id == master_id))
            await session.execute(delete(BotInstance).where(BotInstance.id == bot_id))
            await session.execute(delete(Master).where(Master.id == master_id))
            await session.execute(delete(User).where(User.id.in_([owner_id, other_id])))
