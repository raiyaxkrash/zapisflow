"""
Time slot selection keyboard generator.
"""

from datetime import date, datetime
from typing import Sequence
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.client.callbacks import CalendarNavCallback, MenuCallback, TimeSlotCallback


def build_time_slots_keyboard(
    service_id: int,
    slots: Sequence[datetime],
    target_date: date,
) -> InlineKeyboardMarkup:
    """
    Build a grid of available time slots for the chosen date.
    """
    builder = InlineKeyboardBuilder()

    if not slots:
        builder.row(
            InlineKeyboardButton(
                text="❌ Нет свободных окон на этот день",
                callback_data=CalendarNavCallback(
                    action="ignore", year=target_date.year, month=target_date.month
                ).pack(),
            )
        )
    else:
        # Group slot buttons in 3 columns
        buttons = []
        for slot in slots:
            btn_text = slot.strftime("%H:%M")
            cb = TimeSlotCallback(
                service_id=service_id, timestamp=int(slot.timestamp())
            ).pack()
            buttons.append(InlineKeyboardButton(text=btn_text, callback_data=cb))

        builder.add(*buttons)
        builder.adjust(3)

    # Footer navigation
    builder.row(
        InlineKeyboardButton(
            text="◀️ Выбрать другой день",
            callback_data=CalendarNavCallback(
                action="select_day", year=target_date.year, month=target_date.month, day=0
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MenuCallback(action="main").pack(),
        ),
    )

    return builder.as_markup()
