"""
Admin handlers for appointment management, filtering, details, rescheduling, and status updates.
"""

from datetime import date, datetime, time, timedelta
from typing import List
import pytz
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin.appointments import (
    get_admin_appointment_card_keyboard,
    get_admin_appointment_list_keyboard,
    get_appointments_filters_keyboard,
)
from app.bot.keyboards.admin.callbacks import (
    AdminAppointmentCallback,
    AdminCalendarCallback,
    AdminMenuCallback,
)
from app.bot.keyboards.admin.schedule import get_admin_calendar_keyboard
from app.bot.states.admin import AdminAppointmentNoteSG, AdminRescheduleSG
from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.user_repository import UserRepository
from app.services.booking_service import BookingService
from app.services.exceptions import BookingNotFoundError, InvalidBookingStatusError, SlotAlreadyBookedError
from app.services.master_authorization_service import MasterAuthorizationService
from app.services.slot_engine import SlotEngine
from app.services.tenant_context import LegacyTenantResolver
from app.utils.formatters import format_datetime_ru, format_rub, format_time_ru

router = Router(name="admin_appointments")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


def format_appointment_card(appointment: Appointment, tz_name: str) -> str:
    """
    Format complete appointment details for admin card view.
    """
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_name)
    end_time_str = format_time_ru(appointment.end_time, tz_name=tz_name)
    user = appointment.user

    client_name = user.first_name if user else "Клиент"
    if user and user.last_name:
        client_name += f" {user.last_name}"
    phone_str = user.phone if (user and user.phone) else "не указан"
    username_str = f"@{user.username}" if (user and user.username) else "нет"

    # Status emoji & text
    status_map = {
        AppointmentStatus.CONFIRMED: "🟢 Подтверждена",
        AppointmentStatus.WAITING_PAYMENT: "⏳ Ожидает оплаты",
        AppointmentStatus.PAYMENT_PROOF_SENT: "🔎 Чек на проверке",
        AppointmentStatus.COMPLETED: "🎉 Выполнена",
        AppointmentStatus.CANCELLED_BY_CLIENT: "❌ Отменена клиентом",
        AppointmentStatus.CANCELLED_BY_ADMIN: "❌ Отменена мастером",
        AppointmentStatus.NO_SHOW: "🚫 Не пришёл (NO-SHOW)",
        AppointmentStatus.EXPIRED: "⌛️ Время брони истекло",
    }
    status_label = status_map.get(appointment.status, appointment.status.value)

    card = (
        f"<b>📋 Запись #{appointment.id}</b>\n\n"
        f"<b>Статус:</b> {status_label}\n\n"
        f"👤 <b>Клиент:</b> {client_name}\n"
        f"📞 <b>Телефон:</b> {phone_str}\n"
        f"💬 <b>Telegram:</b> {username_str}\n\n"
        f"🌸 <b>Услуга:</b> {appointment.snapshot_service_title}\n"
        f"🗓 <b>Дата и время:</b> {dt_str} — {end_time_str}\n"
        f"⏱ <b>Длительность:</b> {appointment.snapshot_service_duration_min} мин. "
        f"(буфер: {appointment.snapshot_buffer_duration_min} мин.)\n"
        f"💰 <b>Стоимость:</b> {format_rub(appointment.snapshot_service_price)}\n"
        f"💳 <b>Предоплата:</b> {format_rub(appointment.snapshot_deposit_amount)}\n"
    )

    if appointment.cancel_reason:
        card += f"\n❌ <b>Причина отмены:</b> {appointment.cancel_reason}\n"

    if appointment.admin_notes:
        card += f"\n📝 <b>Заметка мастера:</b> {appointment.admin_notes}\n"

    return card


