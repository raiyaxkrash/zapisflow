"""
Client online booking flow handlers (service -> calendar -> slots -> phone -> policy -> hold).
"""

from html import escape
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import logging
from uuid import uuid4
import pytz
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import (
    BookingActionCallback,
    CalendarNavCallback,
    MenuCallback,
    ServiceCallback,
    StaffChoiceCallback,
    TimeSlotCallback,
    build_inline_calendar,
    build_time_slots_keyboard,
    get_main_menu_keyboard,
    get_payment_screen_keyboard,
    get_phone_request_keyboard,
    get_policy_agreement_keyboard,
    get_services_list_keyboard,
    get_staff_selection_keyboard,
)
from app.bot.states.client import ClientBookingSG
from app.bot.keyboards.client.callbacks import PolicyAgreementCallback
from app.config.settings import settings
from app.database.models.master import BotInstance, BotInstanceStatus
from app.database.models.user import User
from app.database.models.telegram_outbox import TelegramOutbox
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.staff_repository import StaffRepository
from app.repositories.user_repository import UserRepository
from app.services.booking_service import BookingService
from app.services.telegram_outbox import (
    bound_bot_instance_id,
    enqueue_telegram_edit,
    enqueue_telegram_message,
)
from app.services.exceptions import (
    PaymentRequisitesMissingError,
    SlotAlreadyBookedError,
    StaffServiceUnavailableError,
    SubscriptionExpiredError,
)
from app.services.slot_engine import SlotEngine
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.utils.formatters import (
    format_date_ru,
    format_datetime_ru,
    format_duration,
    format_rub,
)

