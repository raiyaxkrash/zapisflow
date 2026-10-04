"""Owner settings for the trusted current customer BotInstance."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.bot.filters import IsAdminFilter
from app.database.models.master import BotInstance
from app.database.models.user import User
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.master_authorization_service import MasterAuthorizationService
from app.services.exceptions import AccessDeniedError, ProvisioningWebhookError

router = Router(name="admin_bot_settings")
router.callback_query.filter(IsAdminFilter())

def mini_app_card(instance, prefix="adm_bot:miniapp:", back="adm_set:view"):
    state = "🟢 Включён" if instance.mini_app_enabled else "⚪ Выключен"
    desired = "off" if instance.mini_app_enabled else "on"
    return (f"⚙️ Настройки бота\n\nMini App\n{state}\n\n"
            "Запись в Telegram доступна независимо от Mini App.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Выключить Mini App" if desired == "off" else "Включить Mini App", callback_data=prefix+desired)],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=back)],
            ]))

@router.callback_query(F.data.startswith("adm_bot:miniapp:"))
async def cb_mini_app_settings(callback: CallbackQuery, session: AsyncSession, db_user: User, master_id: int, bot_instance: BotInstance | None = None):
    if not bot_instance or bot_instance.master_id != master_id:
        await callback.answer("Бот недоступен", show_alert=True)
        return
    try:
        await MasterAuthorizationService(session).require_owner(master_id, db_user.id)
        instance = await session.scalar(select(BotInstance).where(BotInstance.id == bot_instance.id, BotInstance.master_id == master_id, BotInstance.is_current.is_(True)))
        if not instance:
            raise AccessDeniedError("Бот недоступен")
        action = callback.data.rsplit(":", 1)[1]
        if action not in {"view", "on", "off"}:
            raise AccessDeniedError("Неизвестное действие")
        await callback.answer()
        if action != "view":
            instance = await BotProvisioningService(session).set_mini_app_enabled(instance.id, db_user.id, action == "on")
        from app.bot.keyboards.admin import AdminMenuCallback
        text, keyboard = mini_app_card(instance, back=AdminMenuCallback(action="settings").pack())
        if callback.message:
            # answer avoids 'message is not modified' rolling back repeated explicit actions.
            await callback.message.answer(text, reply_markup=keyboard)
    except (AccessDeniedError, ProvisioningWebhookError):
        await callback.answer("Настройка доступна владельцу подключённого бота. Повторите позже.", show_alert=True)
