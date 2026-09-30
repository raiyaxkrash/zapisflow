"""
Admin services and price list management handlers.
Supports editing price, duration, buffer, deposit, toggling visibility, archiving and adding services.
"""

from decimal import Decimal
from typing import Optional
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import (
    AdminMenuCallback,
    AdminServiceCallback,
    get_admin_service_card_keyboard,
    get_admin_services_list_keyboard,
)
from app.bot.states.admin import AdminServiceSG
from app.database.models.service import DepositType, Service
from app.repositories.service_repository import ServiceRepository
from app.services.tenant_context import LegacyTenantResolver
from app.utils.formatters import format_rub

router = Router(name="admin_services_mgmt")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


def format_service_card(service: Service) -> str:
    """
    Format service details text.
    """
    status_str = "🟢 Активна (доступна для записи)" if service.is_active else "🔴 Отключена"
    if service.is_archived:
        status_str += " | 📦 В архиве"

    dep_str = (
        f"{service.deposit_value}%"
        if service.deposit_type == DepositType.PERCENT
        else format_rub(service.deposit_value)
    )

    desc_str = service.description or "нет описания"

    return (
        f"<b>🌸 Услуга #{service.id}: {service.title}</b>\n\n"
        f"<b>Статус:</b> {status_str}\n\n"
        f"💰 <b>Стоимость:</b> {format_rub(service.price)}\n"
        f"⏱ <b>Длительность:</b> {service.duration_min} мин.\n"
        f"⏳ <b>Буферное время:</b> {service.buffer_min} мин.\n"
        f"💳 <b>Предоплата:</b> {dep_str}\n\n"
        f"📝 <b>Описание:</b> {desc_str}"
    )


@router.callback_query(AdminMenuCallback.filter(F.action == "services"))
@router.callback_query(AdminServiceCallback.filter(F.action == "list"))
async def cb_admin_services_list(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """
    List all master services.
    """
    await state.clear()
    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    services = await service_repo.list_all_for_admin(master_id=master_id)

    text = (
        "<b>💰 Управление услугами и прайс-листом</b>\n\n"
        "🟢 — услуга активна и отображается клиентам\n"
        "🔴 — услуга выключена\n"
        "📦 — архивированная услуга\n\n"
        "Выберите услугу для редактирования или добавьте новую:"
    )
    keyboard = get_admin_services_list_keyboard(services)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminServiceCallback.filter(F.action == "detail"))
async def cb_admin_service_detail(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """
    Show individual service management card.
    """
    await state.clear()
    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(callback_data.service_id, master_id=master_id)

    if not service:
        await callback.answer("Услуга не найдена", show_alert=True)
        return

    text = format_service_card(service)
    keyboard = get_admin_service_card_keyboard(service)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminServiceCallback.filter(F.action == "toggle"))
async def cb_admin_service_toggle(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
) -> None:
    """
    Toggle service active status.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    new_status = await service_repo.toggle_active(callback_data.service_id, master_id=master_id)

    if new_status is None:
        await callback.answer("Услуга не найдена", show_alert=True)
        return

    service = await service_repo.get_by_id(callback_data.service_id, master_id=master_id)
    text = format_service_card(service)
    keyboard = get_admin_service_card_keyboard(service)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)

    msg = "Услуга включена 🟢" if new_status else "Услуга выключена 🔴"
    await callback.answer(msg)


@router.callback_query(AdminServiceCallback.filter(F.action == "archive"))
async def cb_admin_service_archive(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
) -> None:
    """
    Soft-delete / archive service.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    await service_repo.archive(callback_data.service_id, master_id=master_id)

    service = await service_repo.get_by_id(callback_data.service_id, master_id=master_id)
    text = format_service_card(service)
    keyboard = get_admin_service_card_keyboard(service)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer("Услуга архивирована 📦")


