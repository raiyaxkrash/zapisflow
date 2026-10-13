from unittest.mock import AsyncMock, patch

import pytest
from aiogram import F
from aiogram.types import CallbackQuery, User as TelegramUser

from app.bot.handlers.client.booking import cb_agree_policy, cb_start_booking
from app.bot.handlers.client.callback_fallback import cb_unhandled_client_callback
from app.bot.handlers.client import client_router
from app.bot.handlers.client.about import router as about_router
from app.bot.handlers.client.booking import router as booking_router
from app.bot.handlers.client.my_appointments import router as appointments_router
from app.bot.handlers.client.portfolio import router as portfolio_router
from app.bot.handlers.client.services import router as services_router
from app.bot.keyboards.client.callbacks import MenuCallback
from app.bot.keyboards.client.menu import get_main_menu_keyboard
from app.services.exceptions import StaffServiceUnavailableError


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


@pytest.mark.asyncio
async def test_every_main_menu_callback_matches_its_registered_feature_handler():
    expected_handlers = {
        "book": (booking_router, "cb_start_booking"),
        "my_bookings": (appointments_router, "cb_my_bookings"),
        "portfolio": (portfolio_router, "cb_portfolio_categories"),
        "services": (services_router, "cb_services_list"),
        "contact": (about_router, "cb_contact_master"),
        "reviews": (about_router, "cb_client_reviews"),
        "about": (about_router, "cb_about_master"),
    }
    keyboard = get_main_menu_keyboard(has_portfolio=True, has_reviews=True)

    for row in keyboard.inline_keyboard:
        for button in row:
            parsed = MenuCallback.unpack(button.callback_data)
            assert parsed.action in expected_handlers
            expected_router, expected_name = expected_handlers[parsed.action]
            query = CallbackQuery(
                id=f"query-{parsed.action}",
                from_user=TelegramUser(
                    id=99, is_bot=False, first_name="Client"
                ),
                chat_instance="chat-instance",
                data=button.callback_data,
            )

            matches = []
            for handler in expected_router.callback_query.handlers:
                matched, _filter_data = await handler.check(query)
                if matched:
                    matches.append(handler.callback.__name__)

            assert expected_name in matches, (
                f"Button {button.text!r} ({button.callback_data!r}) did not match "
                f"{expected_router.name}.{expected_name}"
            )


def test_client_router_contains_booking_router():
    names = {router.name for router in client_router.sub_routers}
    assert "client_booking" in names
    assert "client_callback_fallback" in names


@pytest.mark.asyncio
async def test_dynamic_dispatcher_registers_client_callback_routers():
    from app.bot.bot_instance import create_dispatcher

    redis_client = AsyncMock()
    redis_client.ping.side_effect = ConnectionError("Redis unavailable in unit test")
    with patch("app.bot.bot_instance.Redis.from_url", return_value=redis_client):
        dispatcher = await create_dispatcher()

    registered_roots = {router.name: router for router in dispatcher.sub_routers}
    assert "client_root" in registered_roots
    active_client_router = registered_roots["client_root"]
    child_names = {router.name for router in active_client_router.sub_routers}
    assert {
        "client_start",
        "client_services",
        "client_booking",
        "client_payment",
        "client_my_appointments",
        "client_portfolio",
        "client_about",
        "client_callback_fallback",
    } <= child_names
    assert "callback_query" in dispatcher.resolve_used_update_types()


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


@pytest.mark.asyncio
async def test_agree_policy_acknowledges_staff_service_change_and_clears_stale_flow():
    callback = AsyncMock()
    state = AsyncMock()
    state.get_data.return_value = {"service_id": 10, "slot_timestamp": 1_800_000_000}
    session = AsyncMock()
    session.info = {}
    booking_service = AsyncMock()
    booking_service.create_hold_booking.side_effect = StaffServiceUnavailableError(
        "Выбранный специалист больше не оказывает эту услугу. Начните запись заново."
    )

    with patch(
        "app.bot.handlers.client.booking.BookingService",
        return_value=booking_service,
    ):
        await cb_agree_policy(
            callback=callback,
            state=state,
            db_user=AsyncMock(id=1),
            session=session,
            master_id=7,
        )

    state.clear.assert_awaited_once()
    callback.answer.assert_awaited_once_with(
        "Выбранный специалист больше не оказывает эту услугу. Начните запись заново.",
        show_alert=True,
    )
