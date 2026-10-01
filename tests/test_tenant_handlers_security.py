"""Regression tests for trusted customer-bot tenancy and tenant CRM access."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.admin.clients_mgmt import (
    cb_admin_client_add_note_prompt,
    cb_admin_client_detail,
    format_client_crm_card,
    msg_admin_client_save_note,
)
from app.bot.handlers.client.services import cb_service_view
from app.bot.middlewares.tenant_context import TenantContextMiddleware
from app.bot.middlewares.user_context import UserContextMiddleware
from app.config.settings import settings
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterClient,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.service import DepositType, Service
from app.database.models.user import User


async def _tenant_pair(session: AsyncSession):
    owner = User(telegram_id=995001, first_name="Owner")
    client_a = User(telegram_id=995002, first_name="Client A", phone="+70000000001")
    client_b = User(telegram_id=995003, first_name="Client B", phone="+70000000002")
    session.add_all([owner, client_a, client_b])
    await session.flush()
    master_a = Master(
        owner_user_id=owner.id,
        display_name="Tenant A",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
    )
    master_b = Master(
        owner_user_id=owner.id,
        display_name="Tenant B",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
    )
    session.add_all([master_a, master_b])
    await session.flush()
    session.add_all([
        MasterClient(master_id=master_a.id, user_id=client_a.id, notes="Only A"),
        MasterClient(master_id=master_b.id, user_id=client_b.id, notes="Only B"),
    ])
    await session.flush()
    return master_a, master_b, client_a, client_b


def _callback():
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_webhook_tenant_context_requires_bot_instance_and_ignores_legacy_default(
    pg_session: AsyncSession,
) -> None:
    master_a, master_b, _, _ = await _tenant_pair(pg_session)
    bot_a = BotInstance(master_id=master_a.id, telegram_bot_id=995101, status=BotInstanceStatus.ACTIVE)
    bot_b = BotInstance(master_id=master_b.id, telegram_bot_id=995102, status=BotInstanceStatus.ACTIVE)
    pg_session.add_all([bot_a, bot_b])
    await pg_session.flush()
    middleware = TenantContextMiddleware()
    handler = AsyncMock(return_value="ok")

    with patch.object(settings, "app_mode", "webhook"):
        for bot_instance, expected in [(bot_a, master_a.id), (bot_b, master_b.id)]:
            data = {"session": pg_session, "bot_instance": bot_instance, "master": master_b if bot_instance is bot_a else master_a}
            assert await middleware(handler, MagicMock(), data) == "ok"
            assert data["master_id"] == expected
            assert data["master"].id == expected

        handler.reset_mock()
        for data in (
            {"session": pg_session, "master_id": 1},
            {"session": pg_session, "master_id": master_a.id},
            {"session": pg_session, "master_id": master_a.id, "bot_instance": bot_b},
        ):
            assert await middleware(handler, MagicMock(), data) is None
        handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_customer_service_callback_cannot_read_other_tenant(
    pg_session: AsyncSession,
) -> None:
    master_a, master_b, _, _ = await _tenant_pair(pg_session)
    service_b = Service(
        master_id=master_b.id,
        title="Secret B",
        price=100,
        duration_min=60,
        buffer_min=0,
        deposit_type=DepositType.FIXED,
        deposit_value=0,
        is_active=True,
    )
    pg_session.add(service_b)
    await pg_session.flush()
    callback = _callback()
    callback_data = SimpleNamespace(service_id=service_b.id)

    await cb_service_view(callback, callback_data, pg_session, master_a.id)
    callback.answer.assert_awaited_once_with("Услуга недоступна", show_alert=True)
    callback.message.edit_text.assert_not_awaited()

    callback = _callback()
    await cb_service_view(callback, callback_data, pg_session, master_b.id)
    assert "Secret B" in callback.message.edit_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_crm_forged_user_id_cannot_open_or_edit_foreign_client(
    pg_session: AsyncSession,
) -> None:
    master_a, _, client_a, client_b = await _tenant_pair(pg_session)
    callback = _callback()
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())
    forged = SimpleNamespace(user_id=client_b.id)

    await cb_admin_client_detail(callback, SimpleNamespace(user_id=client_a.id), state, pg_session, master_a.id)
    assert "Client A" in callback.message.edit_text.await_args.kwargs["text"]
    callback = _callback()

    await cb_admin_client_detail(callback, forged, state, pg_session, master_a.id)
    callback.answer.assert_awaited_with("Клиент не найден", show_alert=True)
    callback.message.edit_text.assert_not_awaited()

    callback = _callback()
    await cb_admin_client_add_note_prompt(callback, forged, state, pg_session, master_a.id)
    callback.answer.assert_awaited_with("Клиент не найден", show_alert=True)
    state.set_state.assert_not_awaited()

    message = SimpleNamespace(text="forged note", answer=AsyncMock())
    state.get_data = AsyncMock(return_value={"user_id": client_b.id})
    await msg_admin_client_save_note(message, state, pg_session, master_a.id)
    message.answer.assert_awaited_with("Клиент не найден.")
    from app.repositories.master_client_repository import MasterClientRepository
    repo = MasterClientRepository(pg_session)
    assert (await repo.get_client(master_a.id, client_b.id)) is None
    with pytest.raises(LookupError):
        await repo.update_notes(master_a.id, client_b.id, "forged note")
    assert (await repo.get_client(master_a.id, client_a.id)).notes == "Only A"


@pytest.mark.asyncio
async def test_crm_notes_are_tenant_scoped_even_for_shared_user(
    pg_session: AsyncSession,
) -> None:
    master_a, master_b, client_a, _ = await _tenant_pair(pg_session)
    pg_session.add(MasterClient(master_id=master_b.id, user_id=client_a.id, notes="Allergy B"))
    client_a.admin_notes = "Legacy global note"
    await pg_session.flush()
    card_a = await format_client_crm_card(client_a, pg_session, "UTC", master_a.id)
    card_b = await format_client_crm_card(client_a, pg_session, "UTC", master_b.id)
    assert "Only A" in card_a and "Allergy B" not in card_a
    assert "Allergy B" in card_b and "Only A" not in card_b
    assert "Legacy global note" not in card_a + card_b

    state = SimpleNamespace(get_data=AsyncMock(return_value={"user_id": client_a.id}), clear=AsyncMock())
    message = SimpleNamespace(text="Updated A", answer=AsyncMock())
    await msg_admin_client_save_note(message, state, pg_session, master_a.id)
    assert "Updated A" in message.answer.await_args.kwargs["text"]
    card_b = await format_client_crm_card(client_a, pg_session, "UTC", master_b.id)
    assert "Updated A" not in card_b
    assert client_a.admin_notes == "Legacy global note"


@pytest.mark.asyncio
async def test_user_context_registers_only_verified_tenant_client(
    pg_session: AsyncSession,
) -> None:
    master_a, master_b, _, _ = await _tenant_pair(pg_session)
    bot_a = SimpleNamespace(master_id=master_a.id)
    middleware = UserContextMiddleware()
    handler = AsyncMock(return_value="ok")
    tg_user = TelegramUser(id=995004, is_bot=False, first_name="Visitor")
    data = {"session": pg_session, "event_from_user": tg_user, "master_id": master_a.id, "bot_instance": bot_a}
    with patch.object(settings, "app_mode", "webhook"):
        assert await middleware(handler, MagicMock(), data) == "ok"
        assert data["db_user"] is not None
        from app.repositories.master_client_repository import MasterClientRepository
        repo = MasterClientRepository(pg_session)
        assert await repo.get_client(master_a.id, data["db_user"].id) is not None
        assert await repo.get_client(master_b.id, data["db_user"].id) is None

        handler.reset_mock()
        bad = {"session": pg_session, "event_from_user": tg_user, "master_id": master_b.id, "bot_instance": bot_a}
        assert await middleware(handler, MagicMock(), bad) is None
        handler.assert_not_awaited()
