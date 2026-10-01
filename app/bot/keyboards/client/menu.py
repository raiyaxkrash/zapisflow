"""
Main menu and navigation inline keyboards for clients.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.client.callbacks import MenuCallback


def get_main_menu_keyboard(
    is_admin: bool = False,
    has_portfolio: bool = True,
    has_reviews: bool = True,
) -> InlineKeyboardMarkup:
    """
    Build client main menu keyboard. Adds admin panel button if user is admin.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📅 Записаться",
            callback_data=MenuCallback(action="book").pack(),
        )
    )
    row_2 = [
        InlineKeyboardButton(
            text="📋 Мои записи",
            callback_data=MenuCallback(action="my_bookings").pack(),
        )
    ]
    if has_portfolio:
        row_2.append(
            InlineKeyboardButton(
                text="🖼 Портфолио",
                callback_data=MenuCallback(action="portfolio").pack(),
            )
        )
    builder.row(*row_2)

    builder.row(
        InlineKeyboardButton(
            text="💰 Услуги и цены",
            callback_data=MenuCallback(action="services").pack(),
        ),
        InlineKeyboardButton(
            text="📍 Контакты",
            callback_data=MenuCallback(action="contact").pack(),
        ),
    )

    row_4 = []
    if has_reviews:
        row_4.append(
            InlineKeyboardButton(
                text="⭐ Отзывы",
                callback_data=MenuCallback(action="reviews").pack(),
            )
        )
    row_4.append(
        InlineKeyboardButton(
            text="👤 Обо мне",
            callback_data=MenuCallback(action="about").pack(),
        )
    )
    builder.row(*row_4)

    if is_admin:
        builder.row(
            InlineKeyboardButton(
                text="⚙️ Панель мастера (Админка)",
                callback_data="admin:menu",
            )
        )

    return builder.as_markup()


def get_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Standard back to main menu keyboard.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🏠 Главное меню",
        callback_data=MenuCallback(action="main").pack(),
    )
    return builder.as_markup()
