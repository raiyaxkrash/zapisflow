"""
Text formatting helpers for Russian localization, currency, durations and appointment cards.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import pytz

RU_MONTHS = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

RU_MONTHS_NOMINATIVE = [
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]

RU_WEEKDAYS = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]
RU_WEEKDAYS_FULL = RU_WEEKDAYS

RU_WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def format_rub(amount: Decimal | float | int) -> str:
    """
    Format monetary amounts with thin thousand separators: 2 500 ₽.
    """
    val = int(amount) if float(amount).is_integer() else float(amount)
    formatted = f"{val:,.2f}" if isinstance(val, float) else f"{val:,}"
    return formatted.replace(",", " ") + " ₽"


def format_duration(minutes: int) -> str:
    """
    Format minutes into readable string: 90 -> '1 ч 30 мин', 60 -> '1 ч', 45 -> '45 мин'.
    """
    hours = minutes // 60
    rem_min = minutes % 60
    if hours > 0 and rem_min > 0:
        return f"{hours} ч {rem_min} мин"
    elif hours > 0:
        return f"{hours} ч"
    return f"{rem_min} мин"


def format_date_ru(d: date) -> str:
    """
    Format date as '15 октября 2026'.
    """
    return f"{d.day} {RU_MONTHS[d.month]} {d.year}"


def format_datetime_ru(dt: datetime, tz_name: str = "Europe/Moscow") -> str:
    """
    Format timezone-aware datetime into Russian localized string.
    """
    tz = pytz.timezone(tz_name)
    local_dt = dt.astimezone(tz)
    weekday = RU_WEEKDAYS[local_dt.weekday()]
    return f"{local_dt.day} {RU_MONTHS[local_dt.month]} {local_dt.year} ({weekday}) в {local_dt.strftime('%H:%M')}"


def format_time_ru(dt: datetime, tz_name: str = "Europe/Moscow") -> str:
    """
    Format time portion as '14:30' localized to timezone.
    """
    tz = pytz.timezone(tz_name)
    local_dt = dt.astimezone(tz)
    return local_dt.strftime("%H:%M")


def render_appointment_card(appointment, tz_name: str = "Europe/Moscow") -> str:
    """
    Render clean client-facing appointment information card.
    """
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_name)
    dur_str = format_duration(appointment.snapshot_service_duration_min)
    price_str = format_rub(appointment.snapshot_service_price)
    deposit_str = format_rub(appointment.snapshot_deposit_amount)
    remaining = appointment.snapshot_service_price - appointment.snapshot_deposit_amount
    rem_str = format_rub(remaining)

    status_icon = "⏳"
    status_text = appointment.status.display_name

    text = (
        f"<b>📋 Запись #{appointment.id}</b>\n\n"
        f"🌸 <b>Услуга:</b> {appointment.snapshot_service_title}\n"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"⏳ <b>Длительность:</b> {dur_str}\n"
        f"💰 <b>Стоимость:</b> {price_str}\n"
        f"💳 <b>Предоплата:</b> {deposit_str}\n"
        f"💵 <b>Остаток к оплате:</b> {rem_str}\n"
        f"📌 <b>Статус:</b> {status_text}\n"
    )

    if appointment.cancel_reason:
        text += f"\nℹ️ <i>Причина отмены:</i> {appointment.cancel_reason}"

    return text
