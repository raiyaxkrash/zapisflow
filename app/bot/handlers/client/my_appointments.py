"""
Client appointment management: view active and past appointments, details and cancellations.
"""

from datetime import datetime
from html import escape
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
import pytz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import (
    BookingActionCallback,
    MenuCallback,
    build_inline_calendar,
    get_appointment_detail_keyboard,
    get_my_appointments_keyboard,
    get_payment_screen_keyboard,
    get_review_rating_keyboard,
    get_review_skip_keyboard,
    get_services_list_keyboard,
)
from app.bot.states.client import ClientBookingSG, ClientReviewSG
from app.config.settings import settings
from app.database.models.appointment import AppointmentStatus
from app.database.models.review import Review
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.service_repository import ServiceRepository
from app.services.booking_service import BookingService
from app.services.crm_service import MasterCrmService
from app.services.slot_engine import SlotEngine
from app.utils.formatters import format_duration, format_rub, render_appointment_card

from app.bot.handlers.client.presentation import project_menu

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
        master_settings = await settings_repo.get_by_master_id(master_id)
        if (
            master_settings
            and master_settings.bank_name and master_settings.bank_name.strip()
            and master_settings.bank_card_number and master_settings.bank_card_number.strip()
            and master_settings.bank_recipient_name and master_settings.bank_recipient_name.strip()
        ):
            card_text += (
                "\n\n<b>Реквизиты для предоплаты:</b>\n"
                f"🏦 {escape(master_settings.bank_name.strip())} | "
                f"💳 <code>{escape(master_settings.bank_card_number.strip())}</code>\n"
                f"👤 Получатель: {escape(master_settings.bank_recipient_name.strip())}"
            )
            markup = get_payment_screen_keyboard(appointment.id)
        else:
            card_text += (
                "\n\n⚠️ Предоплата временно недоступна. "
                "Свяжитесь с мастером, чтобы уточнить запись."
            )
            markup = None
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
        await callback.message.edit_text(text=text, reply_markup=await project_menu(session, master_id))
    await callback.answer()


# ---------------------------------------------------------------------------
# REPEAT BOOKING & REVIEWS
# ---------------------------------------------------------------------------


