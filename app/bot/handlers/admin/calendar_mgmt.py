"""
Admin schedule and calendar management handlers.
Allows setting days off, custom working hours, breaks, and viewing daily schedules.
"""

from datetime import date, datetime, time, timedelta
import re
from typing import Optional
import pytz
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import (
    AdminCalendarCallback,
    AdminMenuCallback,
    get_admin_calendar_keyboard,
    get_admin_day_management_keyboard,
)
from app.bot.states.admin import AdminScheduleDaySG
from app.config.settings import settings
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.user_repository import UserRepository
from app.services.tenant_context import LegacyTenantResolver
from app.utils.formatters import RU_WEEKDAYS_FULL, format_rub

router = Router(name="admin_calendar_mgmt")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


async def format_day_info(
    target_date: date, session: AsyncSession, master_id: int
) -> tuple[str, bool]:
    """
    Format status text and determine is_day_off for a target date.
    """
    schedule_repo = ScheduleRepository(session)
    settings_repo = MasterSettingsRepository(session)
    app_repo = AppointmentRepository(session)

    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)

    exception = await schedule_repo.get_exception_for_date(target_date, master_id=master_id)
    template = await schedule_repo.get_template_for_weekday(target_date.weekday(), master_id=master_id)

    if exception is not None:
        is_day_off = exception.is_day_off
        work_start = exception.work_start
        work_end = exception.work_end
        comment = exception.comment
        source = "(индивидуальное расписание дня)"
    elif template is not None:
        is_day_off = template.is_day_off
        work_start = template.work_start
        work_end = template.work_end
        comment = None
        source = "(по стандартному шаблону недели)"
    else:
        # Default fallback
        is_day_off = target_date.weekday() in [5, 6]  # weekends
        work_start = time(10, 0)
        work_end = time(19, 0)
        comment = None
        source = "(по умолчанию)"

    weekday_name = RU_WEEKDAYS_FULL[target_date.weekday()]
    status_emoji = "🔴 ВЫХОДНОЙ ДЕНЬ" if is_day_off else "🟢 РАБОЧИЙ ДЕНЬ"

    text = (
        f"<b>🗓 Управление расписанием</b>\n"
        f"<b>Дата:</b> {target_date.strftime('%d.%m.%Y')} ({weekday_name})\n"
        f"<b>Статус:</b> {status_emoji} <i>{source}</i>\n"
    )

    if not is_day_off and work_start and work_end:
        text += f"⏰ <b>Часы работы:</b> {work_start.strftime('%H:%M')} — {work_end.strftime('%H:%M')}\n"

    if comment:
        text += f"💬 <b>Заметка к дню:</b> {comment}\n"

    # Count appointments
    start_dt = tz.localize(datetime.combine(target_date, time.min))
    end_dt = tz.localize(datetime.combine(target_date, time.max))
    day_apps = await app_repo.list_for_admin(master_id=master_id, date_from=start_dt, date_to=end_dt)
    text += f"👥 <b>Записей на день:</b> {len(day_apps)} шт.\n"

    # Blocked intervals
    blocked = await schedule_repo.get_blocked_intervals(start_dt, end_dt, master_id=master_id)
    if blocked:
        text += "\n🔒 <b>Заблокированные часы / перерывы:</b>\n"
        for b in blocked:
            b_start = b.start_time.astimezone(tz).strftime("%H:%M")
            b_end = b.end_time.astimezone(tz).strftime("%H:%M")
            reason_str = f" ({b.reason})" if b.reason else ""
            text += f"• {b_start} — {b_end}{reason_str}\n"

    return text, is_day_off


