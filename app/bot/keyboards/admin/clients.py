"""
Admin CRM clients database keyboards.
"""

from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.admin.callbacks import AdminClientCallback, AdminMenuCallback


def get_admin_clients_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Client CRM root menu.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🔎 Найти клиента (по имени / телефону / @username)",
            callback_data=AdminClientCallback(action="search").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ В панель мастера",
            callback_data=AdminMenuCallback(action="dashboard").pack(),
        )
    )
    return builder.as_markup()


def get_admin_client_card_keyboard(
    user_id: int, telegram_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    """
    Action keyboard for an individual client profile.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📝 Добавить заметку мастера",
            callback_data=AdminClientCallback(action="note", user_id=user_id).pack(),
        )
    )
    if telegram_id:
        builder.row(
            InlineKeyboardButton(
                text="💬 Написать в Telegram",
                url=f"tg://user?id={telegram_id}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="◀️ К поиску клиентов",
            callback_data=AdminClientCallback(action="list").pack(),
        )
    )
    return builder.as_markup()
