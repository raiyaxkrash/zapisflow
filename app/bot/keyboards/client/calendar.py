"""
Interactive inline calendar generator for appointment date selection.
"""

import calendar
from datetime import date, timedelta
from typing import Set
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.client.callbacks import CalendarNavCallback, MenuCallback, ServiceCallback
from app.utils.formatters import RU_MONTHS_NOMINATIVE, RU_WEEKDAYS_SHORT


def build_inline_calendar(
    year: int,
    month: int,
    available_dates: Set[date],
    today: date,
    service_id: int,
) -> InlineKeyboardMarkup:
    """
    Build an interactive inline calendar for a given month and year.
    Only days present in available_dates and >= today are selectable.
    """
    builder = InlineKeyboardBuilder()

    # 1. Month and Year Header with navigation arrows
    month_name = RU_MONTHS_NOMINATIVE[month]
    header_text = f"{month_name} {year}"

    # Previous and Next month calculations
    prev_date = (date(year, month, 1) - timedelta(days=1))
    next_date = (date(year, month, 28) + timedelta(days=5)).replace(day=1)

    can_go_prev = (prev_date.year, prev_date.month) >= (today.year, today.month)

    prev_cb = (
        CalendarNavCallback(action="prev_month", year=prev_date.year, month=prev_date.month).pack()
        if can_go_prev
        else CalendarNavCallback(action="ignore", year=year, month=month).pack()
    )
    next_cb = CalendarNavCallback(
        action="next_month", year=next_date.year, month=next_date.month
    ).pack()

    builder.row(
        InlineKeyboardButton(text="◀️" if can_go_prev else " ", callback_data=prev_cb),
        InlineKeyboardButton(
            text=header_text,
            callback_data=CalendarNavCallback(action="ignore", year=year, month=month).pack(),
        ),
        InlineKeyboardButton(text="▶️", callback_data=next_cb),
    )

    # 2. Weekday abbreviations row (Пн, Вт, Ср, Чт, Пт, Сб, Вс)
    builder.row(
        *[
            InlineKeyboardButton(
                text=wd,
                callback_data=CalendarNavCallback(action="ignore", year=year, month=month).pack(),
            )
            for wd in RU_WEEKDAYS_SHORT
        ]
    )

    # 3. Days grid (Monday = 0)
    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        week_buttons = []
        for day in week:
            if day == 0:
                # Padding before first day of month
                week_buttons.append(
                    InlineKeyboardButton(
                        text=" ",
                        callback_data=CalendarNavCallback(action="ignore", year=year, month=month).pack(),
                    )
                )
            else:
                day_date = date(year, month, day)
                is_available = day_date in available_dates and day_date >= today

                if is_available:
                    btn_text = f"• {day} •" if day_date == today else str(day)
                    cb = CalendarNavCallback(
                        action="select_day", year=year, month=month, day=day
                    ).pack()
                else:
                    btn_text = "·"
                    cb = CalendarNavCallback(action="ignore", year=year, month=month).pack()

                week_buttons.append(InlineKeyboardButton(text=btn_text, callback_data=cb))

        builder.row(*week_buttons)

    # 4. Footer buttons
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад к услугам",
            callback_data=ServiceCallback(action="list", service_id=0).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 В меню",
            callback_data=MenuCallback(action="main").pack(),
        ),
    )

    return builder.as_markup()
