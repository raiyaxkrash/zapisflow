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
from app.services.crm_service import MasterCrmService
from app.services.master_contacts import contact_phone_e164, render_contacts
from app.services.telegram_outbox import bound_bot_instance_id, enqueue_telegram_contact

from app.bot.handlers.client.presentation import project_brand, section_available, brand_from_settings
from app.database.models import Master
from app.services.branding import branding_context

router = Router(name="client_about")


@router.callback_query(MenuCallback.filter(F.action == "about"))
async def cb_about_master(
    callback: CallbackQuery, session: AsyncSession, master_id: int
) -> None:
    """
    Display 'About Me' master profile, experience and studio address.
    """
    master = await session.get(Master, master_id)
    brand = await branding_context(session, master) if master else None
    text = "<b>О бизнесе</b>\n\n"
    if brand:
        text += html.escape(brand["brand_name"]) + "\n\n"
        text += html.escape(brand["description"] or "Описание пока не добавлено")
    else:
        text += "Описание пока не добавлено"
    rows = [[InlineKeyboardButton(
        text="📅 " + (brand["booking_cta_label"] if brand else "Записаться"),
        callback_data=MenuCallback(action="book").pack(),
    )]]
    if not brand or brand["show_portfolio"]:
        rows[0].append(InlineKeyboardButton(text="🖼 Портфолио", callback_data=MenuCallback(action="portfolio").pack()))
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data=MenuCallback(action="main").pack())])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

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
    config = await MasterSettingsRepository(session).get_by_master_id(master_id)
    if not await section_available(callback, session, master_id, "contacts", brand=brand_from_settings(config)):
        return
    text, keyboard = render_contacts(config)

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
    if not await section_available(callback, session, master_id, "contacts", brand=brand_from_settings(settings)):
        return
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


@router.callback_query(MenuCallback.filter(F.action == "reviews"))
async def cb_client_reviews(
    callback: CallbackQuery, session: AsyncSession, master_id: int
) -> None:
    """Display master reviews and rating summary for clients."""
    brand = await project_brand(session, master_id)
    if not await section_available(callback, session, master_id, "reviews", brand=brand):
        return
    crm_svc = MasterCrmService(session)
    summary = await crm_svc.get_reviews_summary(master_id, limit=5)

    avg_r = summary.get("average_rating")
    cnt = summary.get("total_reviews", 0)

    if cnt > 0 and avg_r is not None:
        stars_bar = "⭐" * round(avg_r)
        text = (
            f"⭐ <b>Отзывы о мастере</b>\n\n"
            f"Рейтинг: <b>{avg_r:.1f}</b> {stars_bar}\n"
            f"Всего отзывов: <b>{cnt}</b>\n\n"
            f"💬 <b>Последние отзывы:</b>\n\n"
        )
        for r in summary.get("recent_reviews", []):
            u_name = html.escape(r.get("user_name") or "Клиент")
            stars = "⭐" * r.get("rating", 5)
            dt_str = r.get("created_at") or ""
            c_text = f"\n«{html.escape(r['comment'])}»" if r.get("comment") else ""
            text += f"• <b>{u_name}</b> {stars} ({dt_str}){c_text}\n\n"
    else:
        text = (
            "⭐ <b>Отзывы клиентов</b>\n\n"
            "Пока отзывов нет. Вы сможете оставить свой отзыв сразу после посещения процедуры! ✨"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 " + brand["booking_cta_label"],
                    callback_data=MenuCallback(action="book").pack(),
                ),
                InlineKeyboardButton(
                    text="🏠 В главное меню",
                    callback_data=MenuCallback(action="main").pack(),
                ),
            ]
        ]
    )

    if callback.message:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text=text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()
