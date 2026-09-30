"""
Admin schedule and calendar management keyboards.
"""

import calendar
from datetime import date, timedelta
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.admin.callbacks import AdminCalendarCallback, AdminMenuCallback
from app.utils.formatters import RU_MONTHS_NOMINATIVE, RU_WEEKDAYS_SHORT


def get_admin_calendar_keyboard(year: int, month: int, today: date) -> InlineKeyboardMarkup:
    """
    Build monthly calendar grid for admin date selection.
    """
    builder = InlineKeyboardBuilder()

    # Header
    month_name = RU_MONTHS_NOMINATIVE[month]
    header = f"{month_name} {year}"

    prev_date = (date(year, month, 1) - timedelta(days=1))
    next_date = (date(year, month, 28) + timedelta(days=5)).replace(day=1)

    builder.row(
        InlineKeyboardButton(
            text="◀️",
            callback_data=AdminCalendarCallback(action="month", year=prev_date.year, month=prev_date.month).pack(),
        ),
        InlineKeyboardButton(
            text=header,
            callback_data="ignore",
        ),
        InlineKeyboardButton(
            text="▶️",
            callback_data=AdminCalendarCallback(action="month", year=next_date.year, month=next_date.month).pack(),
        ),
    )

    # Weekdays
    builder.row(
        *[InlineKeyboardButton(text=wd, callback_data="ignore") for wd in RU_WEEKDAYS_SHORT]
    )

    # Days grid
    month_cal = calendar.monthcalendar(year, month)
    for week in month_cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                d = date(year, month, day)
                mark = f"•{day}•" if d == today else str(day)
                row.append(
                    InlineKeyboardButton(
                        text=mark,
                        callback_data=AdminCalendarCallback(
                            action="day", year=year, month=month, day=day
                        ).pack(),
                    )
                )
        builder.row(*row)

    builder.row(
        InlineKeyboardButton(
            text="◀️ В панель мастера",
            callback_data=AdminMenuCallback(action="dashboard").pack(),
        )
    )

    return builder.as_markup()


def get_admin_day_management_keyboard(
    target_date: date, is_day_off: bool
) -> InlineKeyboardMarkup:
    """
    Actions available for a selected calendar date.
    """
    builder = InlineKeyboardBuilder()

    day_toggle_text = "🟢 Сделать РАБОЧИМ днём" if is_day_off else "🔴 Сделать ВЫХОДНЫМ днём"
    builder.row(
        InlineKeyboardButton(
            text=day_toggle_text,
            callback_data=AdminCalendarCallback(
                action="toggle_day_off",
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
            ).pack(),
        )
    )

    if not is_day_off:
        builder.row(
            InlineKeyboardButton(
                text="🕒 Настроить часы работы",
                callback_data=AdminCalendarCallback(
                    action="set_hours",
                    year=target_date.year,
                    month=target_date.month,
                    day=target_date.day,
                ).pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="🔒 Заблокировать время",
                callback_data=AdminCalendarCallback(
                    action="block_slot",
                    year=target_date.year,
                    month=target_date.month,
                    day=target_date.day,
                ).pack(),
            ),
            InlineKeyboardButton(
                text="➕ Записать клиента",
                callback_data=AdminCalendarCallback(
                    action="manual_book",
                    year=target_date.year,
                    month=target_date.month,
                    day=target_date.day,
                ).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="👁 Посмотреть записи дня",
            callback_data=AdminCalendarCallback(
                action="day_bookings",
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
            ).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад к календарю",
            callback_data=AdminCalendarCallback(
                action="month",
                year=target_date.year,
                month=target_date.month,
            ).pack(),
        )
    )

    return builder.as_markup()
