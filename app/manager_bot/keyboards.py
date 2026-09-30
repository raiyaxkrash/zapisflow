"""Inline keyboards for platform Manager Bot."""

from collections.abc import Sequence
from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.models.master import BotInstance, BotInstanceStatus, Master


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu of Manager Bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Мои проекты", callback_data="mgr:projects")],
            [InlineKeyboardButton(text="➕ Создать проект", callback_data="mgr:master:new")],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="mgr:help")],
        ]
    )


def project_list_keyboard(masters: Sequence[Master]) -> InlineKeyboardMarkup:
    """List of user's registered master projects."""
    buttons = []
    for master in masters:
        status_icon = "🟢" if master.status.value == "ACTIVE" else "🟡"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {master.display_name}",
                callback_data=f"mgr:master:{master.id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="➕ Новый проект", callback_data="mgr:master:new")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="mgr:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def project_card_keyboard(
    master: Master,
    bot_instance: Optional[BotInstance] = None,
    is_ready: bool = False,
) -> InlineKeyboardMarkup:
    """Action buttons for a single Master project."""
    rows = []

    if bot_instance is None:
        # No bot connected yet
        rows.append([
            InlineKeyboardButton(
                text="🤖 Подключить Telegram-бота",
                callback_data=f"mgr:bot:connect:{master.id}",
            )
        ])
    elif bot_instance.status == BotInstanceStatus.SETUP_REQUIRED:
        if bot_instance.telegram_username:
            rows.append([
                InlineKeyboardButton(
                    text="🔗 Настроить в боте (Deep Link)",
                    url=f"https://t.me/{bot_instance.telegram_username}?start=admin",
                )
            ])
        rows.append([
            InlineKeyboardButton(
                text="🚀 Запустить приём записей",
                callback_data=f"mgr:bot:activate:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="📋 Чек-лист готовности",
                callback_data=f"mgr:bot:checklist:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="♻️ Заменить токен",
                callback_data=f"mgr:bot:rotate:{master.id}",
            ),
            InlineKeyboardButton(
                text="⏸ Отключить",
                callback_data=f"mgr:bot:disable:{master.id}",
            ),
        ])
    elif bot_instance.status == BotInstanceStatus.ACTIVE:
        if bot_instance.telegram_username:
            rows.append([
                InlineKeyboardButton(
                    text="🔗 Открыть бота",
                    url=f"https://t.me/{bot_instance.telegram_username}?start=admin",
                )
            ])
        rows.append([
            InlineKeyboardButton(
                text="📋 Чек-лист настроек",
                callback_data=f"mgr:bot:checklist:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="♻️ Заменить токен",
                callback_data=f"mgr:bot:rotate:{master.id}",
            ),
            InlineKeyboardButton(
                text="⏸ Отключить бота",
                callback_data=f"mgr:bot:disable:{master.id}",
            ),
        ])
    elif bot_instance.status == BotInstanceStatus.ERROR:
        rows.append([
            InlineKeyboardButton(
                text="🔄 Повторить подключение",
                callback_data=f"mgr:bot:retry:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="♻️ Заменить токен",
                callback_data=f"mgr:bot:rotate:{master.id}",
            )
        ])
    elif bot_instance.status == BotInstanceStatus.DISABLED:
        rows.append([
            InlineKeyboardButton(
                text="▶️ Включить бота",
                callback_data=f"mgr:bot:enable:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="♻️ Заменить токен",
                callback_data=f"mgr:bot:rotate:{master.id}",
            )
        ])

    rows.append([InlineKeyboardButton(text="⬅️ Назад к проектам", callback_data="mgr:projects")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_connect_keyboard() -> InlineKeyboardMarkup:
    """Confirmation buttons for onboarding candidate bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подключить", callback_data="mgr:bot:confirm_connect"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="mgr:cancel"),
            ]
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Generic cancel button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="mgr:cancel")]
        ]
    )
