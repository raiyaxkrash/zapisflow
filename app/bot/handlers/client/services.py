"""
Services catalog and details handlers.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import (
    MenuCallback,
    ServiceCallback,
    get_service_detail_keyboard,
    get_services_list_keyboard,
)
from app.repositories.service_repository import ServiceRepository
from app.utils.formatters import format_duration, format_rub

router = Router(name="client_services")


@router.callback_query(MenuCallback.filter(F.action == "services"))
@router.callback_query(ServiceCallback.filter(F.action == "list"))
async def cb_services_list(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int
) -> None:
    """
    Display catalog of all active services with prices.
    """
    service_repo = ServiceRepository(session)
    services = await service_repo.list_active(master_id=master_id)

    if not services:
        text = "В данный момент список услуг обновляется мастером. Пожалуйста, загляните позже 🌸"
    else:
        text = (
            "<b>💰 Услуги и стоимость</b>\n\n"
            "Нажмите на интересующую услугу, чтобы узнать подробности и записаться:"
        )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_services_list_keyboard(services, is_booking_flow=False),
        )
    await callback.answer()


@router.callback_query(ServiceCallback.filter(F.action == "view"))
async def cb_service_view(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    session: AsyncSession, master_id: int,
) -> None:
    """
    Display detailed card for a single selected service.
    """
    service_repo = ServiceRepository(session)
    service = await service_repo.get_by_id(callback_data.service_id, master_id=master_id)

    if not service or not service.is_active or service.is_archived:
        await callback.answer("Услуга недоступна", show_alert=True)
        return

    price_str = format_rub(service.price)
    dur_str = format_duration(service.duration_min)
    if service.deposit_type.value == "PERCENT":
        dep_str = f"{int(service.deposit_value)}% ({format_rub(service.price * service.deposit_value / 100)})"
    else:
        dep_str = format_rub(service.deposit_value)

    desc = f"\n\n<i>{service.description}</i>" if service.description else ""

    text = (
        f"🌸 <b>{service.title}</b>{desc}\n\n"
        f"⏳ <b>Длительность:</b> {dur_str}\n"
        f"💰 <b>Стоимость:</b> {price_str}\n"
        f"💳 <b>Предоплата:</b> {dep_str}\n\n"
        "Нажмите <b>«Выбрать дату»</b>, чтобы посмотреть свободные окна для записи:"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_service_detail_keyboard(service.id),
        )
    await callback.answer()
