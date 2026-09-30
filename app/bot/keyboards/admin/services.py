"""
Admin services and pricing management keyboards.
"""

from typing import Sequence
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.admin.callbacks import AdminMenuCallback, AdminServiceCallback
from app.database.models.service import Service
from app.utils.formatters import format_rub


def get_admin_services_list_keyboard(services: Sequence[Service]) -> InlineKeyboardMarkup:
    """
    List of master services with status indicators and add button.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить новую услугу",
            callback_data=AdminServiceCallback(action="add").pack(),
        )
    )

    for svc in services:
        status_icon = "🟢" if svc.is_active else ("📦" if svc.is_archived else "🔴")
        btn_text = f"{status_icon} {svc.title} ({format_rub(svc.price)})"
        builder.row(
            InlineKeyboardButton(
                text=btn_text,
                callback_data=AdminServiceCallback(action="detail", service_id=svc.id).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="◀️ В панель мастера",
            callback_data=AdminMenuCallback(action="dashboard").pack(),
        )
    )
    return builder.as_markup()


def get_admin_service_card_keyboard(service: Service) -> InlineKeyboardMarkup:
    """
    Detailed actions and edit buttons for a service.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="✏️ Название",
            callback_data=AdminServiceCallback(action="edit", service_id=service.id, field="title").pack(),
        ),
        InlineKeyboardButton(
            text="✏️ Стоимость",
            callback_data=AdminServiceCallback(action="edit", service_id=service.id, field="price").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Длительность",
            callback_data=AdminServiceCallback(action="edit", service_id=service.id, field="duration").pack(),
        ),
        InlineKeyboardButton(
            text="✏️ Буфер",
            callback_data=AdminServiceCallback(action="edit", service_id=service.id, field="buffer").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Предоплата",
            callback_data=AdminServiceCallback(action="edit", service_id=service.id, field="deposit").pack(),
        )
    )

    toggle_text = "🔴 Выключить услугу" if service.is_active else "🟢 Включить услугу"
    archive_text = "♻️ Восстановить из архива" if service.is_archived else "📦 Архивировать"

    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=AdminServiceCallback(action="toggle", service_id=service.id).pack(),
        ),
        InlineKeyboardButton(
            text=archive_text,
            callback_data=AdminServiceCallback(
                action="unarchive" if service.is_archived else "archive", service_id=service.id
            ).pack(),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="◀️ К списку услуг",
            callback_data=AdminServiceCallback(action="list").pack(),
        )
    )
    return builder.as_markup()