@router.callback_query(AdminMenuCallback.filter(F.action == "appointments"))
async def cb_appointments_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Submenu for selecting appointment filter category.
    """
    await state.clear()
    text = (
        "<b>📅 Управление записями клиентов</b>\n\n"
        "Выберите категорию для просмотра:"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text, reply_markup=get_appointments_filters_keyboard()
        )
    await callback.answer()


@router.callback_query(AdminAppointmentCallback.filter(F.action == "list"))
async def cb_appointments_list(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    session: AsyncSession,
) -> None:
    """
    Show filtered list of appointments.
    """
    filter_type = callback_data.filter_type or "today"
    master_id = await LegacyTenantResolver.get_master_id(session)
    app_repo = AppointmentRepository(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    now_local = datetime.now(tz)
    today = now_local.date()

    appointments: List[Appointment] = []
    filter_title = ""

    if filter_type == "today":
        filter_title = "🌅 Записи на сегодня"
        start_dt = tz.localize(datetime.combine(today, time.min))
        end_dt = tz.localize(datetime.combine(today, time.max))
        appointments = list(await app_repo.list_for_admin(master_id=master_id, date_from=start_dt, date_to=end_dt))
    elif filter_type == "tomorrow":
        filter_title = "🌄 Записи на завтра"
        tomorrow = today + timedelta(days=1)
        start_dt = tz.localize(datetime.combine(tomorrow, time.min))
        end_dt = tz.localize(datetime.combine(tomorrow, time.max))
        appointments = list(await app_repo.list_for_admin(master_id=master_id, date_from=start_dt, date_to=end_dt))
    elif filter_type == "pending_proof":
        filter_title = "🔎 Записи, ожидающие проверки чека"
        appointments = list(
            await app_repo.list_for_admin(master_id=master_id, status=AppointmentStatus.PAYMENT_PROOF_SENT)
        )
    elif filter_type == "pending_payment":
        filter_title = "⏳ Записи, ожидающие оплаты"
        appointments = list(
            await app_repo.list_for_admin(master_id=master_id, status=AppointmentStatus.WAITING_PAYMENT)
        )
    elif filter_type == "upcoming":
        filter_title = "🔜 Все предстоящие записи"
        start_dt = now_local
        end_dt = now_local + timedelta(days=60)
        all_future = await app_repo.list_for_admin(master_id=master_id, date_from=start_dt, date_to=end_dt)
        appointments = [
            a
            for a in all_future
            if a.status
            in [
                AppointmentStatus.CONFIRMED,
                AppointmentStatus.PAYMENT_PROOF_SENT,
                AppointmentStatus.WAITING_PAYMENT,
            ]
        ]
    elif filter_type == "completed":
        filter_title = "🎉 Завершённые записи"
        appointments = list(
            await app_repo.list_for_admin(master_id=master_id, status=AppointmentStatus.COMPLETED, limit=30)
        )
    elif filter_type == "cancelled":
        filter_title = "❌ Отменённые записи"
        all_apps = await app_repo.list_for_admin(master_id=master_id, limit=50)
        appointments = [
            a
            for a in all_apps
            if a.status
            in [
                AppointmentStatus.CANCELLED_BY_CLIENT,
                AppointmentStatus.CANCELLED_BY_ADMIN,
                AppointmentStatus.NO_SHOW,
                AppointmentStatus.EXPIRED,
            ]
        ]

    if not appointments:
        text = f"<b>{filter_title}</b>\n\nЗаписей в этой категории не найдено."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ К фильтрам записей",
                        callback_data=AdminMenuCallback(action="appointments").pack(),
                    )
                ]
            ]
        )
    else:
        text = f"<b>{filter_title} ({len(appointments)} шт.)</b>\n\nВыберите запись:"
        keyboard = get_admin_appointment_list_keyboard(appointments, filter_type=filter_type)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminAppointmentCallback.filter(F.action == "detail"))
async def cb_appointment_detail(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    session: AsyncSession,
) -> None:
    """
    Show full appointment details card with management actions.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    app_repo = AppointmentRepository(session)
    appointment = await app_repo.get_by_id_with_relations(callback_data.appointment_id, master_id=master_id)

    if not appointment:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)

    text = format_appointment_card(appointment, tz_name=tz_str)
    keyboard = get_admin_appointment_card_keyboard(
        appointment, filter_type=callback_data.filter_type or "today"
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminAppointmentCallback.filter(F.action == "complete"))
async def cb_appointment_complete(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    session: AsyncSession,
) -> None:
    """
    Mark appointment as completed.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    booking_service = BookingService(session)
    try:
        appointment = await booking_service.complete_booking(
            master_id=master_id, appointment_id=callback_data.appointment_id
        )
    except (BookingNotFoundError, InvalidBookingStatusError) as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    text = format_appointment_card(appointment, tz_name=tz_str)
    keyboard = get_admin_appointment_card_keyboard(
        appointment, filter_type=callback_data.filter_type or "today"
    )
    await session.commit()

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer("Запись успешно отмечена как выполненная! 🎉", show_alert=True)


@router.callback_query(AdminAppointmentCallback.filter(F.action == "no_show"))
async def cb_appointment_no_show(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    session: AsyncSession,
) -> None:
    """
    Mark appointment as NO-SHOW.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    booking_service = BookingService(session)
    try:
        appointment = await booking_service.mark_no_show(
            master_id=master_id, appointment_id=callback_data.appointment_id
        )
    except (BookingNotFoundError, InvalidBookingStatusError) as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    text = format_appointment_card(appointment, tz_name=tz_str)
    keyboard = get_admin_appointment_card_keyboard(
        appointment, filter_type=callback_data.filter_type or "today"
    )
    await session.commit()

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer(
        "Клиент отмечен как NO-SHOW. Предоплата удержана.", show_alert=True
    )


