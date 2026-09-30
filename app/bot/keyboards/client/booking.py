"""
Keyboards for booking confirmation, policies, payment screens and appointment details.
"""

from typing import Sequence
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from app.bot.keyboards.client.callbacks import BookingActionCallback, MenuCallback
from app.database.models.appointment import Appointment, AppointmentStatus
from app.utils.formatters import format_datetime_ru


def get_phone_request_keyboard() -> ReplyKeyboardMarkup:
    """
    Build Reply keyboard requesting client's phone number natively.
    """
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)
    )
    builder.row(
        KeyboardButton(text="❌ Отмена")
    )
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def get_policy_agreement_keyboard() -> InlineKeyboardMarkup:
    """
    Cancellation policy confirmation keyboard.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Согласен(на), перейти к оплате",
            callback_data=BookingActionCallback(action="agree_policy").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=BookingActionCallback(action="cancel_policy").pack(),
        )
    )
    return builder.as_markup()


def get_payment_screen_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    """
    Payment instructions screen keyboard.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Я оплатил(а)",
            callback_data=BookingActionCallback(
                action="i_paid", appointment_id=appointment_id
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отменить бронь",
            callback_data=BookingActionCallback(
                action="cancel_booking", appointment_id=appointment_id
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MenuCallback(action="main").pack(),
        ),
    )
    return builder.as_markup()


def get_cancel_upload_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    """
    Cancel receipt upload keyboard.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="◀️ Вернуться к реквизитам",
        callback_data=BookingActionCallback(
            action="detail", appointment_id=appointment_id
        ).pack(),
    )
    return builder.as_markup()


def get_my_appointments_keyboard(
    appointments: Sequence[Appointment],
) -> InlineKeyboardMarkup:
    """
    List user's active and upcoming appointments.
    """
    builder = InlineKeyboardBuilder()

    if not appointments:
        builder.row(
            InlineKeyboardButton(
                text="📅 Записаться на услугу",
                callback_data=MenuCallback(action="book").pack(),
            )
        )
    else:
        for app in appointments:
            dt_str = app.start_time.strftime("%d.%m %H:%M")
            btn_text = f"#{app.id} {app.snapshot_service_title} ({dt_str}) — {app.status.display_name}"
            builder.row(
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=BookingActionCallback(
                        action="detail", appointment_id=app.id
                    ).pack(),
                )
            )

    builder.row(
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MenuCallback(action="main").pack(),
        )
    )
    return builder.as_markup()


def get_appointment_detail_keyboard(
    appointment: Appointment, master_contact_url: str
) -> InlineKeyboardMarkup:
    """
    Detailed appointment actions: cancellation, contact master, or back.
    """
    builder = InlineKeyboardBuilder()

    # If appointment is in active status, allow client cancellation
    if appointment.status in [
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.PAYMENT_PROOF_SENT,
        AppointmentStatus.WAITING_PAYMENT,
    ]:
        builder.row(
            InlineKeyboardButton(
                text="❌ Отменить запись",
                callback_data=BookingActionCallback(
                    action="client_cancel", appointment_id=appointment.id
                ).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="📞 Связаться с мастером",
            url=master_contact_url,
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="◀️ К списку записей",
            callback_data=MenuCallback(action="my_bookings").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 В меню",
            callback_data=MenuCallback(action="main").pack(),
        ),
    )
    return builder.as_markup()
