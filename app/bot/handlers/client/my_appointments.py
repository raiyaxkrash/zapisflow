"""
Client appointment management: view active and past appointments, details and cancellations.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import (
    BookingActionCallback,
    MenuCallback,
    get_appointment_detail_keyboard,
    get_main_menu_keyboard,
    get_my_appointments_keyboard,
    get_payment_screen_keyboard,
)
from app.config.settings import settings
from app.database.models.appointment import AppointmentStatus
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.services.booking_service import BookingService
from app.utils.formatters import render_appointment_card

router = Router(name="client_my_appointments")


@router.callback_query(MenuCallback.filter(F.action == "my_bookings"))
async def cb_my_bookings(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
    session: AsyncSession, master_id: int,
) -> None:
    """
    List user's active upcoming bookings for current master.
    """
    await state.clear()
    appointment_repo = AppointmentRepository(session)
    appointments = await appointment_repo.get_user_upcoming(user_id=db_user.id, master_id=master_id)

    if not appointments:
        text = (
            "<b>📋 Мои записи</b>\n\n"
            "У вас пока нет активных предстоящих записей.\n\n"
            "Вы можете записаться на услугу прямо сейчас 👇"
        )
    else:
        text = (
            "<b>📋 Ваши активные записи:</b>\n\n"
            "Нажмите на запись для просмотра полной информации или управления:"
        )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_my_appointments_keyboard(appointments),
        )
    await callback.answer()


@router.callback_query(BookingActionCallback.filter(F.action == "detail"))
async def cb_booking_detail(
    callback: CallbackQuery,
    callback_data: BookingActionCallback,
    db_user: User,
    session: AsyncSession, master_id: int,
) -> None:
    """
    Show full appointment card.
    """
    appointment_repo = AppointmentRepository(session)
    appointment = await appointment_repo.get_by_id_with_relations(
        appointment_id=callback_data.appointment_id, master_id=master_id
    )

    if not appointment or appointment.user_id != db_user.id:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)

    card_text = render_appointment_card(appointment, tz_name=tz_str)

    # If appointment is still waiting for payment, show requisites / pay button
    if appointment.status == AppointmentStatus.WAITING_PAYMENT:
        bank_name = await settings_repo.get_value(master_id, "bank_name", settings.bank_name)
        card_number = await settings_repo.get_value(master_id, "bank_card_number", settings.bank_card_number)
        phone_req = await settings_repo.get_value(master_id, "default_phone_requisites", settings.default_phone_requisites)
        recipient = await settings_repo.get_value(master_id, "bank_recipient_name", settings.bank_recipient_name)

        card_text += (
            f"\n\n<b>Реквизиты для предоплаты:</b>\n"
            f"🏦 {bank_name} | 💳 <code>{card_number}</code>\n"
            f"📱 СБП: <code>{phone_req}</code> ({recipient})"
        )
        markup = get_payment_screen_keyboard(appointment.id)
    else:
        card_text += (
            "\n\nℹ️ <i>Для переноса времени или даты записи, пожалуйста, свяжитесь с мастером лично.</i>"
        )
        markup = get_appointment_detail_keyboard(appointment)

    if callback.message:
        await callback.message.edit_text(text=card_text, reply_markup=markup)
    await callback.answer()


@router.callback_query(BookingActionCallback.filter(F.action == "client_cancel"))
async def cb_client_cancel(
    callback: CallbackQuery,
    callback_data: BookingActionCallback,
    db_user: User,
    session: AsyncSession, master_id: int,
) -> None:
    """
    Client initiates cancellation: marks status CANCELLED_BY_CLIENT and retains deposit.
    """
    booking_service = BookingService(session)

    try:
        await booking_service.cancel_booking_by_client(
            master_id=master_id,
            appointment_id=callback_data.appointment_id,
            user_id=db_user.id,
            reason="Отменено клиентом через Telegram-бота",
        )
        text = (
            "Ваша запись отменена ❌\n\n"
            "Подтверждённая предоплата удерживается согласно правилам бронирования. "
            "Если ваш чек ещё проверяется, мастер отдельно проверит поступление перевода."
        )
    except Exception as e:
        text = f"Не удалось отменить запись: {e}"

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=get_main_menu_keyboard())
    await callback.answer()
