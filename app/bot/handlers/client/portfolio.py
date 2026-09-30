"""
Portfolio browsing handlers: category selection and interactive work slider.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.keyboards.client import (
    MenuCallback,
    PortfolioNavCallback,
    get_main_menu_keyboard,
    get_portfolio_categories_keyboard,
    get_portfolio_item_keyboard,
)
from app.database.models.portfolio import PortfolioCategory, PortfolioItem
from app.repositories.portfolio_repository import PortfolioRepository
from app.services.tenant_context import LegacyTenantResolver

router = Router(name="client_portfolio")


@router.callback_query(MenuCallback.filter(F.action == "portfolio"))
@router.callback_query(PortfolioNavCallback.filter(F.action == "categories"))
async def cb_portfolio_categories(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """
    List active portfolio categories for current master.
    """
    await state.clear()
    master_id = await LegacyTenantResolver.get_master_id(session)
    portfolio_repo = PortfolioRepository(session)
    categories = await portfolio_repo.list_categories(master_id=master_id, active_only=True)

    if not categories:
        text = "Раздел портфолио в данный момент наполняется новыми работами 🌸"
        if callback.message:
            await callback.message.edit_text(text=text, reply_markup=get_main_menu_keyboard())
        await callback.answer()
        return

    text = "<b>🖼 Портфолио работ</b>\n\nВыберите категорию для просмотра фотографий:"

    if callback.message:
        if callback.message.photo:
            # If previous message was a photo, send a new text message and delete the photo
            await callback.message.delete()
            await callback.message.answer(
                text=text,
                reply_markup=get_portfolio_categories_keyboard(categories),
            )
        else:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_portfolio_categories_keyboard(categories),
            )
    await callback.answer()


@router.callback_query(PortfolioNavCallback.filter(F.action.in_(["category", "next", "prev"])))
async def cb_portfolio_view_item(
    callback: CallbackQuery,
    callback_data: PortfolioNavCallback,
    session: AsyncSession,
) -> None:
    """
    View works within a category with slider navigation strictly verifying master ownership.
    """
    category_id = callback_data.category_id
    index = callback_data.item_index
    master_id = await LegacyTenantResolver.get_master_id(session)
    portfolio_repo = PortfolioRepository(session)

    # Fetch items for this category ensuring tenant ownership
    items = await portfolio_repo.list_items(category_id=category_id, master_id=master_id)

    if not items:
        await callback.answer("В этой категории пока нет опубликованных работ", show_alert=True)
        return

    index = max(0, min(index, len(items) - 1))
    item = items[index]
    caption = item.caption or "Работа мастера 🌸"
    keyboard = get_portfolio_item_keyboard(
        category_id=category_id, current_index=index, total_count=len(items)
    )

    if callback.message:
        if callback.message.photo:
            # Edit existing photo message
            await callback.message.edit_media(
                media=InputMediaPhoto(media=item.telegram_file_id, caption=caption),
                reply_markup=keyboard,
            )
        else:
            # Send photo and remove old text
            await callback.message.delete()
            await callback.message.answer_photo(
                photo=item.telegram_file_id,
                caption=caption,
                reply_markup=keyboard,
            )
    await callback.answer()