@router.callback_query(BookingActionCallback.filter(F.action == "repeat"))
async def cb_repeat_booking(
    callback: CallbackQuery,
    callback_data: BookingActionCallback,
    db_user: User,
    session: AsyncSession,
    master_id: int,
    state: FSMContext,
) -> None:
    """Initiate repeat booking for previous service, suggesting nearest available dates."""
    appointment_repo = AppointmentRepository(session)
    appointment = await appointment_repo.get_by_id_with_relations(
        appointment_id=callback_data.appointment_id, master_id=master_id
    )
    if not appointment or appointment.user_id != db_user.id:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(appointment.service_id, master_id=master_id)
    if not service or not service.is_active or service.is_archived:
        # Suggest browsing active services
        services = await service_repo.list_active(master_id=master_id)
        text = (
            "⚠️ Выбранная ранее услуга сейчас недоступна для записи.\n"
            "Пожалуйста, выберите подходящую услугу из каталога:"
        )
        await state.set_state(ClientBookingSG.choosing_service)
        if callback.message:
            await callback.message.edit_text(
                text=text, reply_markup=get_services_list_keyboard(services, is_booking_flow=True)
            )
        await callback.answer()
        return

    # Set up booking state for this service
    await state.clear()
    await state.update_data(service_id=service.id)
    await state.set_state(ClientBookingSG.choosing_date)

    slot_engine = SlotEngine(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()

    available_dates_list = await slot_engine.get_available_dates(
        service_id=service.id, start_date=today, days_count=35, master_id=master_id
    )
    available_dates = set(available_dates_list)

    text = (
        f"🔁 <b>Повторная запись:</b> {escape(service.title)}\n"
        f"⏳ <b>Длительность:</b> {format_duration(service.duration_min)}\n"
        f"💰 <b>Стоимость:</b> {format_rub(service.price)}\n\n"
        "Выберите удобную дату в календаре (доступные дни отмечены числами):"
    )
    calendar_markup = build_inline_calendar(
        year=today.year,
        month=today.month,
        available_dates=available_dates,
        today=today,
        service_id=service.id,
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=calendar_markup)
    await callback.answer()


@router.callback_query(BookingActionCallback.filter(F.action == "review"))
async def cb_start_review(
    callback: CallbackQuery,
    callback_data: BookingActionCallback,
    db_user: User,
    session: AsyncSession,
    master_id: int,
) -> None:
    """Prompt client to select rating (1 to 5 stars)."""
    app_id = callback_data.appointment_id
    appointment_repo = AppointmentRepository(session)
    appointment = await appointment_repo.get_by_id_with_relations(appointment_id=app_id, master_id=master_id)
    if not appointment or appointment.user_id != db_user.id:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    # Check if already reviewed
    existing = await session.scalar(select(Review).where(Review.appointment_id == app_id))
    if existing:
        stars_txt = "⭐" * existing.rating
        await callback.answer(f"Вы уже оценили этот визит на {stars_txt}!", show_alert=True)
        return

    text = (
        f"⭐ <b>Оценка визита #{app_id}</b>\n\n"
        f"Услуга: <b>{escape(appointment.snapshot_service_title)}</b>\n\n"
        "Пожалуйста, оцените качество обслуживания от 1 до 5 звёзд:"
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=get_review_rating_keyboard(app_id))
    await callback.answer()


@router.callback_query(F.data.startswith("rev:star:"))
async def cb_review_star_selected(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    """Rating selected, prompt client for optional comment."""
    parts = callback.data.split(":")
    app_id = int(parts[2])
    rating = int(parts[3])

    # Check existing
    existing = await session.scalar(select(Review).where(Review.appointment_id == app_id))
    if existing:
        await callback.answer("Отзыв к этой записи уже оставлен.", show_alert=True)
        return

    await state.set_state(ClientReviewSG.waiting_for_comment)
    await state.update_data(master_id=master_id, appointment_id=app_id, rating=rating)

    stars_str = "⭐" * rating
    text = (
        f"Вы выбрали оценку: <b>{stars_str} ({rating}/5)</b>\n\n"
        "Хотите оставить отзыв или пожелание мастеру? "
        "Напишите текст сообщением в чат или нажмите кнопку «Без комментария»."
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=get_review_skip_keyboard(app_id, rating))
    await callback.answer()


@router.callback_query(F.data.startswith("rev:skip:"))
async def cb_review_skip_comment(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    """Save review without comment."""
    await state.clear()
    parts = callback.data.split(":")
    app_id = int(parts[2])
    rating = int(parts[3])

    crm_svc = MasterCrmService(session)
    try:
        await crm_svc.create_review(
            master_id=master_id,
            user_id=db_user.id,
            appointment_id=app_id,
            rating=rating,
            comment=None,
        )
        text = (
            "🎉 <b>Спасибо за вашу оценку!</b>\n\n"
            "Ваш отзыв успешно сохранён и помогает мастеру развиваться."
        )
    except Exception as exc:
        text = f"Не удалось сохранить отзыв: {exc}"

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=await project_menu(session, master_id))
    await callback.answer()


@router.message(ClientReviewSG.waiting_for_comment)
async def msg_review_save_comment(
    message: Message, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    """Save review with client comment."""
    data = await state.get_data()
    app_id = data.get("appointment_id")
    rating = data.get("rating")
    await state.clear()

    if not app_id or not rating:
        await message.answer("Сессия истекла.", reply_markup=await project_menu(session, master_id))
        return

    comment_text = (message.text or "").strip()
    crm_svc = MasterCrmService(session)
    try:
        await crm_svc.create_review(
            master_id=master_id,
            user_id=db_user.id,
            appointment_id=app_id,
            rating=rating,
            comment=comment_text,
        )
        text = (
            "🎉 <b>Спасибо за ваш отзыв!</b>\n\n"
            "Мастер обязательно прочтёт ваш комментарий. До новых встреч!"
        )
    except Exception as exc:
        text = f"Не удалось сохранить отзыв: {exc}"

    await message.answer(text, reply_markup=await project_menu(session, master_id))
