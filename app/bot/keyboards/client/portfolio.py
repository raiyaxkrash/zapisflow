"""
Portfolio browsing and slider keyboards.
"""

from typing import Sequence
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.client.callbacks import MenuCallback, PortfolioNavCallback
from app.database.models.portfolio import PortfolioCategory


def get_portfolio_categories_keyboard(
    categories: Sequence[PortfolioCategory],
) -> InlineKeyboardMarkup:
    """
    List of active portfolio categories.
    """
    builder = InlineKeyboardBuilder()

    for cat in categories:
        builder.row(
            InlineKeyboardButton(
                text=cat.title,
                callback_data=PortfolioNavCallback(
                    action="category", category_id=cat.id, item_index=0
                ).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MenuCallback(action="main").pack(),
        )
    )
    return builder.as_markup()


def get_portfolio_item_keyboard(
    category_id: int, current_index: int, total_count: int
) -> InlineKeyboardMarkup:
    """
    Navigation slider keyboard for viewing portfolio items in a category.
    """
    builder = InlineKeyboardBuilder()

    # Navigation arrows row
    nav_buttons = []
    if current_index > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=PortfolioNavCallback(
                    action="prev", category_id=category_id, item_index=current_index - 1
                ).pack(),
            )
        )

    nav_buttons.append(
        InlineKeyboardButton(
            text=f"{current_index + 1} / {total_count}",
            callback_data="ignore",
        )
    )

    if current_index < total_count - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперёд ▶️",
                callback_data=PortfolioNavCallback(
                    action="next", category_id=category_id, item_index=current_index + 1
                ).pack(),
            )
        )

    builder.row(*nav_buttons)

    # Action row
    builder.row(
        InlineKeyboardButton(
            text="📅 Записаться",
            callback_data=MenuCallback(action="book").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📂 Все категории",
            callback_data=PortfolioNavCallback(action="categories").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 В меню",
            callback_data=MenuCallback(action="main").pack(),
        ),
    )

    return builder.as_markup()
