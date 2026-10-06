"""
Start command, main menu and global cancel handlers for clients.
"""

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import MenuCallback, get_main_menu_keyboard
from app.database.models.master import BotInstance, BotInstanceStatus
from app.database.models.user import User

router = Router(name="client_start")


async def client_brand(session, instance):
    if session is None or instance is None:
        return None
    from app.database.models import Master
    from app.services.branding import branding_context

    master = await session.get(Master, instance.master_id)
    return await branding_context(session, master, instance) if master else None


def client_menu(is_admin, brand):
    return get_main_menu_keyboard(
        is_admin=is_admin,
        has_portfolio=brand["show_portfolio"] if brand else True,
        has_reviews=brand["show_reviews"] if brand else True,
        has_contacts=brand["show_contacts"] if brand else True,
        booking_label=brand["booking_cta_label"] if brand else "Записаться",
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    db_user: User,
    is_admin: bool,
    command: CommandObject | None = None,
    bot_instance: BotInstance | None = None,
    session: AsyncSession | None = None,
) -> None:
    """
    Handle /start command: greets user and renders the main inline menu.
    Supports /start admin deep link for owners/admins, and gates clients in SETUP_REQUIRED.
    """
    await state.clear()

    # 1. Support /start admin deep link (Section 39)
    if command and command.args == "admin" and is_admin and session:
        from app.bot.handlers.admin.dashboard import cmd_admin_dashboard

        await cmd_admin_dashboard(message, state, db_user, session)
        return

    brand = await client_brand(session, bot_instance)

    # 2. Customer behavior during SETUP_REQUIRED (Section 33)
    if (
        bot_instance
        and bot_instance.status == BotInstanceStatus.SETUP_REQUIRED
        and not is_admin
    ):
        first_name = html.escape(
            db_user.first_name if db_user else message.from_user.first_name
        )
        text = (
            f"Здравствуйте, {first_name}! 🌸\n\n"
            "💅 Онлайн-запись пока настраивается мастером.\n"
            "Пожалуйста, загляните чуть позже — запись скоро будет открыта!"
        )
        if brand:
            text = (
                html.escape(brand["brand_name"])
                + "\n\n"
                + text
                + "\n\nРаботает на ZapisFlow"
            )
        await message.answer(text=text)
        return

    first_name = html.escape(
        db_user.first_name if db_user else message.from_user.first_name
    )

    text = (
        f"Здравствуйте, {first_name}! 🌸\n\n"
        "Добро пожаловать в бот онлайн-записи к мастеру красоты.\n\n"
        "Здесь вы можете:\n"
        "• Ознакомиться с услугами и ценами\n"
        "• Посмотреть примеры работ в портфолио\n"
        "• Выбрать удобную дату и время для визита\n"
        "• Управлять своими записями\n\n"
        "Выберите интересующий раздел в меню ниже 👇"
    )

    if brand:
        text = (
            f"Здравствуйте, {first_name}!\n\n<b>{html.escape(brand['brand_name'])}</b>\n\n"
            + html.escape(
                brand["welcome_text"] or "Выберите услугу и удобное время для визита."
            )
            + "\n\nРаботает на ZapisFlow"
        )
    await message.answer(text=text, reply_markup=client_menu(is_admin, brand))


@router.callback_query(MenuCallback.filter(F.action == "main"))
async def cb_main_menu(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
    is_admin: bool,
    bot_instance: BotInstance | None = None,
    session: AsyncSession | None = None,
) -> None:
    """
    Return to main menu from any inline screen.
    """
    await state.clear()
    first_name = html.escape(
        db_user.first_name if db_user else callback.from_user.first_name
    )

    text = f"Главное меню 🌸\n\nРады видеть вас снова, {first_name}! Чем могу помочь?"

    brand = await client_brand(session, bot_instance)
    if brand:
        text = (
            html.escape(brand["brand_name"])
            + "\n\nЧто вы хотите сделать?\n\nРаботает на ZapisFlow"
        )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=client_menu(is_admin, brand),
        )
    await callback.answer()


@router.message(Command("cancel"))
@router.message(F.text.casefold() == "❌ отмена")
async def cmd_cancel(
    message: Message,
    state: FSMContext,
    db_user: User,
    is_admin: bool,
    bot_instance: BotInstance | None = None,
    session: AsyncSession | None = None,
) -> None:
    """
    Global cancellation handler that clears FSM state and removes reply keyboards.
    """
    await state.clear()

    await message.answer(
        "Действие отменено ↩️",
        reply_markup=ReplyKeyboardRemove(),
    )
    brand = await client_brand(session, bot_instance)
    await message.answer(
        "Вы вернулись в главное меню:",
        reply_markup=client_menu(is_admin, brand),
    )
