"""
Keyboards for booking confirmation, policies, payment screens and appointment details.
"""

from typing import Any, Sequence
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from app.bot.keyboards.client.callbacks import (
    BookingActionCallback,
    PolicyAgreementCallback,
    MenuCallback,
    StaffChoiceCallback,
)
from app.database.models.appointment import Appointment, AppointmentStatus
from app.utils.formatters import format_datetime_ru


def get_staff_selection_keyboard(
    staff_members: Sequence[Any],
) -> InlineKeyboardMarkup:
    """
    Inline keyboard allowing client to select a specific specialist.
    """
    builder = InlineKeyboardBuilder()
    for s in staff_members:
        spec_text = f" ({s.specialization})" if getattr(s, "specialization", None) else ""
        builder.row(
            InlineKeyboardButton(
                text=f"👩‍💼 {s.display_name}{spec_text}",
                callback_data=StaffChoiceCallback(action="select", staff_id=s.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=MenuCallback(action="main").pack(),
        )
    )
    return builder.as_markup()


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


def get_policy_agreement_keyboard(confirmation_id: str | None = None) -> InlineKeyboardMarkup:
    """
    Cancellation policy confirmation keyboard.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Согласен(на), перейти к оплате",
            callback_data=(PolicyAgreementCallback(confirmation_id=confirmation_id).pack()
                           if confirmation_id else BookingActionCallback(action="agree_policy").pack()),
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


def get_appointment_detail_keyboard(appointment: Appointment) -> InlineKeyboardMarkup:
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

    # Post-visit features for completed appointment
    if appointment.status == AppointmentStatus.COMPLETED:
        builder.row(
            InlineKeyboardButton(
                text="📅 Записаться снова",
                callback_data=BookingActionCallback(
                    action="repeat", appointment_id=appointment.id
                ).pack(),
            ),
            InlineKeyboardButton(
                text="⭐ Оценить визит",
                callback_data=BookingActionCallback(
                    action="review", appointment_id=appointment.id
                ).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="📞 Контакты",
            callback_data=MenuCallback(action="contact").pack(),
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


def get_review_rating_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    """Rating selection keyboard (1 to 5 stars)."""
    builder = InlineKeyboardBuilder()
    for star in [5, 4, 3, 2, 1]:
        builder.row(
            InlineKeyboardButton(
                text=f"{'⭐' * star} ({star})",
                callback_data=f"rev:star:{appointment_id}:{star}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад к записи",
            callback_data=BookingActionCallback(
                action="detail", appointment_id=appointment_id
            ).pack(),
        )
    )
    return builder.as_markup()


def get_review_skip_keyboard(appointment_id: int, rating: int) -> InlineKeyboardMarkup:
    """Keyboard allowing to skip optional comment."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⏩ Без комментария",
            callback_data=f"rev:skip:{appointment_id}:{rating}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🏠 В главное меню",
            callback_data=MenuCallback(action="main").pack(),
        )
    )
    return builder.as_markup()
