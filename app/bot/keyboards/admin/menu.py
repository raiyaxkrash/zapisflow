"""
Admin dashboard navigation keyboards.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.admin.callbacks import AdminMenuCallback


def get_admin_dashboard_keyboard() -> InlineKeyboardMarkup:
    """
    Main administrative dashboard menu.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📅 Записи клиентов",
            callback_data=AdminMenuCallback(action="appointments").pack(),
        ),
        InlineKeyboardButton(
            text="💳 Входящие чеки",
            callback_data=AdminMenuCallback(action="payments").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🗓 Календарь и график",
            callback_data=AdminMenuCallback(action="calendar").pack(),
        ),
        InlineKeyboardButton(
            text="💰 Услуги и цены",
            callback_data=AdminMenuCallback(action="services").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="👥 База клиентов (CRM)",
            callback_data=AdminMenuCallback(action="clients").pack(),
        ),
        InlineKeyboardButton(
            text="⚙️ Настройки и реквизиты",
            callback_data=AdminMenuCallback(action="settings").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика и доходы",
            callback_data=AdminMenuCallback(action="analytics").pack(),
        ),
        InlineKeyboardButton(
            text="📢 Рассылка клиентам",
            callback_data=AdminMenuCallback(action="broadcast").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🏠 Выйти в меню клиента",
            callback_data=AdminMenuCallback(action="exit").pack(),
        )
    )

    return builder.as_markup()


def get_admin_back_keyboard() -> InlineKeyboardMarkup:
    """
    Back button to admin main dashboard.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="◀️ В панель мастера",
        callback_data=AdminMenuCallback(action="dashboard").pack(),
    )
    return builder.as_markup()