@router.callback_query(AdminAppointmentCallback.filter(F.action == "cancel"))
async def cb_appointment_cancel(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    bot: Bot,
    session: AsyncSession,
) -> None:
    """
    Cancel appointment by admin and notify client.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    booking_service = BookingService(session)
    try:
        appointment = await booking_service.cancel_booking_by_admin(
            master_id=master_id,
            appointment_id=callback_data.appointment_id,
            reason="Отменено мастером через панель управления",
        )
    except (BookingNotFoundError, InvalidBookingStatusError) as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)
    await session.commit()

    # Notify client if Telegram ID is available
    if appointment.user and appointment.user.telegram_id:
        try:
            client_msg = (
                f"⚠️ <b>Ваша запись отменена мастером</b>\n\n"
                f"🌸 <b>Услуга:</b> {appointment.snapshot_service_title}\n"
                f"🗓 <b>Дата и время:</b> {dt_str}\n\n"
                "Если у вас возникли вопросы или вы хотите перенести визит на другое время, "
                "пожалуйста, свяжитесь с мастером 🌸"
            )
            await bot.send_message(
                chat_id=appointment.user.telegram_id, text=client_msg
            )
        except Exception:
            pass

    text = format_appointment_card(appointment, tz_name=tz_str)
    keyboard = get_admin_appointment_card_keyboard(
        appointment, filter_type=callback_data.filter_type or "today"
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer("Запись отменена. Клиент уведомлён ❌", show_alert=True)


# --- Rescheduling Wizard ---


@router.callback_query(AdminAppointmentCallback.filter(F.action == "reschedule"))
async def cb_appointment_reschedule_start(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """
    Start rescheduling appointment: pick target date.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    app_repo = AppointmentRepository(session)
    appointment = await app_repo.get_by_id(callback_data.appointment_id, master_id=master_id)
    if not appointment:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    await state.set_state(AdminRescheduleSG.picking_date)
    await state.update_data(
        appointment_id=appointment.id,
        service_id=appointment.service_id,
        filter_type=callback_data.filter_type,
    )

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()

    text = (
        f"<b>🗓 Перенос записи #{appointment.id}</b>\n\n"
        f"Услуга: <b>{appointment.snapshot_service_title}</b>\n"
        f"Текущее время: <b>{format_datetime_ru(appointment.start_time, tz_name=tz_str)}</b>\n\n"
        "Выберите новую дату для переноса:"
    )
    keyboard = get_admin_calendar_keyboard(year=today.year, month=today.month, today=today)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(
    AdminRescheduleSG.picking_date,
    AdminCalendarCallback.filter(F.action == "month"),
)
async def cb_reschedule_month_nav(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Page the admin calendar while keeping the reschedule wizard active."""
    selected = AdminCalendarCallback.unpack(callback.data)
    master_id = await LegacyTenantResolver.get_master_id(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    today = datetime.now(pytz.timezone(tz_str)).date()
    keyboard = get_admin_calendar_keyboard(selected.year, selected.month, today)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(
    AdminRescheduleSG.picking_date,
    AdminCalendarCallback.filter(F.action == "day"),
)
async def cb_reschedule_pick_date(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """
    Date chosen for reschedule: compute available slots and show time grid.
    """
    selected = AdminCalendarCallback.unpack(callback.data)
    target_date = date(selected.year, selected.month, selected.day)

    data = await state.get_data()
    service_id = data["service_id"]
    appointment_id = data["appointment_id"]
    master_id = await LegacyTenantResolver.get_master_id(session)
    appointment = await AppointmentRepository(session).get_by_id(appointment_id, master_id=master_id)
    if appointment is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    slot_engine = SlotEngine(session)
    slots = await slot_engine.get_available_slots(
        service_id=service_id,
        target_date=target_date,
        master_id=appointment.master_id,
        duration_min=appointment.snapshot_service_duration_min,
        buffer_min=appointment.snapshot_buffer_duration_min,
        exclude_appointment_id=appointment.id,
        allow_inactive=True,
    )

    if not slots:
        await callback.answer(
            f"На {target_date.strftime('%d.%m')} нет свободных слотов. Выберите другой день.",
            show_alert=True,
        )
        return

    await state.set_state(AdminRescheduleSG.picking_time)
    await state.update_data(reschedule_date=target_date.isoformat())

    # Build slots keyboard
    buttons = []
    row = []
    for slot in slots:
        slot_str = slot.strftime("%H:%M")
        row.append(
            InlineKeyboardButton(
                text=slot_str,
                callback_data=f"adm_resched_slot:{slot.isoformat()}",
            )
        )
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="◀️ Назад к календарю",
            callback_data=AdminAppointmentCallback(
                action="reschedule", appointment_id=appointment_id
            ).pack(),
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = (
        f"<b>🗓 Перенос записи #{appointment_id}</b>\n\n"
        f"Дата: <b>{target_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Выберите новое свободное время:"
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(
    AdminRescheduleSG.picking_time,
    F.data.startswith("adm_resched_slot:"),
)
async def cb_reschedule_confirm_slot(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    db_user: User,
) -> None:
    """
    Time chosen: perform reschedule, notify client, clear state.
    """
    new_slot_iso = callback.data.split(":", 1)[1]
    new_start_time = datetime.fromisoformat(new_slot_iso)

    data = await state.get_data()
    appointment_id = data["appointment_id"]
    master_id = await LegacyTenantResolver.get_master_id(session)
    auth_service = MasterAuthorizationService(session)
    if not await auth_service.is_admin(master_id, db_user.id):
        await callback.answer("Доступ запрещен", show_alert=True)
        return
    admin = await UserRepository(session).get_or_create_legacy_admin(db_user.id)

    booking_service = BookingService(session)
    try:
        appointment = await booking_service.reschedule_booking_by_admin(
            master_id=master_id,
            appointment_id=appointment_id,
            new_start_time=new_start_time,
            admin_id=admin.id,
        )
    except (SlotAlreadyBookedError, InvalidBookingStatusError) as e:
        await callback.answer(str(e), show_alert=True)
        return

    await state.clear()

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)

    # Notify client
    if appointment.user and appointment.user.telegram_id:
        try:
            client_msg = (
                f"🗓 <b>Ваша запись перенесена мастером!</b>\n\n"
                f"🌸 <b>Услуга:</b> {appointment.snapshot_service_title}\n"
                f"⏰ <b>Новая дата и время:</b> {dt_str}\n\n"
                "Ждём вас в назначенное время! ❤️"
            )
            await bot.send_message(
                chat_id=appointment.user.telegram_id, text=client_msg
            )
        except Exception:
            pass

    text = format_appointment_card(appointment, tz_name=tz_str)
    keyboard = get_admin_appointment_card_keyboard(appointment, filter_type=filter_type)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer("Запись успешно перенесена! Клиент уведомлён ✅", show_alert=True)


# --- Appointment Internal Note ---


@router.callback_query(AdminAppointmentCallback.filter(F.action == "note"))
async def cb_appointment_add_note(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    state: FSMContext,
) -> None:
    """
    Prompt admin to enter an internal note for the appointment.
    """
    await state.set_state(AdminAppointmentNoteSG.entering_note)
    await state.update_data(
        appointment_id=callback_data.appointment_id,
        filter_type=callback_data.filter_type,
    )

    text = (
        f"<b>📝 Заметка к записи #{callback_data.appointment_id}</b>\n\n"
        "Отправьте текст внутренней заметки (видна только мастеру):\n"
        "<i>Например: Сложный дизайн френч, не любит кофе, аллергия на ромашку</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data=AdminAppointmentCallback(
                        action="detail",
                        appointment_id=callback_data.appointment_id,
                        filter_type=callback_data.filter_type,
                    ).pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminAppointmentNoteSG.entering_note, F.text)
async def msg_appointment_save_note(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """
    Save internal master note and show updated appointment card.
    """
    data = await state.get_data()
    appointment_id = data["appointment_id"]
    filter_type = data.get("filter_type", "today")
    await state.clear()

    master_id = await LegacyTenantResolver.get_master_id(session)
    app_repo = AppointmentRepository(session)
    appointment = await app_repo.get_by_id_with_relations(appointment_id, master_id=master_id)
    if appointment:
        appointment.admin_notes = message.text.strip()
        await session.flush()

        settings_repo = MasterSettingsRepository(session)
        tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
        text = (
            "✅ <b>Заметка сохранена!</b>\n\n"
            + format_appointment_card(appointment, tz_name=tz_str)
        )
        keyboard = get_admin_appointment_card_keyboard(
            appointment, filter_type=filter_type
        )
        await message.answer(text=text, reply_markup=keyboard)
    else:
        await message.answer("Запись не найдена.")