@router.callback_query(AdminServiceCallback.filter(F.action == "unarchive"))
async def cb_admin_service_unarchive(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
) -> None:
    """
    Restore service from archive.
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    await service_repo.unarchive(callback_data.service_id, master_id=master_id)

    service = await service_repo.get_by_id(callback_data.service_id, master_id=master_id)
    text = format_service_card(service)
    keyboard = get_admin_service_card_keyboard(service)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer("Услуга восстановлена из архива ♻️")


# --- Edit Specific Field ---


@router.callback_query(AdminServiceCallback.filter(F.action == "edit"))
async def cb_admin_service_edit_prompt(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    state: FSMContext,
) -> None:
    """
    Prompt admin to input new value for a specific field.
    """
    field = callback_data.field
    service_id = callback_data.service_id

    prompts = {
        "title": "Введите <b>новое название</b> услуги:",
        "price": "Введите <b>новую стоимость</b> услуги в рублях (например: 2500):",
        "duration": "Введите <b>длительность услуги в минутах</b> (например: 90 или 120):",
        "buffer": "Введите <b>буферное время после услуги в минутах</b> (например: 15 или 30):",
        "deposit": "Введите <b>размер обязательной предоплаты</b> в рублях (например: 500):",
    }
    prompt_text = prompts.get(field, "Введите новое значение:")

    await state.set_state(AdminServiceSG.edit_field_value)
    await state.update_data(service_id=service_id, field=field)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data=AdminServiceCallback(
                        action="detail", service_id=service_id
                    ).pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=prompt_text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminServiceSG.edit_field_value, F.text)
async def msg_admin_service_save_field(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """
    Validate and save updated field value.
    """
    data = await state.get_data()
    service_id = data["service_id"]
    field = data["field"]
    raw_val = message.text.strip()

    master_id = await LegacyTenantResolver.get_master_id(session)
    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(service_id, master_id=master_id)
    if not service:
        await message.answer("Услуга не найдена.")
        await state.clear()
        return

    try:
        if field == "title":
            if len(raw_val) < 2:
                await message.answer("⚠️ Название слишком короткое. Введите снова:")
                return
            service.title = raw_val
        elif field == "price":
            price_val = Decimal(raw_val.replace(" ", "").replace(",", "."))
            if price_val < 0:
                await message.answer("⚠️ Стоимость не может быть отрицательной. Введите снова:")
                return
            service.price = price_val
        elif field == "duration":
            dur = int(raw_val)
            if dur <= 0:
                await message.answer("⚠️ Длительность должна быть больше 0. Введите снова:")
                return
            service.duration_min = dur
        elif field == "buffer":
            buf = int(raw_val)
            if buf < 0:
                await message.answer("⚠️ Буфер не может быть отрицательным. Введите снова:")
                return
            service.buffer_min = buf
        elif field == "deposit":
            dep = Decimal(raw_val.replace(" ", "").replace(",", "."))
            if dep < 0 or dep > service.price:
                await message.answer(
                    f"⚠️ Предоплата не может превышать стоимость услуги ({format_rub(service.price)}). Введите снова:"
                )
                return
            service.deposit_type = DepositType.FIXED
            service.deposit_value = dep
    except ValueError:
        await message.answer("⚠️ Некорректный ввод. Введите числовое значение:")
        return

    await session.flush()
    await session.refresh(service)
    await state.clear()

    text = f"✅ <b>Параметр успешно обновлён!</b>\n\n" + format_service_card(service)
    keyboard = get_admin_service_card_keyboard(service)
    await message.answer(text=text, reply_markup=keyboard)


# --- Add Service Wizard ---


@router.callback_query(AdminServiceCallback.filter(F.action == "add"))
async def cb_admin_service_add_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """
    Step 1: Start adding service - ask for title.
    """
    await state.clear()
    await state.set_state(AdminServiceSG.title)

    text = (
        "<b>➕ Добавление новой услуги</b>\n\n"
        "Шаг 1 из 5: Введите <b>название услуги</b>\n"
        "<i>Например: Наращивание ресниц 2D, Маникюр со снятием</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data=AdminServiceCallback(action="list").pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminServiceSG.title, F.text)
async def msg_add_service_title(message: Message, state: FSMContext) -> None:
    """
    Step 2: Title received - ask for description.
    """
    title = message.text.strip()
    if len(title) < 2:
        await message.answer("⚠️ Слишком короткое название. Введите снова:")
        return

    await state.update_data(title=title)
    await state.set_state(AdminServiceSG.description)

    text = (
        f"Название: <b>{title}</b>\n\n"
        "Шаг 2 из 5: Введите <b>краткое описание</b> услуги (или отправьте «-» без описания):"
    )
    await message.answer(text=text)


@router.message(AdminServiceSG.description, F.text)
async def msg_add_service_desc(message: Message, state: FSMContext) -> None:
    """
    Step 3: Description received - ask for price.
    """
    desc = message.text.strip()
    description = None if desc == "-" else desc
    await state.update_data(description=description)
    await state.set_state(AdminServiceSG.price)

    text = "Шаг 3 из 5: Введите <b>стоимость услуги в рублях</b> (например: 2500):"
    await message.answer(text=text)


@router.message(AdminServiceSG.price, F.text)
async def msg_add_service_price(message: Message, state: FSMContext) -> None:
    """
    Step 4: Price received - ask for duration.
    """
    try:
        price = Decimal(message.text.strip().replace(" ", "").replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите корректную сумму в рублях:")
        return

    await state.update_data(price=str(price))
    await state.set_state(AdminServiceSG.duration)

    text = "Шаг 4 из 5: Введите <b>длительность услуги в минутах</b> (например: 90 или 120):"
    await message.answer(text=text)


@router.message(AdminServiceSG.duration, F.text)
async def msg_add_service_duration(message: Message, state: FSMContext) -> None:
    """
    Step 5: Duration received - ask for deposit.
    """
    try:
        dur = int(message.text.strip())
        if dur <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите целое положительное число минут:")
        return

    await state.update_data(duration_min=dur, buffer_min=15)
    await state.set_state(AdminServiceSG.deposit_value)

    text = (
        f"Длительность: <b>{dur} мин.</b> (буфер по умолчанию 15 мин.)\n\n"
        "Шаг 5 из 5: Введите <b>размер обязательной предоплаты</b> в рублях (например: 500, или 0 если без предоплаты):"
    )
    await message.answer(text=text)


@router.message(AdminServiceSG.deposit_value, F.text)
async def msg_add_service_finalize(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """
    Finalize service creation and save to database.
    """
    try:
        deposit_val = Decimal(message.text.strip().replace(" ", "").replace(",", "."))
        if deposit_val < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите корректную сумму предоплаты в рублях:")
        return

    data = await state.get_data()
    await state.clear()

    price = Decimal(data["price"])
    if deposit_val > price:
        deposit_val = price

    master_id = await LegacyTenantResolver.get_master_id(session)
    new_service = Service(
        master_id=master_id,
        title=data["title"],
        description=data["description"],
        price=price,
        duration_min=data["duration_min"],
        buffer_min=data.get("buffer_min", 15),
        deposit_type=DepositType.FIXED,
        deposit_value=deposit_val,
        is_active=True,
        is_archived=False,
    )
    session.add(new_service)
    await session.flush()
    await session.refresh(new_service)

    text = f"🎉 <b>Новая услуга успешно создана!</b>\n\n" + format_service_card(new_service)
    keyboard = get_admin_service_card_keyboard(new_service)
    await message.answer(text=text, reply_markup=keyboard)
