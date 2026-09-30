"""
Admin appointments list and action keyboards.
"""

from datetime import datetime, timezone
from typing import Sequence
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.admin.callbacks import (
    AdminAppointmentCallback,
    AdminMenuCallback,
)
from app.database.models.appointment import Appointment, AppointmentStatus


def get_appointments_filters_keyboard() -> InlineKeyboardMarkup:
    """
    Submenu with appointment filter categories.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🌅 Сегодня",
            callback_data=AdminAppointmentCallback(action="list", filter_type="today").pack(),
        ),
        InlineKeyboardButton(
            text="🌄 Завтра",
            callback_data=AdminAppointmentCallback(action="list", filter_type="tomorrow").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔎 Ожидают проверки чека",
            callback_data=AdminAppointmentCallback(action="list", filter_type="pending_proof").pack(),
        ),
        InlineKeyboardButton(
            text="⏳ Ожидают оплаты",
            callback_data=AdminAppointmentCallback(action="list", filter_type="pending_payment").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔜 Все предстоящие",
            callback_data=AdminAppointmentCallback(action="list", filter_type="upcoming").pack(),
        ),
        InlineKeyboardButton(
            text="🎉 Завершённые",
            callback_data=AdminAppointmentCallback(action="list", filter_type="completed").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отменённые",
            callback_data=AdminAppointmentCallback(action="list", filter_type="cancelled").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ В панель мастера",
            callback_data=AdminMenuCallback(action="dashboard").pack(),
        )
    )

    return builder.as_markup()


def get_admin_appointment_list_keyboard(
    appointments: Sequence[Appointment], filter_type: str
) -> InlineKeyboardMarkup:
    """
    List of appointments matching the chosen filter.
    """
    builder = InlineKeyboardBuilder()

    for app in appointments:
        dt_str = app.start_time.strftime("%d.%m %H:%M")
        name = app.user.first_name if app.user else "Клиент"
        btn_text = f"#{app.id} {dt_str} {name} — {app.snapshot_service_title}"
        builder.row(
            InlineKeyboardButton(
                text=btn_text,
                callback_data=AdminAppointmentCallback(
                    action="detail", appointment_id=app.id, filter_type=filter_type
                ).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="◀️ К фильтрам записей",
            callback_data=AdminMenuCallback(action="appointments").pack(),
        )
    )
    return builder.as_markup()


def get_admin_appointment_card_keyboard(
    appointment: Appointment, filter_type: str = "today"
) -> InlineKeyboardMarkup:
    """
    Action keyboard for managing an individual client appointment.
    """
    builder = InlineKeyboardBuilder()

    # Active appointment actions
    if appointment.status in [
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.PAYMENT_PROOF_SENT,
        AppointmentStatus.WAITING_PAYMENT,
    ]:
        builder.row(
            InlineKeyboardButton(
                text="🗓 Перенести запись",
                callback_data=AdminAppointmentCallback(
                    action="reschedule", appointment_id=appointment.id, filter_type=filter_type
                ).pack(),
            ),
            InlineKeyboardButton(
                text="❌ Отменить запись",
                callback_data=AdminAppointmentCallback(
                    action="cancel", appointment_id=appointment.id, filter_type=filter_type
                ).pack(),
            ),
        )
        if appointment.status == AppointmentStatus.CONFIRMED and appointment.end_time <= datetime.now(timezone.utc):
            builder.row(
                InlineKeyboardButton(
                    text="✔️ Завершить (Выполнена)",
                    callback_data=AdminAppointmentCallback(
                        action="complete", appointment_id=appointment.id, filter_type=filter_type
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="🚫 Отметить NO-SHOW",
                    callback_data=AdminAppointmentCallback(
                        action="no_show", appointment_id=appointment.id, filter_type=filter_type
                    ).pack(),
                ),
            )

    if appointment.user and appointment.user.telegram_id:
        builder.row(
            InlineKeyboardButton(
                text="💬 Написать клиенту",
                url=f"tg://user?id={appointment.user.telegram_id}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="📝 Внутренняя заметка",
            callback_data=AdminAppointmentCallback(
                action="note", appointment_id=appointment.id, filter_type=filter_type
            ).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад к списку",
            callback_data=AdminAppointmentCallback(
                action="list", filter_type=filter_type
            ).pack(),
        )
    )

    return builder.as_markup()
