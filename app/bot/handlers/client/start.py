"""
Start command, main menu and global cancel handlers for clients.
"""

from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import MenuCallback, get_main_menu_keyboard
from app.database.models.master import BotInstance, BotInstanceStatus
from app.database.models.user import User

router = Router(name="client_start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    db_user: User,
    is_admin: bool,
    command: Optional[CommandObject] = None,
    bot_instance: Optional[BotInstance] = None,
    session: Optional[AsyncSession] = None,
) -> None:
    """
    Handle /start command: greets user and renders the main inline menu.
    Supports /start admin deep link for owners/admins, and gates clients in SETUP_REQUIRED.
    """
    await state.clear()

    # 1. Support /start admin deep link (Section 39)
    if command and command.args == "admin":
        if is_admin and session:
            from app.bot.handlers.admin.dashboard import cmd_admin_dashboard
            await cmd_admin_dashboard(message, state, db_user, session)
            return

    # 2. Customer behavior during SETUP_REQUIRED (Section 33)
    if bot_instance and bot_instance.status == BotInstanceStatus.SETUP_REQUIRED and not is_admin:
        first_name = db_user.first_name if db_user else message.from_user.first_name
        text = (
            f"Здравствуйте, {first_name}! 🌸\n\n"
            "💅 Онлайн-запись пока настраивается мастером.\n"
            "Пожалуйста, загляните чуть позже — запись скоро будет открыта!"
        )
        await message.answer(text=text)
        return

    first_name = db_user.first_name if db_user else message.from_user.first_name

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

    await message.answer(
        text=text,
        reply_markup=get_main_menu_keyboard(is_admin=is_admin),
    )


@router.callback_query(MenuCallback.filter(F.action == "main"))
async def cb_main_menu(
    callback: CallbackQuery, state: FSMContext, db_user: User, is_admin: bool
) -> None:
    """
    Return to main menu from any inline screen.
    """
    await state.clear()
    first_name = db_user.first_name if db_user else callback.from_user.first_name

    text = (
        f"Главное меню 🌸\n\n"
        f"Рады видеть вас снова, {first_name}! Чем могу помочь?"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_main_menu_keyboard(is_admin=is_admin),
        )
    await callback.answer()


@router.message(Command("cancel"))
@router.message(F.text.casefold() == "❌ отмена")
async def cmd_cancel(
    message: Message, state: FSMContext, db_user: User, is_admin: bool
) -> None:
    """
    Global cancellation handler that clears FSM state and removes reply keyboards.
    """
    current_state = await state.get_state()
    await state.clear()

    await message.answer(
        "Действие отменено ↩️",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Вы вернулись в главное меню:",
        reply_markup=get_main_menu_keyboard(is_admin=is_admin),
    )