@router.callback_query(AdminMenuCallback.filter(F.action == "calendar"))
async def cb_admin_calendar_root(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """
    Open master calendar month view.
    """
    await state.clear()
    master_id = await LegacyTenantResolver.get_master_id(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()

    text = (
        "<b>🗓 График и рабочий календарь мастера</b>\n\n"
        "Выберите дату в календаре для настройки часов, перерывов или выходных:"
    )
    keyboard = get_admin_calendar_keyboard(year=today.year, month=today.month, today=today)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminCalendarCallback.filter(F.action == "month"))
async def cb_admin_calendar_month_nav(
    callback: CallbackQuery,
    callback_data: AdminCalendarCallback,
    session: AsyncSession,
) -> None:
    """
    Navigate between months in calendar.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()

    keyboard = get_admin_calendar_keyboard(
        year=callback_data.year, month=callback_data.month, today=today
    )
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminCalendarCallback.filter(F.action == "day"))
async def cb_admin_calendar_day_select(
    callback: CallbackQuery,
    callback_data: AdminCalendarCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """
    Open day management card for the selected date.
    """
    await state.clear()
    master_id = await LegacyTenantResolver.get_master_id(session)
    target_date = date(callback_data.year, callback_data.month, callback_data.day)
    text, is_day_off = await format_day_info(target_date, session, master_id=master_id)

    keyboard = get_admin_day_management_keyboard(target_date, is_day_off=is_day_off)
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminCalendarCallback.filter(F.action == "toggle_day_off"))
async def cb_admin_calendar_toggle_day_off(
    callback: CallbackQuery,
    callback_data: AdminCalendarCallback,
    session: AsyncSession,
) -> None:
    """
    Toggle day off status for the date.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    target_date = date(callback_data.year, callback_data.month, callback_data.day)
    schedule_repo = ScheduleRepository(session)

    _, current_is_day_off = await format_day_info(target_date, session, master_id=master_id)
    new_is_day_off = not current_is_day_off

    await schedule_repo.set_date_exception(
        target_date=target_date,
        is_day_off=new_is_day_off,
        work_start=time(10, 0) if not new_is_day_off else None,
        work_end=time(19, 0) if not new_is_day_off else None,
        master_id=master_id,
    )

    text, is_day_off = await format_day_info(target_date, session, master_id=master_id)
    keyboard = get_admin_day_management_keyboard(target_date, is_day_off=is_day_off)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)

    alert_msg = "День сделан выходным 🔴" if new_is_day_off else "День сделан рабочим 🟢"
    await callback.answer(alert_msg, show_alert=False)


# --- Set Custom Working Hours ---


@router.callback_query(AdminCalendarCallback.filter(F.action == "set_hours"))
async def cb_admin_calendar_set_hours_prompt(
    callback: CallbackQuery,
    callback_data: AdminCalendarCallback,
    state: FSMContext,
) -> None:
    """
    Prompt admin to input custom working hours.
    """
    target_date = date(callback_data.year, callback_data.month, callback_data.day)
    await state.set_state(AdminScheduleDaySG.setting_hours)
    await state.update_data(
        year=target_date.year, month=target_date.month, day=target_date.day
    )

    text = (
        f"<b>⏰ Настройка рабочих часов: {target_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Отправьте рабочие часы в формате: <b>ЧЧ:ММ-ЧЧ:ММ</b>\n\n"
        "<i>Пример: 10:00-19:00 или 12:00-21:30</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data=AdminCalendarCallback(
                        action="day",
                        year=target_date.year,
                        month=target_date.month,
                        day=target_date.day,
                    ).pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminScheduleDaySG.setting_hours, F.text)
