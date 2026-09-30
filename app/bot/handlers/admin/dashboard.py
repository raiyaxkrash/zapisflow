"""
Admin dashboard entry point and main menu handler.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import (
    AdminMenuCallback,
    get_admin_dashboard_keyboard,
)
from app.bot.keyboards.client import get_main_menu_keyboard
from app.database.models.user import User

router = Router(name="admin_dashboard")
# Apply admin filter to all routes in this router
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


@router.message(Command("admin"))
@router.callback_query(F.data == "admin:menu")
@router.callback_query(AdminMenuCallback.filter(F.action == "dashboard"))
async def cmd_admin_dashboard(
    event: Message | CallbackQuery,
    state: FSMContext,
    db_user: User,
    session: AsyncSession,
) -> None:
    """
    Open master administrative dashboard.
    """
    await state.clear()
    first_name = db_user.first_name if db_user else "Мастер"

    text = (
        f"<b>⚙️ Панель управления мастера — {first_name}</b>\n\n"
        "Выберите раздел для работы:\n"
        "• <b>Записи</b> — просмотр, перенос, подтверждение и отмена\n"
        "• <b>Входящие чеки</b> — быстрая сверка предоплат от клиентов\n"
        "• <b>Календарь</b> — настройка часов, перерывов и выходных дней\n"
        "• <b>Услуги</b> — редактирование прайса, буфера и длительности\n"
        "• <b>Клиенты</b> — поиск по базе, заметки, LTV и статистика"
    )

    keyboard = get_admin_dashboard_keyboard()

    if isinstance(event, Message):
        await event.answer(text=text, reply_markup=keyboard)
    else:
        if event.message:
            await event.message.edit_text(text=text, reply_markup=keyboard)
        await event.answer()


@router.callback_query(AdminMenuCallback.filter(F.action == "exit"))
async def cb_admin_exit(
    callback: CallbackQuery, state: FSMContext, is_admin: bool
) -> None:
    """
    Exit admin dashboard back to client view.
    """
    await state.clear()
    text = "Вы вышли из панели администратора в главное меню 🌸"
    if callback.message:
        await callback.message.edit_text(
            text=text, reply_markup=get_main_menu_keyboard(is_admin=is_admin)
        )
    await callback.answer()
