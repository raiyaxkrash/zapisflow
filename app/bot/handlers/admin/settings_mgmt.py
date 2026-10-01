"""
Admin settings and studio payment details management handlers.
Allows viewing and editing payment requisites, studio address, and booking policies.
"""

from typing import Any
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import AdminMenuCallback, get_admin_dashboard_keyboard
from app.bot.states.admin import AdminSettingsSG
from app.config.settings import settings
from app.repositories.master_settings_repository import MasterSettingsRepository

router = Router(name="admin_settings_mgmt")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


async def format_settings_text(session: AsyncSession, master_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """
    Load settings and build text and keyboard.
    """
    repo = MasterSettingsRepository(session)

    card = await repo.get_value(master_id, "bank_card_number", settings.bank_card_number)
    bank = await repo.get_value(master_id, "bank_name", settings.bank_name)
    recipient = await repo.get_value(master_id, "bank_recipient_name", settings.bank_recipient_name)
    address = await repo.get_value(
        master_id, "studio_address", "г. Москва, ул. Ленина, д. 25, студия 4"
    )
    hold_min = await repo.get_value(master_id, "hold_duration_minutes", settings.hold_duration_minutes)
    cancel_hours = await repo.get_value(master_id, "cancel_policy_hours", 24)

    text = (
        "<b>⚙️ Настройки и реквизиты студии</b>\n\n"
        f"💳 <b>Номер карты:</b> <code>{card}</code>\n"
        f"🏦 <b>Банк:</b> {bank}\n"
        f"👤 <b>Получатель:</b> {recipient}\n"
        f"📍 <b>Адрес студии:</b> {address}\n"
        f"⏳ <b>Время удержания слота (hold):</b> {hold_min} мин.\n"
        f"⏰ <b>Бесплатная отмена:</b> за {cancel_hours} ч. до визита\n\n"
        "Нажмите на параметр для изменения:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Номер карты",
                    callback_data="adm_set:edit:bank_card_number",
                ),
                InlineKeyboardButton(
                    text="✏️ Банк",
                    callback_data="adm_set:edit:bank_name",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ ФИО получателя",
                    callback_data="adm_set:edit:bank_recipient_name",
                ),
                InlineKeyboardButton(
                    text="✏️ Адрес студии",
                    callback_data="adm_set:edit:studio_address",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Время удержания (мин)",
                    callback_data="adm_set:edit:hold_duration_minutes",
                ),
                InlineKeyboardButton(
                    text="✏️ Часы отмены",
                    callback_data="adm_set:edit:cancel_policy_hours",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ В панель мастера",
                    callback_data=AdminMenuCallback(action="dashboard").pack(),
                )
            ],
        ]
    )

    return text, keyboard


@router.callback_query(AdminMenuCallback.filter(F.action == "settings"))
async def cb_admin_settings_view(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int
) -> None:
    """
    Show current settings and edit options.
    """
    await state.clear()
    text, keyboard = await format_settings_text(session, master_id)
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_set:edit:"))
async def cb_admin_settings_edit_prompt(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """
    Prompt admin for new value of the chosen setting.
    """
    setting_key = callback.data.split(":")[2]
    await state.set_state(AdminSettingsSG.editing_value)
    await state.update_data(setting_key=setting_key)

    names = {
        "bank_card_number": "номер банковской карты для предоплат",
        "bank_name": "название банка (например: Сбербанк, Т-Банк)",
        "bank_recipient_name": "ФИО получателя перевода (например: Екатерина В.)",
        "studio_address": "полный адрес студии / кабинета",
        "hold_duration_minutes": "время удержания неоплаченного слота (в минутах, например: 30)",
        "cancel_policy_hours": "срок отмены без потери предоплаты (в часах, например: 24)",
    }
    label = names.get(setting_key, setting_key)

    text = f"Введите новое значение для параметра <b>«{label}»</b>:"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data=AdminMenuCallback(action="settings").pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminSettingsSG.editing_value, F.text)
async def msg_admin_settings_save_value(
    message: Message, state: FSMContext, session: AsyncSession, master_id: int
) -> None:
    """
    Validate and save updated setting value.
    """
    data = await state.get_data()
    setting_key = data["setting_key"]
    raw_val = message.text.strip()

    val: Any = raw_val
    if setting_key in (
        "hold_duration_minutes",
        "cancel_policy_hours",
        "booking_horizon_days",
        "min_advance_hours",
        "grid_step_minutes",
        "default_buffer_minutes",
    ):
        try:
            val = int(raw_val)
        except ValueError:
            await message.answer("⚠️ Введите целое число.")
            return

    repo = MasterSettingsRepository(session)
    await repo.update_settings(master_id, **{setting_key: val})
    await session.commit()
    await state.clear()

    text, keyboard = await format_settings_text(session, master_id)
    await message.answer(
        text=f"✅ <b>Настройка успешно сохранена!</b>\n\n{text}", reply_markup=keyboard
    )