async def msg_admin_calendar_save_hours(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """
    Validate and save custom working hours.
    """
    data = await state.get_data()
    target_date = date(data["year"], data["month"], data["day"])

    match = re.match(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$", message.text.strip())
    if not match:
        await message.answer(
            "⚠️ Неверный формат времени. Введите в формате <b>10:00-19:00</b>:"
        )
        return

    h1, m1, h2, m2 = map(int, match.groups())
    if not (0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= h2 <= 23 and 0 <= m2 <= 59):
        await message.answer("⚠️ Некорректные значения часов или минут. Попробуйте снова:")
        return

    start_t = time(h1, m1)
    end_t = time(h2, m2)
    if start_t >= end_t:
        await message.answer("⚠️ Время начала должно быть раньше времени окончания. Попробуйте снова:")
        return

    await state.clear()
    master_id = await LegacyTenantResolver.get_master_id(session)
    schedule_repo = ScheduleRepository(session)
    await schedule_repo.set_date_exception(
        target_date=target_date,
        is_day_off=False,
        work_start=start_t,
        work_end=end_t,
        master_id=master_id,
    )

    text, is_day_off = await format_day_info(target_date, session, master_id=master_id)
    keyboard = get_admin_day_management_keyboard(target_date, is_day_off=is_day_off)
    await message.answer(
        text=f"✅ <b>Рабочие часы сохранены!</b>\n\n{text}", reply_markup=keyboard
    )


# --- Block Manual Interval ---


@router.callback_query(AdminCalendarCallback.filter(F.action == "block_slot"))
async def cb_admin_calendar_block_slot_prompt(
    callback: CallbackQuery,
    callback_data: AdminCalendarCallback,
    state: FSMContext,
) -> None:
    """
    Prompt admin to block time interval on target date.
    """
    target_date = date(callback_data.year, callback_data.month, callback_data.day)
    await state.set_state(AdminScheduleDaySG.blocking_slot_start)
    await state.update_data(
        year=target_date.year, month=target_date.month, day=target_date.day
    )

    text = (
        f"<b>🔒 Блокировка времени: {target_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Отправьте интервал и причину блокировки в формате:\n"
        "<b>ЧЧ:ММ-ЧЧ:ММ Причина</b>\n\n"
        "<i>Пример: 13:00-14:00 Обед</i>\n"
        "<i>Пример: 15:30-17:00 Личные дела</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data=AdminCalendarCallback(
                        action="day",
                        year=target_date.year,
                        month=target_date.month,
                        day=target_date.day,
                    ).pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminScheduleDaySG.blocking_slot_start, F.text)
async def msg_admin_calendar_save_blocked_slot(
    message: Message, state: FSMContext, db_user: User, session: AsyncSession
) -> None:
    """
    Parse blocked interval and insert BlockedInterval record.
    """
    data = await state.get_data()
    target_date = date(data["year"], data["month"], data["day"])

    match = re.match(
        r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})(?:\s+(.*))?$",
        message.text.strip(),
    )
    if not match:
        await message.answer(
            "⚠️ Неверный формат. Введите в формате <b>13:00-14:00 Обед</b>:"
        )
        return

    h1, m1, h2, m2, reason = match.groups()
    h1, m1, h2, m2 = int(h1), int(m1), int(h2), int(m2)
    start_t = time(h1, m1)
    end_t = time(h2, m2)

    if start_t >= end_t:
        await message.answer("⚠️ Начало интервала должно быть раньше окончания.")
        return

    master_id = await LegacyTenantResolver.get_master_id(session)
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)

    start_dt = tz.localize(datetime.combine(target_date, start_t))
    end_dt = tz.localize(datetime.combine(target_date, end_t))

    await state.clear()
    schedule_repo = ScheduleRepository(session)
    admin = await UserRepository(session).get_active_admin_by_user_id(db_user.id)
    if admin is None:
        await message.answer("Профиль администратора не найден")
        return
    await schedule_repo.create_blocked_interval(
        start_time=start_dt,
        end_time=end_dt,
        reason=reason.strip() if reason else "Заблокировано мастером",
        created_by_admin_id=admin.id,
        master_id=master_id,
    )

    text, is_day_off = await format_day_info(target_date, session, master_id=master_id)
    keyboard = get_admin_day_management_keyboard(target_date, is_day_off=is_day_off)
    await message.answer(
        text=f"🔒 <b>Интервал успешно заблокирован!</b>\n\n{text}",
        reply_markup=keyboard,
    )


# --- View Day Bookings ---


@router.callback_query(AdminCalendarCallback.filter(F.action == "day_bookings"))
async def cb_admin_calendar_day_bookings(
    callback: CallbackQuery,
    callback_data: AdminCalendarCallback,
    session: AsyncSession,
) -> None:
    """
    List all appointments booked on the selected day.
    """
    target_date = date(callback_data.year, callback_data.month, callback_data.day)
    master_id = await LegacyTenantResolver.get_master_id(session)
    settings_repo = MasterSettingsRepository(session)
    app_repo = AppointmentRepository(session)

    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    tz = pytz.timezone(tz_str)
    start_dt = tz.localize(datetime.combine(target_date, time.min))
    end_dt = tz.localize(datetime.combine(target_date, time.max))

    appointments = await app_repo.list_for_admin(master_id=master_id, date_from=start_dt, date_to=end_dt)

    if not appointments:
        text = f"<b>🗓 Записи на {target_date.strftime('%d.%m.%Y')}</b>\n\nВ этот день записей нет."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Назад к дню",
                        callback_data=AdminCalendarCallback(
                            action="day",
                            year=target_date.year,
                            month=target_date.month,
                            day=target_date.day,
                        ).pack(),
                    )
                ]
            ]
        )
    else:
        text = f"<b>🗓 Записи на {target_date.strftime('%d.%m.%Y')} ({len(appointments)} шт.)</b>\n\n"
        buttons = []
        for app in appointments:
            time_str = app.start_time.astimezone(tz).strftime("%H:%M")
            client_name = app.user.first_name if app.user else "Клиент"
            status_symbol = "🟢" if app.status.value == "confirmed" else "⏳"
            btn_text = f"{status_symbol} #{app.id} {time_str} {client_name} — {app.snapshot_service_title}"
            from app.bot.keyboards.admin.callbacks import AdminAppointmentCallback
            buttons.append([
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=AdminAppointmentCallback(
                        action="detail", appointment_id=app.id, filter_type="today"
                    ).pack(),
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="◀️ Назад к дню",
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
