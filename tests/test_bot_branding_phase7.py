"""Real tenant settings and Telegram transport doubles, without production bots."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import test_miniapp as base
from aiogram.filters import CommandObject
from aiogram.types import MenuButtonCommands, MenuButtonWebApp

from app.bot.handlers.client.start import cmd_start
from app.bot.handlers.client.presentation import project_menu
from app.bot.handlers.client.about import (
    cb_contact_master,
    cb_client_reviews,
    cb_about_master,
)
from app.bot.handlers.client.portfolio import cb_portfolio_categories
from app.bot.handlers.client.booking import cb_cancel_policy
from app.services.branding import BrandingInput, save_branding

system = base.system
miniapp_database_url = base.miniapp_database_url


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["manual_token", "managed"])
async def test_start_custom_brand_escaping_and_tenant_isolation(system, source):
    async with system.factory() as session:
        instance = await session.get(base.BotInstance, system.bots[0].id)
        instance.provisioning_source = source
        instance.managed_by_platform = source == "managed"
        await save_branding(
            session,
            instance.master_id,
            BrandingInput(
                brand_name="Anna & friends",
                welcome_text="Добро пожаловать & до встречи",
                booking_cta_label="Выбрать время",
                show_portfolio=False,
                show_contacts=False,
                show_reviews=False,
            ),
        )
        message, state = AsyncMock(), AsyncMock()
        await cmd_start(
            message,
            state,
            SimpleNamespace(first_name="<Client>"),
            False,
            bot_instance=instance,
            session=session,
        )
        output = message.answer.call_args.kwargs
        assert (
            "Anna &amp; friends" in output["text"]
            and "&lt;Client&gt;" in output["text"]
        )
        assert "Добро пожаловать &amp; до встречи" in output["text"]
        buttons = [b for row in output["reply_markup"].inline_keyboard for b in row]
        assert [b.callback_data for b in buttons] == [
            "menu:book",
            "menu:my_bookings",
            "menu:services",
            "menu:about",
        ]
        assert buttons[0].web_app is None and buttons[0].url is None
        other = await project_menu(session, system.masters[1].id)
        assert other.inline_keyboard[0][0].text == "📅 Записаться"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler", [cb_contact_master, cb_client_reviews, cb_portfolio_categories]
)
async def test_old_hidden_section_callbacks_answer_alert(system, handler):
    async with system.factory() as session:
        master_id = system.masters[0].id
        await save_branding(
            session,
            master_id,
            BrandingInput(
                show_portfolio=False, show_contacts=False, show_reviews=False
            ),
        )
        callback = AsyncMock()
        kwargs = {"callback": callback, "session": session, "master_id": master_id}
        if handler == cb_portfolio_categories:
            kwargs["state"] = AsyncMock()
        await handler(**kwargs)
        callback.answer.assert_awaited_once_with(
            "Этот раздел сейчас недоступен", show_alert=True
        )
        callback.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_policy_returns_current_brand_menu(system):
    async with system.factory() as session:
        master_id = system.masters[0].id
        await save_branding(
            session,
            master_id,
            BrandingInput(booking_cta_label="Мой визит", show_portfolio=False),
        )
        callback, state = AsyncMock(), AsyncMock()
        await cb_cancel_policy(callback, state, False, session, master_id)
        menu = callback.message.edit_text.call_args.kwargs["reply_markup"]
        assert menu.inline_keyboard[0][0].text == "📅 Мой визит"
        assert "menu:portfolio" not in [
            b.callback_data for row in menu.inline_keyboard for b in row
        ]
        state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_about_uses_real_brand_without_invented_experience(system):
    async with system.factory() as session:
        await save_branding(
            session,
            system.masters[0].id,
            BrandingInput(
                brand_name="Barber House",
                description="Стрижки & уход",
                show_portfolio=False,
            ),
        )
        callback = AsyncMock()
        callback.message.photo = []
        await cb_about_master(callback, session, system.masters[0].id)
        output = callback.message.edit_text.call_args.kwargs
        assert (
            "Barber House" in output["text"] and "Стрижки &amp; уход" in output["text"]
        )
        assert "Анастасия" not in output["text"] and "5+" not in output["text"]
        assert "menu:portfolio" not in [
            b.callback_data
            for row in output["reply_markup"].inline_keyboard
            for b in row
        ]


@pytest.mark.asyncio
async def test_start_admin_deep_link_preserved():
    message, state, session = AsyncMock(), AsyncMock(), AsyncMock()
    user = SimpleNamespace(first_name="Owner")
    with patch(
        "app.bot.handlers.admin.dashboard.cmd_admin_dashboard", new_callable=AsyncMock
    ) as dashboard:
        await cmd_start(
            message,
            state,
            user,
            True,
            command=CommandObject(command="start", args="admin"),
            session=session,
        )
        dashboard.assert_awaited_once_with(message, state, user, session)
        message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_sync_commands_and_system_menu(system, monkeypatch):
    enabled = True
    from app.config.settings import settings

    monkeypatch.setattr(settings, "mini_app_base_url", "https://app.example.test")
    client = await base.login(
        system, 11001, raw=base.signed(11001, query_id=str(uuid.uuid4()))
    )
    async with system.factory() as session:
        row = await session.get(base.BotInstance, system.bots[0].id)
        row.mini_app_enabled = enabled
        await session.commit()
    bot = AsyncMock()
    system.registry.get_by_instance_id = AsyncMock(return_value=bot)
    response = await base.post(client, "/master/branding/sync-telegram", {})
    assert response.json()["ok"]
    assert [c.command for c in bot.set_my_commands.call_args.kwargs["commands"]] == [
        "start",
        "cancel",
    ]
    menu = bot.set_chat_menu_button.call_args.kwargs["menu_button"]
    assert isinstance(menu, MenuButtonWebApp if enabled else MenuButtonCommands)
    if enabled:
        assert menu.web_app.url.endswith("/b/" + str(system.bots[0].public_id))


@pytest.mark.asyncio
async def test_default_and_setup_welcome_preserve_response(system):
    async with system.factory() as session:
        row = await session.get(base.BotInstance, system.bots[0].id)
        message, state = AsyncMock(), AsyncMock()
        await cmd_start(
            message,
            state,
            SimpleNamespace(first_name="Client"),
            False,
            bot_instance=row,
            session=session,
        )
        assert "Здесь можно записаться" in message.answer.call_args.kwargs["text"]
        row.status = base.BotInstanceStatus.SETUP_REQUIRED
        message.reset_mock()
        await cmd_start(
            message,
            state,
            SimpleNamespace(first_name="Client"),
            False,
            bot_instance=row,
            session=session,
        )
        assert (
            "Онлайн-запись пока настраивается"
            in message.answer.call_args.kwargs["text"]
        )
        assert "reply_markup" not in message.answer.call_args.kwargs


@pytest.mark.asyncio
async def test_late_telegram_failure_preserves_published_brand(system):
    client = await base.login(
        system, 11001, raw=base.signed(11001, query_id=str(uuid.uuid4()))
    )
    async with system.factory() as session:
        await save_branding(
            session, system.masters[0].id, BrandingInput(brand_name="Published")
        )
        await session.commit()
    bot = AsyncMock()
    bot.set_my_commands.side_effect = TimeoutError()
    system.registry.get_by_instance_id = AsyncMock(return_value=bot)
    response = await base.post(client, "/master/branding/sync-telegram", {})
    assert not response.json()["ok"]
    bot.set_my_name.assert_awaited_once_with(name="Published")
    assert (await client.get("/api/miniapp/context")).json()["project"][
        "name"
    ] == "Published"


@pytest.mark.asyncio
async def test_registry_unavailable_is_retryable_without_token_leak(system):
    from app.services.exceptions import BotUnavailableError

    client = await base.login(
        system, 11001, raw=base.signed(11001, query_id=str(uuid.uuid4()))
    )
    system.registry.get_by_instance_id = AsyncMock(
        side_effect=BotUnavailableError("internal diagnostic")
    )
    response = await base.post(client, "/master/branding/sync-telegram", {})
    assert response.status_code == 200 and response.json()["ok"] is False
    assert "internal diagnostic" not in response.text


@pytest.mark.asyncio
async def test_service_text_escapes_markup_and_keeps_price_duration_callbacks():
    from decimal import Decimal
    from app.bot.handlers.client.services import cb_service_view
    from app.bot.keyboards.client.callbacks import ServiceCallback
    from app.bot.keyboards.client.services import get_services_list_keyboard

    service = SimpleNamespace(
        id=7,
        title="<b>Стрижка</b>",
        description="Уход & <script>",
        price=Decimal("1500"),
        duration_min=60,
        deposit_type=SimpleNamespace(value="FIXED"),
        deposit_value=Decimal("0"),
        is_active=True,
        is_archived=False,
    )
    callback = AsyncMock()
    with patch("app.bot.handlers.client.services.ServiceRepository") as repository:
        repository.return_value.get_by_id = AsyncMock(return_value=service)
        await cb_service_view(
            callback, ServiceCallback(action="view", service_id=7), AsyncMock(), 42
        )
        repository.return_value.get_by_id.assert_awaited_once_with(7, master_id=42)
    text = callback.message.edit_text.call_args.kwargs["text"]
    assert "&lt;b&gt;Стрижка&lt;/b&gt;" in text and "Уход &amp; &lt;script&gt;" in text
    menu = get_services_list_keyboard([service])
    assert (
        ServiceCallback.unpack(menu.inline_keyboard[0][0].callback_data).service_id == 7
    )
    assert "1 500" in menu.inline_keyboard[0][0].text
    assert (
        "1 ч" in menu.inline_keyboard[0][0].text
        or "60 мин" in menu.inline_keyboard[0][0].text
    )
