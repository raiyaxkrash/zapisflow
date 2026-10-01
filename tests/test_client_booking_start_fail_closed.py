from unittest.mock import AsyncMock

import pytest
from aiogram import F
from aiogram.types import CallbackQuery, User as TelegramUser

from app.bot.handlers.client.booking import cb_start_booking
from app.bot.handlers.client.callback_fallback import cb_unhandled_client_callback
from app.bot.handlers.client import client_router
from app.bot.keyboards.client.callbacks import MenuCallback
from app.bot.keyboards.client.menu import get_main_menu_keyboard


@pytest.mark.asyncio
async def test_booking_start_answers_when_trusted_tenant_context_is_missing():
    callback = AsyncMock()

    await cb_start_booking(
        callback=callback,
        state=AsyncMock(),
        session=AsyncMock(),
        master_id=None,
    )

    callback.answer.assert_awaited_once_with(
        "Не удалось определить проект. Пожалуйста, откройте бота заново.",
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_main_menu_booking_button_round_trips_to_book_action():
    keyboard = get_main_menu_keyboard()
    booking_button = keyboard.inline_keyboard[0][0]

    assert booking_button.text == "📅 Записаться"
    parsed = MenuCallback.unpack(booking_button.callback_data)
    assert parsed.action == "book"

    query = CallbackQuery(
        id="query-1",
        from_user=TelegramUser(id=99, is_bot=False, first_name="Client"),
        chat_instance="chat-instance",
        data=booking_button.callback_data,
    )
    matched = await MenuCallback.filter(F.action == "book")(query)
    assert matched is not False
    assert matched["callback_data"].action == "book"


def test_client_router_contains_booking_router():
    names = {router.name for router in client_router.sub_routers}
    assert "client_booking" in names
    assert "client_callback_fallback" in names


@pytest.mark.asyncio
async def test_unknown_client_callback_is_acknowledged_with_visible_alert():
    callback = AsyncMock()
    callback.data = "old:callback"
    bot_instance = AsyncMock()
    bot_instance.id = 17

    await cb_unhandled_client_callback(callback, bot_instance)

    callback.answer.assert_awaited_once_with(
        "Эта кнопка устарела. Откройте меню командой /start и попробуйте снова.",
        show_alert=True,
    )
