"""
About me and contact information handlers.
"""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import MenuCallback
from app.repositories.settings_repository import SettingsRepository

router = Router(name="client_about")


@router.callback_query(MenuCallback.filter(F.action == "about"))
async def cb_about_master(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """
    Display 'About Me' master profile, experience and studio address.
    """
    settings_repo = SettingsRepository(session)
    master_name = await settings_repo.get_value("master_name", "Анастасия")
    description = await settings_repo.get_value(
        "master_description",
        "Сертифицированный мастер ногтевого сервиса и эстетики с опытом более 5 лет. "
        "Использую только стерильные одноразовые расходники, премиальные материалы и современные техники.",
    )
    address = await settings_repo.get_value(
        "studio_address", "г. Москва, ул. Ленина, д. 25, студия 4"
    )
    experience = await settings_repo.get_value("master_experience", "5+ лет практики")

    text = (
        f"🌸 <b>О мастере — {master_name}</b>\n\n"
        f"✨ <b>Опыт:</b> {experience}\n\n"
        f"📝 <b>О себе:</b>\n{description}\n\n"
        f"📍 <b>Адрес студии:</b>\n{address}\n"
    )

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
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """
    Display contact methods and direct link to master.
    """
    settings_repo = SettingsRepository(session)
    phone = await settings_repo.get_value("default_phone_requisites", "+7 (999) 000-00-00")
    telegram_link = await settings_repo.get_value("master_telegram", "https://t.me/")
    address = await settings_repo.get_value(
        "studio_address", "г. Москва, ул. Ленина, д. 25, студия 4"
    )

    text = (
        "<b>📞 Контакты мастера</b>\n\n"
        "Если у вас есть вопросы по записи, индивидуальным дизайнам или вы хотите перенести визит — свяжитесь со мной удобным способом:\n\n"
        f"📱 <b>Телефон / WhatsApp:</b> <code>{phone}</code>\n"
        f"📍 <b>Адрес студии:</b> {address}\n"
        "🕒 <b>Режим работы:</b> с 10:00 до 19:00"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Написать в Telegram",
                    url=telegram_link,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Главное меню",
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
