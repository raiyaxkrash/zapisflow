"""
Admin manual booking wizard for walk-ins and phone calls.
Allows creating confirmed appointments directly without deposit checks.
"""

from datetime import date, datetime, time
from uuid import uuid4
from typing import Optional
import pytz
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin.callbacks import (
    AdminAppointmentCallback,
    AdminCalendarCallback,
    AdminMenuCallback,
)
from app.bot.states.admin import AdminManualBookingSG
from app.config.settings import settings
from app.database.models.user import User
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.user_repository import UserRepository
from app.services.booking_service import BookingService
from app.services.exceptions import SlotAlreadyBookedError
from app.services.slot_engine import SlotEngine
from app.utils.formatters import format_datetime_ru, format_rub

router = Router(name="admin_manual_booking")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


@router.callback_query(AdminCalendarCallback.filter(F.action == "manual_book"))
async def cb_manual_booking_start(
    callback: CallbackQuery,
    callback_data: AdminCalendarCallback,
    state: FSMContext,
    session: AsyncSession, master_id: int,
) -> None:
    """
    Step 1: Admin clicks "Записать клиента" for a specific date in calendar.
    Shows list of active services.
    """
    target_date = date(callback_data.year, callback_data.month, callback_data.day)
    await state.clear()
    await state.set_state(AdminManualBookingSG.choosing_service)
    await state.update_data(target_date_iso=target_date.isoformat())

    service_repo = ServiceRepository(session)
    services = await service_repo.list_active(master_id=master_id)

    if not services:
        await callback.answer("Нет активных услуг для записи.", show_alert=True)
        return

    text = (
        f"<b>➕ Ручная запись клиента на {target_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Выберите услугу из списка:"
    )
    buttons = []
    for s in services:
        btn_text = f"{s.title} ({format_rub(s.price)}, {s.duration_min} мин)"
        buttons.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"adm_mb_svc:{s.id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="◀️ Отмена (назад к дню)",
            callback_data=AdminCalendarCallback(
                action="day",
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
            ).pack(),
        )
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(
    AdminManualBookingSG.choosing_service,
    F.data.startswith("adm_mb_svc:"),
)
async def cb_manual_booking_choose_service(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession, master_id: int,
) -> None:
    """
    Step 2: Service chosen. Calculate slots for target date and present time grid.
    """
    service_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    target_date = date.fromisoformat(data["target_date_iso"])

    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(service_id, master_id=master_id)
    if not service:
        await callback.answer("Услуга не найдена", show_alert=True)
        return

    slot_engine = SlotEngine(session)
    slots = await slot_engine.get_available_slots(
        service_id=service_id, target_date=target_date, master_id=master_id
    )

    if not slots:
        await callback.answer(
            f"На {target_date.strftime('%d.%m')} нет свободных слотов для этой услуги.",
            show_alert=True,
        )
        return

    await state.set_state(AdminManualBookingSG.choosing_time)
    await state.update_data(service_id=service.id, service_title=service.title)

    buttons = []
    row = []
    for slot in slots:
        slot_str = slot.strftime("%H:%M")
        row.append(
            InlineKeyboardButton(
                text=slot_str,
                callback_data=f"adm_mb_slot:{slot.isoformat()}",
            )
        )
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="◀️ Назад к выбору услуги",
            callback_data=AdminCalendarCallback(
                action="manual_book",
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
            ).pack(),
        )
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = (
        f"<b>➕ Ручная запись на {target_date.strftime('%d.%m.%Y')}</b>\n"
        f"Услуга: <b>{service.title}</b>\n\n"
        "Выберите свободное время записи:"
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(
    AdminManualBookingSG.choosing_time,
    F.data.startswith("adm_mb_slot:"),
)
async def cb_manual_booking_choose_time(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Step 3: Time slot chosen. Prompt for client name.
    """
    slot_iso = callback.data.split(":", 1)[1]
    await state.update_data(slot_iso=slot_iso)
    await state.set_state(AdminManualBookingSG.client_name)

    slot_dt = datetime.fromisoformat(slot_iso)
    text = (
        f"<b>➕ Запись на {slot_dt.strftime('%d.%m.%Y в %H:%M')}</b>\n\n"
        "Введите <b>имя и фамилию клиента</b>:\n"
        "<i>Например: Екатерина Смирнова (звонок)</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=AdminMenuCallback(action="calendar").pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminManualBookingSG.client_name, F.text)
async def msg_manual_booking_client_name(
    message: Message, state: FSMContext
) -> None:
    """
    Step 4: Client name received. Prompt for phone number with optional skip.
    """
    client_name = message.text.strip()
    await state.update_data(client_name=client_name)
    await state.set_state(AdminManualBookingSG.client_phone)

    text = (
        f"Клиент: <b>{client_name}</b>\n\n"
        "Введите <b>номер телефона</b> клиента (например: +79991234567)\n"
        "или нажмите «Пропустить», если телефона нет:"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить телефон",
                    callback_data="adm_mb:skip_phone",
                )
            ]
        ]
    )
    await message.answer(text=text, reply_markup=keyboard)


@router.callback_query(
    AdminManualBookingSG.client_phone,
    F.data == "adm_mb:skip_phone",
)
async def cb_manual_booking_skip_phone(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """
    Skip phone number step.
    """
    await state.update_data(client_phone=None)
    await state.set_state(AdminManualBookingSG.admin_notes)

    text = (
        "Введите <b>внутреннюю заметку</b> к записи (видна только вам)\n"
        "или нажмите «Пропустить»:"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить заметку",
                    callback_data="adm_mb:skip_notes",
                )
            ]
        ]
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminManualBookingSG.client_phone, F.text)
async def msg_manual_booking_client_phone(
    message: Message, state: FSMContext
) -> None:
    """
    Phone number received. Prompt for internal notes.
    """
    phone = message.text.strip()
    await state.update_data(client_phone=phone)
    await state.set_state(AdminManualBookingSG.admin_notes)

    text = (
        f"Телефон: <b>{phone}</b>\n\n"
        "Введите <b>внутреннюю заметку</b> к записи (видна только вам)\n"
        "или нажмите «Пропустить»:"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить заметку",
                    callback_data="adm_mb:skip_notes",
                )
            ]
        ]
    )
    await message.answer(text=text, reply_markup=keyboard)


@router.callback_query(
    AdminManualBookingSG.admin_notes,
    F.data == "adm_mb:skip_notes",
)
async def cb_manual_booking_skip_notes(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int
) -> None:
    """
    Skip note and finalize appointment creation.
    """
    await finalize_manual_booking(
        event=callback,
        notes=None,
        state=state,
        session=session,
        master_id=master_id,
    )


@router.message(AdminManualBookingSG.admin_notes, F.text)
async def msg_manual_booking_save_notes(
    message: Message, state: FSMContext, session: AsyncSession, master_id: int
) -> None:
    """
    Notes received: finalize appointment creation.
    """
    notes = message.text.strip()
    await finalize_manual_booking(
        event=message,
        notes=notes,
        state=state,
        session=session,
        master_id=master_id,
    )


async def finalize_manual_booking(
    event: Message | CallbackQuery,
    notes: Optional[str],
    state: FSMContext,
    session: AsyncSession, master_id: int,
) -> None:
    """
    Create User profile if needed and record CONFIRMED manual appointment.
    """
    data = await state.get_data()
    service_id = data["service_id"]
    slot_iso = data["slot_iso"]
    client_name = data["client_name"]
    client_phone = data.get("client_phone")
    start_time = datetime.fromisoformat(slot_iso)

    user_repo = UserRepository(session)
    booking_service = BookingService(session)

    # 1. Resolve user
    user = None
    if client_phone:
        user = await user_repo.get_by_phone_exact(client_phone, master_id=master_id)

    if not user:
        # Synthetic negative telegram_id for offline/walk-in clients
        synthetic_tg_id = -(uuid4().int & ((1 << 63) - 1))
        user = User(
            telegram_id=synthetic_tg_id,
            first_name=client_name,
            phone=client_phone,
        )
        session.add(user)
        await session.flush()
        await session.refresh(user)

    # 2. Create manual confirmed appointment
    try:
        appointment, _ = await booking_service.create_hold_booking(
            master_id=master_id,
            user_id=user.id,
            service_id=service_id,
            start_time=start_time,
            cancel_policy_agreed=True,
            is_manual=True,
            admin_notes=notes,
        )
    except SlotAlreadyBookedError as e:
        err_msg = f"⚠️ Ошибка бронирования: {e}"
        if isinstance(event, CallbackQuery):
            await event.answer(err_msg, show_alert=True)
        else:
            await event.answer(err_msg)
        return

    await state.clear()

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)

    success_text = (
        f"✅ <b>Запись успешно создана!</b>\n\n"
        f"<b>Номер записи:</b> #{appointment.id}\n"
        f"<b>Статус:</b> 🟢 Подтверждена (ручная запись)\n"
        f"<b>Клиент:</b> {client_name}\n"
        f"<b>Телефон:</b> {client_phone or 'не указан'}\n"
        f"<b>Услуга:</b> {appointment.snapshot_service_title}\n"
        f"<b>Дата и время:</b> {dt_str}\n"
        f"<b>Стоимость:</b> {format_rub(appointment.snapshot_service_price)}\n"
    )
    if notes:
        success_text += f"<b>Заметка:</b> {notes}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Открыть карточку записи",
                    callback_data=AdminAppointmentCallback(
                        action="detail", appointment_id=appointment.id, filter_type="today"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Вернуться в календарь",
                    callback_data=AdminMenuCallback(action="calendar").pack(),
                )
            ],
        ]
    )

    if isinstance(event, Message):
        await event.answer(text=success_text, reply_markup=keyboard)
    else:
        if event.message:
            await event.message.edit_text(text=success_text, reply_markup=keyboard)
        await event.answer("Запись создана! ✅")
