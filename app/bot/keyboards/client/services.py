"""
Keyboards for services browsing and selection.
"""

from typing import Sequence
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.client.callbacks import MenuCallback, ServiceCallback
from app.database.models.service import Service
from app.utils.formatters import format_rub, format_duration


def get_services_list_keyboard(
    services: Sequence[Service], is_booking_flow: bool = False
) -> InlineKeyboardMarkup:
    """
    Build keyboard with active services.
    """
    builder = InlineKeyboardBuilder()

    for svc in services:
        action = "select" if is_booking_flow else "view"
        btn_text = f"{svc.title} — {format_rub(svc.price)} · {format_duration(svc.duration_min)}"
        builder.row(
            InlineKeyboardButton(
                text=btn_text,
                callback_data=ServiceCallback(action=action, service_id=svc.id).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MenuCallback(action="main").pack(),
        )
    )

    return builder.as_markup()


def get_service_detail_keyboard(service_id: int) -> InlineKeyboardMarkup:
    """
    Build keyboard for service details view.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📅 Выбрать дату",
            callback_data=ServiceCallback(action="select", service_id=service_id).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ К списку услуг",
            callback_data=ServiceCallback(action="list", service_id=0).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MenuCallback(action="main").pack(),
        ),
    )

    return builder.as_markup()
