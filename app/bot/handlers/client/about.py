"""
About me and contact information handlers.
"""

import html

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import MenuCallback
from app.config.settings import settings as app_settings
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.services.master_contacts import contact_phone_e164, render_contacts
from app.services.telegram_outbox import bound_bot_instance_id, enqueue_telegram_contact

router = Router(name="client_about")


@router.callback_query(MenuCallback.filter(F.action == "about"))
async def cb_about_master(
    callback: CallbackQuery, session: AsyncSession, master_id: int
) -> None:
    """
    Display 'About Me' master profile, experience and studio address.
    """
    settings_repo = MasterSettingsRepository(session)
    master_name = await settings_repo.get_value(master_id, "master_name", "Анастасия")
    description = await settings_repo.get_value(
        master_id,
        "master_description",
        "Сертифицированный мастер ногтевого сервиса и эстетики с опытом более 5 лет. "
        "Использую только стерильные одноразовые расходники, премиальные материалы и современные техники.",
    )
    address = await settings_repo.get_value(master_id, "studio_address")
    experience = await settings_repo.get_value(master_id, "master_experience", "5+ лет практики")

    text = (
        f"🌸 <b>О мастере — {html.escape(str(master_name))}</b>\n\n"
        f"✨ <b>Опыт:</b> {html.escape(str(experience))}\n\n"
        f"📝 <b>О себе:</b>\n{html.escape(str(description))}\n"
    )
    if address:
        text += f"\n📍 <b>Адрес студии:</b>\n{html.escape(str(address))}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Записаться",
                    callback_data=MenuCallback(action="book").pack(),
                ),
                InlineKeyboardButton(
                    text="🖼 Портфолио",
                    callback_data=MenuCallback(action="portfolio").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏠 В главное меню",
                    callback_data=MenuCallback(action="main").pack(),
                )
            ],
        ]
    )

    if callback.message:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text=text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(MenuCallback.filter(F.action == "contact"))
async def cb_contact_master(
    callback: CallbackQuery, session: AsyncSession, master_id: int
) -> None:
    """Display the tenant's configured contacts without placeholder values."""
    settings_repo = MasterSettingsRepository(session)
    text, keyboard = render_contacts(await settings_repo.get_by_master_id(master_id))

    if callback.message:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text=text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "contact:call")
async def cb_contact_call(
    callback: CallbackQuery, session: AsyncSession, master_id: int
) -> None:
    """Send a native Telegram contact card for the configured phone."""
    settings = await MasterSettingsRepository(session).get_by_master_id(master_id)
    phone = contact_phone_e164(settings.studio_phone if settings else None)
    if not phone or not callback.message:
        await callback.answer("Телефон мастера недоступен", show_alert=True)
        return
    if app_settings.app_mode == "webhook":
        scope = session.info.get("webhook_update_scope")
        update_id = session.info.get("webhook_update_id")
        if not scope or not isinstance(update_id, int):
            await callback.answer("Контакт временно недоступен", show_alert=True)
            return
        await enqueue_telegram_contact(
            session,
            master_id=master_id,
            bot_instance_id=bound_bot_instance_id(session, master_id),
            chat_id=callback.message.chat.id,
            phone_number=phone,
            first_name="Мастер",
            idempotency_key=f"contact-card:{scope}:{update_id}",
        )
        session.info.setdefault("post_commit", []).append(lambda: callback.answer("Контакт отправляется"))
    else:
        await callback.message.answer_contact(phone_number=phone, first_name="Мастер")
        await callback.answer()