router = Router(name="client_booking")
logger = logging.getLogger("app.bot.handlers.client.booking")


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
    Start booking flow.
    If master studio has multiple active specialists -> prompt client to choose staff member.
    If single active specialist -> skip staff selection and go directly to services list.
    """
    if bot_instance and bot_instance.status == BotInstanceStatus.SETUP_REQUIRED and not is_admin:
        logger.info(
            "Client booking start blocked during bot setup: bot_instance_id=%s master_id=%s",
            bot_instance.id,
            bot_instance.master_id,
        )
        await callback.answer(
            "💅 Онлайн-запись к мастеру пока настраивается. Пожалуйста, загляните позже!",
            show_alert=True,
        )
        return

    if master_id is None:
        # Fail closed, but never leave Telegram's callback spinner waiting forever.
        logger.error(
            "Client booking start rejected: trusted tenant context missing; bot_instance_id=%s",
            bot_instance.id if bot_instance else None,
        )
        await callback.answer(
            "Не удалось определить проект. Пожалуйста, откройте бота заново.",
            show_alert=True,
        )
        return

    logger.info(
        "Client booking start received: bot_instance_id=%s master_id=%s",
        bot_instance.id if bot_instance else None,
        master_id,
    )

    if not is_admin:
        policy = SubscriptionAccessPolicy(session)
        if not await policy.can_accept_new_booking(master_id):
            logger.info(
                "Client booking start blocked by subscription: bot_instance_id=%s master_id=%s",
                bot_instance.id if bot_instance else None,
                master_id,
            )
            settings_repo = MasterSettingsRepository(session)
            master_settings = await settings_repo.get_by_master_id(master_id)
            contacts_list = []
            if master_settings:
                if master_settings.studio_phone:
                    contacts_list.append(master_settings.studio_phone)
                if master_settings.telegram_username:
                    tg_user = master_settings.telegram_username.lstrip("@")
                    contacts_list.append(f"@{tg_user}")
            contacts_str = f": {' / '.join(contacts_list)}" if contacts_list else "."
            alert_text = f"Запись временно недоступна. Пожалуйста, свяжитесь с мастером напрямую{contacts_str}"
            await callback.answer(alert_text, show_alert=True)
            return

    await state.clear()
    staff_repo = StaffRepository(session)
    active_staff = await staff_repo.list_active(master_id=master_id)

    if not active_staff:
        logger.info("Client booking start has no active staff: master_id=%s", master_id)
        text = "К сожалению, в данный момент запись временно недоступна. Пожалуйста, свяжитесь с нами напрямую 🌸"
        if callback.message:
            await callback.message.edit_text(text=text)
        await callback.answer()
        return

    # If studio has more than 1 specialist: show specialist choice step
    if len(active_staff) > 1:
        logger.info(
            "Client booking start routed to staff selection: master_id=%s staff_count=%s",
            master_id,
            len(active_staff),
        )
        text = "<b>📅 Онлайн-запись</b>\n\nВыберите специалиста:"
        await state.set_state(ClientBookingSG.choosing_staff)
        if callback.message:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_staff_selection_keyboard(active_staff),
            )
        await callback.answer()
        return

    # Single specialist: seamlessly skip staff choice step (backward-compatible)
    single_staff = active_staff[0]
    await state.update_data(staff_id=single_staff.id)

    staff_svc_ids = await staff_repo.list_services_for_staff(single_staff.id, master_id=master_id)
    service_repo = ServiceRepository(session)
    all_services = await service_repo.list_active(master_id=master_id)
    services = [s for s in all_services if s.id in staff_svc_ids] or all_services

    if not services:
        logger.info("Client booking start has no active services: master_id=%s", master_id)
        text = "К сожалению, в данный момент онлайн-запись временно недоступна. Пожалуйста, свяжитесь с мастером напрямую 🌸"
        if callback.message:
            await callback.message.edit_text(text=text)
        await callback.answer()
        return

    text = "<b>📅 Онлайн-запись</b>\n\nВыберите услугу, на которую хотите записаться:"
    logger.info(
        "Client booking start routed to service selection: master_id=%s service_count=%s",
        master_id,
        len(services),
    )
    await state.set_state(ClientBookingSG.choosing_service)

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_services_list_keyboard(services, is_booking_flow=True),
        )
    await callback.answer()


@router.callback_query(StaffChoiceCallback.filter(F.action == "select"))
async def cb_staff_selected(
    callback: CallbackQuery,
    callback_data: StaffChoiceCallback,
    state: FSMContext,
    session: AsyncSession,
    master_id: int,
) -> None:
    """Specialist selected: show services offered by this staff member."""
    staff_id = callback_data.staff_id
    staff_repo = StaffRepository(session)
    staff = await staff_repo.get_by_id(staff_id, master_id=master_id)
    if not staff or not staff.is_active:
        await callback.answer("Специалист недоступен", show_alert=True)
        return

    await state.update_data(staff_id=staff_id)
    staff_svc_ids = await staff_repo.list_services_for_staff(staff_id, master_id=master_id)
    service_repo = ServiceRepository(session)
    all_services = await service_repo.list_active(master_id=master_id)
    services = [s for s in all_services if s.id in staff_svc_ids] or all_services

    if not services:
        text = f"У специалиста <b>{escape(staff.display_name)}</b> пока нет доступных услуг 🌸"
        if callback.message:
            await callback.message.edit_text(text=text)
        await callback.answer()
        return

    text = f"<b>📅 Онлайн-запись к мастеру {escape(staff.display_name)}</b>\n\nВыберите услугу:"
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
    session: AsyncSession, master_id: int,
) -> None:
    """
    Service selected: calculate available calendar days and show inline calendar.
    """
    service_id = callback_data.service_id
    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(service_id, master_id=master_id)

    if not service or not service.is_active or service.is_archived:
        await callback.answer("Услуга недоступна", show_alert=True)
        return

    await state.update_data(service_id=service_id)
    data = await state.get_data()
    staff_id = data.get("staff_id")

    # Calculate available dates for the next 30 days
    slot_engine = SlotEngine(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()

    horizon_val = await settings_repo.get_value(master_id, "booking_horizon_days", 30)
    horizon_days = max(1, int(horizon_val))

    available_dates_list = await slot_engine.get_available_dates(
        service_id=service_id, start_date=today, days_count=horizon_days, master_id=master_id, staff_id=staff_id
    )
    available_dates = set(available_dates_list)

    await state.set_state(ClientBookingSG.choosing_date)

    text = (
        f"🌸 <b>Услуга:</b> {escape(service.title)}\n"
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
    session: AsyncSession, master_id: int,
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
    staff_id = data.get("staff_id")
    if not service_id:
        await callback.answer("Сессия устарела. Пожалуйста, начните заново.", show_alert=True)
        return

    slot_engine = SlotEngine(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()

    horizon_val = await settings_repo.get_value(master_id, "booking_horizon_days", 30)
    horizon_days = max(1, int(horizon_val))

    if action in ["prev_month", "next_month"]:
        # Month switch
        available_dates_list = await slot_engine.get_available_dates(
            service_id=service_id, start_date=today, days_count=horizon_days, master_id=master_id, staff_id=staff_id
        )
        available_dates = set(available_dates_list)

        service_repo = ServiceRepository(session)
        service = await service_repo.get_by_id(service_id, master_id=master_id)

        text = (
            f"🌸 <b>Услуга:</b> {escape(service.title)}\n\n"
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
                service_id=service_id, start_date=today, days_count=horizon_days, master_id=master_id, staff_id=staff_id
            )
            available_dates = set(available_dates_list)
            service_repo = ServiceRepository(session)
            service = await service_repo.get_by_id(service_id, master_id=master_id)

            text = f"🌸 <b>Услуга:</b> {escape(service.title)}\n\nВыберите дату:"
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
        slots = await slot_engine.get_available_slots(service_id, chosen_date, master_id=master_id, staff_id=staff_id)

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
    session: AsyncSession, master_id: int,
) -> None:
    """
    Slot chosen: check if user phone is known. If not, request phone; otherwise show policy agreement.
    """
    slot_ts = callback_data.timestamp
    slot_dt = datetime.fromtimestamp(slot_ts, pytz.UTC)
    await state.update_data(slot_timestamp=slot_ts)

    data = await state.get_data()
    service_id = data.get("service_id") or callback_data.service_id
    await _log_booking_fsm("slot", state, master_id)
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
    policy_text = (
        "⚠️ <b>ВАЖНОЕ ПРАВИЛО:</b>\n"
        "<i>Для подтверждения записи требуется внесение предоплаты. "
        "В случае отмены записи клиентом внесённая предоплата не возвращается.</i>\n\n"
        "Подтверждая запись, вы соглашаетесь с данным условием."
        if deposit_amount > 0
        else "Предоплата не требуется. Подтверждая запись, вы соглашаетесь с правилами отмены проекта."
    )

    text = (
        "<b>📋 Проверьте данные вашей записи</b>\n\n"
        f"🌸 <b>Услуга:</b> {escape(service.title)}\n"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"⏳ <b>Длительность:</b> {dur_str}\n"
        f"💰 <b>Стоимость услуги:</b> {price_str}\n"
        f"💳 <b>Предоплата:</b> {dep_str}\n\n"
        f"{policy_text}"
    )

    confirmation_id = uuid4().hex
    await state.update_data(confirmation_id=confirmation_id)
    await state.set_state(ClientBookingSG.confirming_policy)
    await _log_booking_fsm("policy", state, master_id)

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_policy_agreement_keyboard(confirmation_id),
        )
    await callback.answer()


@router.message(ClientBookingSG.entering_phone, F.contact)
@router.message(ClientBookingSG.entering_phone, F.text)
async def msg_receive_phone(
    message: Message,
    state: FSMContext,
    db_user: User,
    session: AsyncSession, master_id: int,
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
    policy_text = (
        "⚠️ <b>ВАЖНОЕ ПРАВИЛО:</b>\n"
        "<i>Для подтверждения записи требуется внесение предоплаты. "
        "В случае отмены записи клиентом внесённая предоплата не возвращается.</i>\n\n"
        "Подтверждая запись, вы соглашаетесь с данным условием."
        if deposit_amount > 0
        else "Предоплата не требуется. Подтверждая запись, вы соглашаетесь с правилами отмены проекта."
    )

    text = (
        "<b>📋 Проверьте данные вашей записи</b>\n\n"
        f"🌸 <b>Услуга:</b> {escape(service.title)}\n"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"⏳ <b>Длительность:</b> {dur_str}\n"
        f"💰 <b>Стоимость услуги:</b> {price_str}\n"
        f"💳 <b>Предоплата:</b> {dep_str}\n\n"
        f"{policy_text}"
    )

    confirmation_id = uuid4().hex
    await state.update_data(confirmation_id=confirmation_id)
    await state.set_state(ClientBookingSG.confirming_policy)
    await message.answer(
        text=text,
        reply_markup=get_policy_agreement_keyboard(confirmation_id),
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


@router.callback_query(PolicyAgreementCallback.filter())
@router.callback_query(BookingActionCallback.filter(F.action == "agree_policy"))
async def cb_agree_policy(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
    session: AsyncSession, master_id: int,
    callback_data: PolicyAgreementCallback | BookingActionCallback | None = None,
) -> None:
    """
    Client agreed to policy: create hold booking, create pending payment and show requisites.
    """
    # Different update IDs can represent two clicks on the same policy screen.
    # Serialize them using its server-bound identity, and use the committed
    # outbox row as a durable receipt even after Redis FSM has been cleared.
    confirmation_key = None
    if callback.message:
        instance_id = session.info.get("bot_instance_id")
        if instance_id and session.info.get("trusted_master_id") == master_id:
            screen_id = (callback_data.confirmation_id
                         if isinstance(callback_data, PolicyAgreementCallback)
                         else "legacy")
            if len(screen_id) > 32 or not screen_id.isalnum():
                await callback.answer("Сессия истекла. Начните запись заново.", show_alert=True)
                return
            confirmation_key = (
                f"booking-confirm:{instance_id}:{db_user.id}:"
                f"{callback.message.chat.id}:{callback.message.message_id}:{screen_id}"
            )
            await session.execute(
                sql_text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": confirmation_key},
            )
            receipt = await session.scalar(select(TelegramOutbox.id).where(
                TelegramOutbox.idempotency_key == confirmation_key,
                TelegramOutbox.master_id == master_id,
                TelegramOutbox.bot_instance_id == instance_id,
                TelegramOutbox.target_chat_id == callback.message.chat.id,
            ))
            if receipt is not None:
                await callback.answer(
                    "Запись уже создана. Подтверждение появится в этом сообщении.",
                    show_alert=True,
                )
                return

    data = await state.get_data()
    await _log_booking_fsm("agree", state, master_id)
    service_id = data.get("service_id")
    slot_ts = data.get("slot_timestamp")

    if isinstance(callback_data, PolicyAgreementCallback) and data.get("confirmation_id") != callback_data.confirmation_id:
        await callback.answer("Сессия истекла. Начните запись заново.", show_alert=True)
        return
    if isinstance(callback_data, BookingActionCallback) and data.get("confirmation_id"):
        await callback.answer("Откройте актуальное подтверждение записи.", show_alert=True)
        return

    if not service_id or not slot_ts:
        await callback.answer("Сессия истекла. Начните запись заново.", show_alert=True)
        await state.clear()
        return

    slot_dt = datetime.fromtimestamp(slot_ts, pytz.UTC)
    booking_service = BookingService(session)
    staff_id = data.get("staff_id")

    try:
        appointment, payment = await booking_service.create_hold_booking(
            master_id=master_id,
            user_id=db_user.id,
            service_id=service_id,
            start_time=slot_dt,
            cancel_policy_agreed=True,
            staff_id=staff_id,
        )
    except SlotAlreadyBookedError:
        await callback.answer(
            "Ой! Кто-то успел занять это время прямо перед вами. Пожалуйста, выберите другое время 🌸",
            show_alert=True,
        )
        return
    except PaymentRequisitesMissingError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    except StaffServiceUnavailableError as exc:
        # A staff/service assignment can change while the client is in the
        # booking flow. Treat this as a completed business rejection so the
        # update is acknowledged and Telegram cannot retry the mutation.
        await state.clear()
        await callback.answer(exc.message, show_alert=True)
        return
    except SubscriptionExpiredError as exc:
        # The entitlement gate is an expected business rejection. Acknowledge
        # it only after the webhook transaction commits its completion marker,
        # so Telegram retries cannot leave the callback spinning indefinitely.
        session.info.setdefault("post_commit", []).append(state.clear)
        session.info.setdefault("post_commit", []).append(
            lambda message=str(exc): callback.answer(message, show_alert=True)
        )
        return

    # Fetch requisites from settings
    settings_repo = MasterSettingsRepository(session)
    master_payment_settings = await settings_repo.get_by_master_id(master_id)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)

    staff_line = ""
    if appointment.staff_id:
        staff_obj = await StaffRepository(session).get_by_id(appointment.staff_id, master_id)
        if staff_obj:
            staff_line = f"👩‍💼 <b>Специалист:</b> {escape(staff_obj.display_name)}\n"

    bot_instance_id = bound_bot_instance_id(session, master_id)
    if payment is None:
        text = (
            f"✅ <b>Запись подтверждена! (№{appointment.id})</b>\n\n"
            f"🌸 <b>Услуга:</b> {escape(appointment.snapshot_service_title)}\n"
            f"{staff_line}"
            f"🗓 <b>Дата и время:</b> {dt_str}\n\n"
            "Предоплата не требуется. До встречи!"
        )
        session.info.setdefault("post_commit", []).append(state.clear)
        if callback.message:
            await enqueue_telegram_edit(
                session,
                master_id=master_id,
                bot_instance_id=bot_instance_id,
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                text=text,
                reply_markup=get_main_menu_keyboard(is_admin=False),
                idempotency_key=confirmation_key or f"appointment:{appointment.id}:confirmation-screen",
            )
        else:
            await enqueue_telegram_message(
                session,
                master_id=master_id,
                bot_instance_id=bot_instance_id,
                chat_id=callback.from_user.id,
                text=text,
                reply_markup=get_main_menu_keyboard(is_admin=False),
                idempotency_key=f"appointment:{appointment.id}:confirmation-screen",
            )
        session.info.setdefault("post_commit", []).append(
            lambda: callback.answer("Запись подтверждена. Сообщение обновляется.", show_alert=True)
        )
        return

    if not master_payment_settings or not all(
        value and value.strip()
        for value in (
            master_payment_settings.bank_name,
            master_payment_settings.bank_card_number,
            master_payment_settings.bank_recipient_name,
        )
    ):
        # BookingService enforces this invariant before appointment insertion.
        # Keep a visible, neutral fail-closed response if the settings row changes
        # during this transaction or an unexpected legacy record is encountered.
        await callback.answer(
            "Предоплата временно недоступна. Свяжитесь с мастером.", show_alert=True
        )
        return

    bank_name = escape(master_payment_settings.bank_name.strip())
    card_number = escape(master_payment_settings.bank_card_number.strip())
    recipient = escape(master_payment_settings.bank_recipient_name.strip())
    hold_mins = int(await settings_repo.get_value(master_id, "hold_duration_minutes", settings.hold_duration_minutes))
    deposit_str = format_rub(appointment.snapshot_deposit_amount)

    text = (
        f"🎉 <b>Слот успешно зарезервирован! (Запись #{appointment.id})</b>\n\n"
        f"🌸 <b>Услуга:</b> {escape(appointment.snapshot_service_title)}\n"
        f"{staff_line}"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"💳 <b>Предоплата к переводу:</b> {deposit_str}\n\n"
        f"⏳ <i>У вас есть {hold_mins} минут для внесения предоплаты. По истечении этого времени бронь аннулируется.</i>\n\n"
        "<b>Реквизиты для перевода:</b>\n"
        f"🏦 <b>Банк:</b> {bank_name}\n"
        f"💳 <b>Номер карты:</b> <code>{card_number}</code>\n"
        f"👤 <b>Получатель:</b> {recipient}\n\n"
        "После перевода нажмите <b>«Я оплатил(а)»</b> и отправьте фото чека или скриншот:"
    )

    # Redis FSM is external to PostgreSQL. Keep it until the booking and the
    # processed-update marker have committed together.
    session.info.setdefault("post_commit", []).append(state.clear)

    if callback.message:
        await enqueue_telegram_edit(
            session,
            master_id=master_id,
            bot_instance_id=bot_instance_id,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            text=text,
            reply_markup=get_payment_screen_keyboard(appointment.id),
            idempotency_key=confirmation_key or f"appointment:{appointment.id}:payment-screen",
        )
    else:
        await enqueue_telegram_message(
            session,
            master_id=master_id,
            bot_instance_id=bot_instance_id,
            chat_id=callback.from_user.id,
            text=text,
            reply_markup=get_payment_screen_keyboard(appointment.id),
            idempotency_key=f"appointment:{appointment.id}:payment-screen",
        )
    session.info.setdefault("post_commit", []).append(
        lambda: callback.answer("Время зарезервировано. Реквизиты появятся в этом сообщении.", show_alert=True)
    )


async def _log_booking_fsm(checkpoint: str, state: FSMContext, master_id: int) -> None:
    """Log routing metadata and field presence, never client contact data."""
    key = state.key
    data = await state.get_data()
    logger.info(
        "Booking FSM checkpoint=%s master_id=%s bot_id=%s chat_id=%s user_id=%s "
        "destiny=%s state=%s has_service=%s has_slot=%s has_staff=%s",
        checkpoint, master_id, key.bot_id, key.chat_id, key.user_id,
        key.destiny, await state.get_state(), bool(data.get("service_id")),
        bool(data.get("slot_timestamp")), bool(data.get("staff_id")),
    )
