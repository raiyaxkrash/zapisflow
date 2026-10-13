from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from aiogram.types import MenuButtonCommands, MenuButtonWebApp

from app.config.settings import settings
from app.bot.keyboards.client.menu import get_main_menu_keyboard
from app.bot.keyboards.client.callbacks import MenuCallback
from app.services.bot_provisioning_service import BotProvisioningService
from app.bot.handlers.client.start import cmd_start
from app.bot.handlers.client.booking import cb_start_booking
from app.bot.states.client import ClientBookingSG


@pytest.mark.parametrize("origin", ["", "https://app.example.test"])
def test_message_menu_always_uses_text_booking(monkeypatch, origin):
    monkeypatch.setattr(settings, "mini_app_base_url", origin)
    buttons = [b for row in get_main_menu_keyboard().inline_keyboard for b in row]
    book = next(b for b in buttons if b.text == "📅 Записаться")
    assert book.web_app is None
    assert MenuCallback.unpack(book.callback_data).action == "book"
    assert all(b.web_app is None for b in buttons)
    assert not any("онлайн" in b.text.lower() or "Mini App" in b.text for b in buttons)


@pytest.mark.asyncio
async def test_system_menu_uses_config_and_separate_tenant_urls(monkeypatch):
    monkeypatch.setattr(settings, "mini_app_base_url", "https://app.example.test/")
    service = object.__new__(BotProvisioningService)
    service.gateway = SimpleNamespace(set_chat_menu_button=AsyncMock(return_value=True))
    ids = [uuid.uuid4(), uuid.uuid4()]
    for public_id in ids:
        assert await service.configure_client_menu(SimpleNamespace(public_id=public_id, mini_app_enabled=True), "test-token")
    for call, public_id in zip(service.gateway.set_chat_menu_button.call_args_list, ids):
        button = call.kwargs["menu_button"]
        assert isinstance(button, MenuButtonWebApp)
        assert button.text == "ZapisFlow"
        assert button.web_app.url == f"https://app.example.test/b/{public_id}"


@pytest.mark.asyncio
async def test_empty_config_does_not_install_broken_system_button(monkeypatch):
    monkeypatch.setattr(settings, "mini_app_base_url", "")
    service = object.__new__(BotProvisioningService)
    service.gateway = SimpleNamespace(set_chat_menu_button=AsyncMock(return_value=True))
    assert await service.configure_client_menu(SimpleNamespace(public_id=uuid.uuid4(), mini_app_enabled=True), "test-token")
    assert isinstance(service.gateway.set_chat_menu_button.call_args.kwargs["menu_button"], MenuButtonCommands)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["", "https://unreachable.example.test"])
async def test_start_menu_enters_existing_text_fsm_without_miniapp(monkeypatch, origin):
    monkeypatch.setattr(settings, "mini_app_base_url", origin)
    message, state = AsyncMock(), AsyncMock()
    await cmd_start(message, state, SimpleNamespace(first_name="Client"), False)
    menu = message.answer.call_args.kwargs["reply_markup"]
    assert MenuCallback.unpack(menu.inline_keyboard[0][0].callback_data).action == "book"
    callback = AsyncMock()
    callback.data = menu.inline_keyboard[0][0].callback_data
    staff = [SimpleNamespace(id=i, display_name=f"Staff {i}", specialization=None) for i in (1, 2)]
    with patch("app.bot.handlers.client.booking.SubscriptionAccessPolicy") as policy, patch(
        "app.bot.handlers.client.booking.StaffRepository"
    ) as repository:
        policy.return_value.can_accept_new_booking = AsyncMock(return_value=True)
        repository.return_value.list_active = AsyncMock(return_value=staff)
        await cb_start_booking(callback, state, AsyncMock(), master_id=7)
    state.set_state.assert_awaited_once_with(ClientBookingSG.choosing_staff)
    callback.message.edit_text.assert_awaited_once()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_instance_uses_commands_without_inline_webapp(monkeypatch):
    monkeypatch.setattr(settings, "mini_app_base_url", "https://app.example.test")
    service = object.__new__(BotProvisioningService)
    service.gateway = SimpleNamespace(set_chat_menu_button=AsyncMock(return_value=True))
    await service.configure_client_menu(SimpleNamespace(public_id=uuid.uuid4(), mini_app_enabled=False), "test-token")
    assert isinstance(service.gateway.set_chat_menu_button.call_args.kwargs["menu_button"], MenuButtonCommands)
    buttons = [button for row in get_main_menu_keyboard(is_admin=True).inline_keyboard for button in row]
    assert any(button.text == "⚙️ Панель управления" for button in buttons)
    assert all(button.web_app is None for button in buttons)
    assert next(button for button in buttons if button.text == "📅 Записаться").callback_data == "menu:book"
