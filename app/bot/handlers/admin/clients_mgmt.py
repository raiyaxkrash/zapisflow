"""
Admin CRM clients database and profile management handlers.
Search clients by name/phone/username, view LTV and appointment history, add master notes.
"""

from typing import Optional
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import (
    AdminClientCallback,
    AdminMenuCallback,
    get_admin_client_card_keyboard,
    get_admin_clients_menu_keyboard,
)
from app.bot.states.admin import AdminClientNoteSG, AdminClientSearchSG
from app.config.settings import settings
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.settings_repository import SettingsRepository
from app.repositories.user_repository import UserRepository
from app.utils.formatters import format_datetime_ru, format_rub

router = Router(name="admin_clients_mgmt")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


async def format_client_crm_card(
    user: User, session: AsyncSession, tz_name: str
) -> str:
    """
    Format rich CRM profile with stats and LTV.
    """
    user_repo = UserRepository(session)
    stats = await user_repo.get_user_crm_stats(user.id)

    full_name = user.first_name
    if user.last_name:
        full_name += f" {user.last_name}"

    phone_str = user.phone or "не указан"
    username_str = f"@{user.username}" if user.username else "нет"
    registered_str = user.first_seen_at.strftime("%d.%m.%Y")

    first_visit = (
        format_datetime_ru(stats["first_booking_at"], tz_name=tz_name)
        if stats["first_booking_at"]
        else "ещё не было"
    )
    last_visit = (
        format_datetime_ru(stats["last_booking_at"], tz_name=tz_name)
        if stats["last_booking_at"]
        else "ещё не было"
    )

    card = (
        f"<b>👤 Карточка клиента #{user.id}</b>\n\n"
        f"<b>Имя:</b> {full_name}\n"
        f"📞 <b>Телефон:</b> {phone_str}\n"
        f"💬 <b>Telegram:</b> {username_str}\n"
        f"📅 <b>В базе с:</b> {registered_str}\n\n"
        f"📊 <b>Статистика визитов:</b>\n"
        f"• Всего записей: <b>{stats['total_bookings']}</b>\n"
        f"• Успешных визитов: <b>{stats['completed']}</b> ✅\n"
        f"• Отмен: <b>{stats['cancelled']}</b> ❌\n"
        f"• Неявок (NO-SHOW): <b>{stats['no_show']}</b> 🚫\n"
        f"• Общий доход (LTV): <b>{format_rub(stats['total_spent'])}</b> 💰\n\n"
        f"🗓 <b>Первый визит:</b> {first_visit}\n"
        f"🗓 <b>Последний визит:</b> {last_visit}\n"
    )

    if user.admin_notes:
        card += f"\n📝 <b>Заметка мастера:</b> {user.admin_notes}\n"

    return card


@router.callback_query(AdminMenuCallback.filter(F.action == "clients"))
@router.callback_query(AdminClientCallback.filter(F.action == "list"))
async def cb_admin_clients_root(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """
    Open client CRM root menu.
    """
    await state.clear()
    text = (
        "<b>👥 База клиентов (CRM мастера)</b>\n\n"
        "Здесь вы можете искать клиентов, просматривать историю записей, "
        "общую сумму покупок (LTV) и оставлять внутренние заметки."
    )
    keyboard = get_admin_clients_menu_keyboard()

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminClientCallback.filter(F.action == "search"))
async def cb_admin_client_search_prompt(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """
    Prompt admin for search query.
    """
    await state.clear()
    await state.set_state(AdminClientSearchSG.entering_query)

    text = (
        "<b>🔎 Поиск клиента в базе</b>\n\n"
        "Отправьте имя, фамилию, номер телефона или @username для поиска:"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=AdminMenuCallback(action="clients").pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminClientSearchSG.entering_query, F.text)
async def msg_admin_client_search_results(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """
    Perform search and return list of matching clients.
    """
    query_text = message.text.strip()
    await state.clear()

    user_repo = UserRepository(session)
    users = await user_repo.search_users(search_text=query_text, limit=20)

    if not users:
        text = (
            f"По запросу <b>«{query_text}»</b> клиентов не найдено.\n"
            "Попробуйте изменить запрос:"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔎 Искать снова",
                        callback_data=AdminClientCallback(action="search").pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ В меню CRM",
                        callback_data=AdminMenuCallback(action="clients").pack(),
                    )
                ],
            ]
        )
        await message.answer(text=text, reply_markup=keyboard)
        return

    text = f"<b>Найдено клиентов: {len(users)}</b>\nВыберите для просмотра карточки:"
    buttons = []
    for u in users:
        name = u.first_name + (f" {u.last_name}" if u.last_name else "")
        info = u.phone or (f"@{u.username}" if u.username else "без контакта")
        btn_text = f"#{u.id} {name} ({info})"
        buttons.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=AdminClientCallback(action="detail", user_id=u.id).pack(),
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="🔎 Новый поиск",
            callback_data=AdminClientCallback(action="search").pack(),
        ),
        InlineKeyboardButton(
            text="◀️ В меню CRM",
            callback_data=AdminMenuCallback(action="clients").pack(),
        ),
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text=text, reply_markup=keyboard)


@router.callback_query(AdminClientCallback.filter(F.action == "detail"))
async def cb_admin_client_detail(
    callback: CallbackQuery,
    callback_data: AdminClientCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """
    Show full CRM profile card for a client.
    """
    await state.clear()
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(callback_data.user_id)

    if not user:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    settings_repo = SettingsRepository(session)
    tz_str = await settings_repo.get_value("timezone", settings.timezone)

    text = await format_client_crm_card(user, session, tz_name=tz_str)
    # Only show telegram link if real positive telegram_id
    tg_id = user.telegram_id if (user.telegram_id and user.telegram_id > 0) else None
    keyboard = get_admin_client_card_keyboard(user_id=user.id, telegram_id=tg_id)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminClientCallback.filter(F.action == "note"))
async def cb_admin_client_add_note_prompt(
    callback: CallbackQuery,
    callback_data: AdminClientCallback,
    state: FSMContext,
) -> None:
    """
    Prompt admin to input internal CRM note for the client.
    """
    await state.set_state(AdminClientNoteSG.entering_note)
    await state.update_data(user_id=callback_data.user_id)

    text = (
        f"<b>📝 Заметка к профилю клиента #{callback_data.user_id}</b>\n\n"
        "Отправьте текст заметки (видна только мастеру):\n"
        "<i>Например: Предпочитает кофе с молоком без сахара, чувствительная кожа</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data=AdminClientCallback(
                        action="detail", user_id=callback_data.user_id
                    ).pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminClientNoteSG.entering_note, F.text)
async def msg_admin_client_save_note(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """
    Save internal CRM note and show updated profile.
    """
    data = await state.get_data()
    user_id = data["user_id"]
    await state.clear()

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(user_id)
    if not user:
        await message.answer("Клиент не найден.")
        return

    user.admin_notes = message.text.strip()
    await session.flush()

    settings_repo = SettingsRepository(session)
    tz_str = await settings_repo.get_value("timezone", settings.timezone)

    text = (
        f"✅ <b>Заметка сохранена!</b>\n\n"
        + await format_client_crm_card(user, session, tz_name=tz_str)
    )
    tg_id = user.telegram_id if (user.telegram_id and user.telegram_id > 0) else None
    keyboard = get_admin_client_card_keyboard(user_id=user.id, telegram_id=tg_id)
    await message.answer(text=text, reply_markup=keyboard)
