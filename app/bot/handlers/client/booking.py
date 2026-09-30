"""
Client online booking flow handlers (service -> calendar -> slots -> phone -> policy -> hold).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import pytz
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import (
    BookingActionCallback,
    CalendarNavCallback,
    MenuCallback,
    ServiceCallback,
    TimeSlotCallback,
    build_inline_calendar,
    build_time_slots_keyboard,
    get_main_menu_keyboard,
    get_payment_screen_keyboard,
    get_phone_request_keyboard,
    get_policy_agreement_keyboard,
    get_services_list_keyboard,
)
from app.bot.states.client import ClientBookingSG
from app.config.settings import settings
from app.database.models.master import BotInstance, BotInstanceStatus
from app.database.models.user import User
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.user_repository import UserRepository
from app.services.booking_service import BookingService
from app.services.exceptions import SlotAlreadyBookedError
from app.services.slot_engine import SlotEngine
from app.services.tenant_context import LegacyTenantResolver
from app.utils.formatters import (
    format_date_ru,
    format_datetime_ru,
    format_duration,
    format_rub,
)

router = Router(name="client_booking")


@router.callback_query(MenuCallback.filter(F.action == "book"))
async def cb_start_booking(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    master_id: Optional[int] = None,
    bot_instance: Optional[BotInstance] = None,
    is_admin: bool = False,
) -> None:
    """
    Start booking flow: prompt client to choose a service.
    Gates clients when bot is in SETUP_REQUIRED status.
    """
    if bot_instance and bot_instance.status == BotInstanceStatus.SETUP_REQUIRED and not is_admin:
        await callback.answer(
            "💅 Онлайн-запись к мастеру пока настраивается. Пожалуйста, загляните позже!",
            show_alert=True,
        )
        return

    await state.clear()
    if master_id is None:
        master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    services = await service_repo.list_active(master_id=master_id)

    if not services:
        text = "К сожалению, в данный момент онлайн-запись временно недоступна. Пожалуйста, свяжитесь с мастером напрямую 🌸"
        if callback.message:
            await callback.message.edit_text(text=text)
        await callback.answer()
        return

    text = "<b>📅 Онлайн-запись</b>\n\nВыберите услугу, на которую хотите записаться:"
    await state.set_state(ClientBookingSG.choosing_service)

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_services_list_keyboard(services, is_booking_flow=True),
        )
    await callback.answer()


@router.callback_query(ServiceCallback.filter(F.action == "select"))
async def cb_service_selected(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """
    Service selected: calculate available calendar days and show inline calendar.
    """
    service_id = callback_data.service_id
    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(service_id, master_id=master_id)

    if not service or not service.is_active or service.is_archived:
        await callback.answer("Услуга недоступна", show_alert=True)
        return

    await state.update_data(service_id=service_id)

    # Calculate available dates for the next 30 days
    slot_engine = SlotEngine(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()

    available_dates_list = await slot_engine.get_available_dates(
        service_id=service_id, start_date=today, days_count=35, master_id=master_id
    )
    available_dates = set(available_dates_list)

    await state.set_state(ClientBookingSG.choosing_date)

    text = (
        f"🌸 <b>Услуга:</b> {service.title}\n"
        f"⏳ <b>Длительность:</b> {format_duration(service.duration_min)}\n"
        f"💰 <b>Стоимость:</b> {format_rub(service.price)}\n\n"
        "Выберите удобную дату в календаре (доступные дни отмечены числами):"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_inline_calendar(
                year=today.year,
                month=today.month,
                available_dates=available_dates,
                today=today,
                service_id=service_id,
            ),
        )
    await callback.answer()


@router.callback_query(CalendarNavCallback.filter())
async def cb_calendar_navigation(
    callback: CallbackQuery,
    callback_data: CalendarNavCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """
    Handle calendar month paging and day selection.
    """
    action = callback_data.action
    if action == "ignore":
        await callback.answer()
        return

    data = await state.get_data()
    service_id = data.get("service_id")
    if not service_id:
        await callback.answer("Сессия устарела. Пожалуйста, начните заново.", show_alert=True)
        return

    master_id = await LegacyTenantResolver.get_master_id(session)
    slot_engine = SlotEngine(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()

    if action in ["prev_month", "next_month"]:
        # Month switch
        available_dates_list = await slot_engine.get_available_dates(
            service_id=service_id, start_date=today, days_count=45, master_id=master_id
        )
        available_dates = set(available_dates_list)

        service_repo = ServiceRepository(session)
        service = await service_repo.get_by_id(service_id, master_id=master_id)

        text = (
            f"🌸 <b>Услуга:</b> {service.title}\n\n"
            "Выберите удобную дату в календаре:"
        )

        if callback.message:
            await callback.message.edit_text(
                text=text,
                reply_markup=build_inline_calendar(
                    year=callback_data.year,
                    month=callback_data.month,
                    available_dates=available_dates,
                    today=today,
                    service_id=service_id,
                ),
            )
        await callback.answer()
        return

    if action == "select_day":
        if callback_data.day == 0:
            # Re-render calendar
            available_dates_list = await slot_engine.get_available_dates(
                service_id=service_id, start_date=today, days_count=35, master_id=master_id
            )
            available_dates = set(available_dates_list)
            service_repo = ServiceRepository(session)
            service = await service_repo.get_by_id(service_id, master_id=master_id)

            text = f"🌸 <b>Услуга:</b> {service.title}\n\nВыберите дату:"
            if callback.message:
                await callback.message.edit_text(
                    text=text,
                    reply_markup=build_inline_calendar(
                        year=callback_data.year,
                        month=callback_data.month,
                        available_dates=available_dates,
                        today=today,
                        service_id=service_id,
                    ),
                )
            await callback.answer()
            return

        # Day chosen! Calculate available slots
        chosen_date = date(callback_data.year, callback_data.month, callback_data.day)
        slots = await slot_engine.get_available_slots(service_id, chosen_date, master_id=master_id)

        await state.update_data(chosen_date_iso=chosen_date.isoformat())
        await state.set_state(ClientBookingSG.choosing_time)

        date_str = format_date_ru(chosen_date)
        text = (
            f"🗓 <b>Выбранная дата:</b> {date_str}\n\n"
            "Выберите подходящее время для начала процедуры:"
        )

        if callback.message:
            await callback.message.edit_text(
                text=text,
                reply_markup=build_time_slots_keyboard(service_id, slots, chosen_date),
            )
        await callback.answer()


@router.callback_query(TimeSlotCallback.filter())
async def cb_slot_selected(
    callback: CallbackQuery,
    callback_data: TimeSlotCallback,
    state: FSMContext,
    db_user: User,
    session: AsyncSession,
) -> None:
    """
    Slot chosen: check if user phone is known. If not, request phone; otherwise show policy agreement.
    """
    slot_ts = callback_data.timestamp
    slot_dt = datetime.fromtimestamp(slot_ts, pytz.UTC)
    await state.update_data(slot_timestamp=slot_ts)

    data = await state.get_data()
    service_id = data.get("service_id") or callback_data.service_id
    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(service_id, master_id=master_id)

    if not db_user.phone:
        # Prompt for phone number using native Telegram contact button
        await state.set_state(ClientBookingSG.entering_phone)
        text = (
            "📱 <b>Контактный телефон</b>\n\n"
            "Для завершения записи и отправки напоминаний, пожалуйста, отправьте ваш номер телефона.\n\n"
            "Нажмите кнопку <b>«Поделиться номером телефона»</b> внизу экрана 👇"
        )
        if callback.message:
            await callback.message.delete()
        await callback.message.answer(
            text=text,
            reply_markup=get_phone_request_keyboard(),
        )
        await callback.answer()
        return

    # User already has phone number registered
    await _show_policy_screen(callback, state, service, slot_dt, session, master_id=master_id)


async def _show_policy_screen(
    callback: CallbackQuery,
    state: FSMContext,
    service,
    slot_dt: datetime,
    session: AsyncSession,
    master_id: int,
) -> None:
    """
    Render confirmation screen with cancellation and deposit policy.
    """
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(slot_dt, tz_name=tz_str)
    dur_str = format_duration(service.duration_min)
    price_str = format_rub(service.price)

    if service.deposit_type.value == "PERCENT":
        deposit_amount = (service.price * service.deposit_value / Decimal("100")).quantize(Decimal("1.00"))
    else:
        deposit_amount = service.deposit_value.quantize(Decimal("1.00"))
    dep_str = format_rub(deposit_amount)

    text = (
        "<b>📋 Проверьте данные вашей записи</b>\n\n"
        f"🌸 <b>Услуга:</b> {service.title}\n"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"⏳ <b>Длительность:</b> {dur_str}\n"
        f"💰 <b>Стоимость услуги:</b> {price_str}\n"
        f"💳 <b>Предоплата:</b> {dep_str}\n\n"
        "⚠️ <b>ВАЖНОЕ ПРАВИЛО:</b>\n"
        "<i>Для подтверждения записи требуется внесение предоплаты. "
        "В случае отмены записи клиентом внесённая предоплата не возвращается.</i>\n\n"
        "Подтверждая запись, вы соглашаетесь с данным условием."
    )

    await state.set_state(ClientBookingSG.confirming_policy)

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_policy_agreement_keyboard(),
        )
    await callback.answer()


@router.message(ClientBookingSG.entering_phone, F.contact)
@router.message(ClientBookingSG.entering_phone, F.text)
async def msg_receive_phone(
    message: Message,
    state: FSMContext,
    db_user: User,
    session: AsyncSession,
) -> None:
    """
    Handle contact sharing or manual phone number input.
    """
    if message.contact:
        phone = message.contact.phone_number
    else:
        text_phone = message.text.strip()
        # Basic validation: digits and '+'
        digits = "".join(ch for ch in text_phone if ch.isdigit() or ch == "+")
        if len(digits) < 10:
            await message.answer(
                "Пожалуйста, введите корректный номер телефона (например, +79991234567) "
                "или нажмите кнопку «Поделиться номером телефона» 👇",
                reply_markup=get_phone_request_keyboard(),
            )
            return
        phone = digits

    if not phone.startswith("+") and len(phone) == 11 and phone.startswith("7"):
        phone = "+" + phone

    user_repo = UserRepository(session)
    await user_repo.update_phone(db_user.id, phone)
    db_user.phone = phone

    # Remove reply keyboard
    await message.answer("Спасибо! Номер телефона сохранён ✅", reply_markup=ReplyKeyboardRemove())

    # Proceed to policy confirmation
    data = await state.get_data()
    service_id = data.get("service_id")
    slot_ts = data.get("slot_timestamp")

    if not service_id or not slot_ts:
        await message.answer(
            "Время ожидания истекло. Пожалуйста, начните запись заново через меню.",
            reply_markup=get_main_menu_keyboard(),
        )
        await state.clear()
        return

    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(service_id, master_id=master_id)
    slot_dt = datetime.fromtimestamp(slot_ts, pytz.UTC)

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(slot_dt, tz_name=tz_str)
    dur_str = format_duration(service.duration_min)
    price_str = format_rub(service.price)

    if service.deposit_type.value == "PERCENT":
        deposit_amount = (service.price * service.deposit_value / Decimal("100")).quantize(Decimal("1.00"))
    else:
        deposit_amount = service.deposit_value.quantize(Decimal("1.00"))
    dep_str = format_rub(deposit_amount)

    text = (
        "<b>📋 Проверьте данные вашей записи</b>\n\n"
        f"🌸 <b>Услуга:</b> {service.title}\n"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"⏳ <b>Длительность:</b> {dur_str}\n"
        f"💰 <b>Стоимость услуги:</b> {price_str}\n"
        f"💳 <b>Предоплата:</b> {dep_str}\n\n"
        "⚠️ <b>ВАЖНОЕ ПРАВИЛО:</b>\n"
        "<i>Для подтверждения записи требуется внесение предоплаты. "
        "В случае отмены записи клиентом внесённая предоплата не возвращается.</i>\n\n"
        "Подтверждая запись, вы соглашаетесь с данным условием."
    )

    await state.set_state(ClientBookingSG.confirming_policy)
    await message.answer(
        text=text,
        reply_markup=get_policy_agreement_keyboard(),
    )


@router.callback_query(BookingActionCallback.filter(F.action == "cancel_policy"))
async def cb_cancel_policy(
    callback: CallbackQuery, state: FSMContext, is_admin: bool
) -> None:
    """
    Cancel booking on policy screen.
    """
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            "Запись отменена ↩️ Вы вернулись в главное меню.",
            reply_markup=get_main_menu_keyboard(is_admin=is_admin),
        )
    await callback.answer()


@router.callback_query(BookingActionCallback.filter(F.action == "agree_policy"))
async def cb_agree_policy(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
    session: AsyncSession,
) -> None:
    """
    Client agreed to policy: create hold booking, create pending payment and show requisites.
    """
    data = await state.get_data()
    service_id = data.get("service_id")
    slot_ts = data.get("slot_timestamp")

    if not service_id or not slot_ts:
        await callback.answer("Сессия истекла. Начните запись заново.", show_alert=True)
        await state.clear()
        return

    slot_dt = datetime.fromtimestamp(slot_ts, pytz.UTC)
    booking_service = BookingService(session)
    master_id = await LegacyTenantResolver.get_master_id(session)

    try:
        appointment, payment = await booking_service.create_hold_booking(
            master_id=master_id,
            user_id=db_user.id,
            service_id=service_id,
            start_time=slot_dt,
            cancel_policy_agreed=True,
        )
    except SlotAlreadyBookedError:
        await callback.answer(
            "Ой! Кто-то успел занять это время прямо перед вами. Пожалуйста, выберите другое время 🌸",
            show_alert=True,
        )
        return

    await state.clear()

    # Fetch requisites from settings
    settings_repo = MasterSettingsRepository(session)
    bank_name = await settings_repo.get_value(master_id, "bank_name", settings.bank_name)
    card_number = await settings_repo.get_value(master_id, "bank_card_number", settings.bank_card_number)
    phone_req = await settings_repo.get_value(master_id, "default_phone_requisites", settings.default_phone_requisites)
    recipient = await settings_repo.get_value(master_id, "bank_recipient_name", settings.bank_recipient_name)
    hold_mins = int(await settings_repo.get_value(master_id, "hold_duration_minutes", settings.hold_duration_minutes))

    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)
    deposit_str = format_rub(appointment.snapshot_deposit_amount)

    text = (
        f"🎉 <b>Слот успешно зарезервирован! (Запись #{appointment.id})</b>\n\n"
        f"🌸 <b>Услуга:</b> {appointment.snapshot_service_title}\n"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"💳 <b>Предоплата к переводу:</b> {deposit_str}\n\n"
        f"⏳ <i>У вас есть {hold_mins} минут для внесения предоплаты. По истечении этого времени бронь аннулируется.</i>\n\n"
        "<b>Реквизиты для перевода:</b>\n"
        f"🏦 <b>Банк:</b> {bank_name}\n"
        f"💳 <b>Номер карты:</b> <code>{card_number}</code>\n"
        f"📱 <b>По номеру телефона (СБП):</b> <code>{phone_req}</code>\n"
        f"👤 <b>Получатель:</b> {recipient}\n\n"
        "После перевода нажмите <b>«Я оплатил(а)»</b> и отправьте фото чека или скриншот:"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_payment_screen_keyboard(appointment.id),
        )
    await callback.answer()
