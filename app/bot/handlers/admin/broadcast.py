"""
Admin mass broadcast wizard and campaign execution handlers.
Interactive creation flow: text -> optional photo -> optional inline button -> preview -> execution.
"""

import re
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import AdminMenuCallback
from app.bot.states.admin import AdminBroadcastSG
from app.database.models.broadcast import BroadcastStatus
from app.database.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.broadcast_service import BroadcastService
from app.services.master_authorization_service import MasterAuthorizationService
from app.services.tenant_context import LegacyTenantResolver

router = Router(name="admin_broadcast")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


@router.callback_query(AdminMenuCallback.filter(F.action == "broadcast"))
async def cb_admin_broadcast_root(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """
    Open mass broadcast management menu with subscriber statistics.
    """
    await state.clear()
    master_id = await LegacyTenantResolver.get_master_id(session)
    broadcast_svc = BroadcastService(session)
    eligible_users = await broadcast_svc.get_eligible_users(master_id=master_id)

    text = (
        "<b>📢 Массовые рассылки клиентам</b>\n\n"
        f"👥 <b>Доступно получателей:</b> {len(eligible_users)} чел.\n"
        "<i>(клиенты, разрешившие рассылки и не заблокировавшие бота)</i>\n\n"
        "Рассылки отправляются с безопасной скоростью до 25 сообщ./сек., "
        "чтобы исключить блокировки Telegram."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Создать новую рассылку",
                    callback_data="adm_bc:new",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ В панель мастера",
                    callback_data=AdminMenuCallback(action="dashboard").pack(),
                )
            ],
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "adm_bc:new")
async def cb_admin_broadcast_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """
    Step 1: Prompt for message text.
    """
    await state.clear()
    await state.set_state(AdminBroadcastSG.entering_text)

    text = (
        "<b>📢 Создание рассылки (Шаг 1 из 3)</b>\n\n"
        "Отправьте <b>текст сообщения</b> для рассылки:\n"
        "<i>(поддерживается HTML-разметка: &lt;b&gt;, &lt;i&gt;, ссылки и эмодзи)</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data=AdminMenuCallback(action="broadcast").pack(),
                )
            ]
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(AdminBroadcastSG.entering_text, F.text)
async def msg_broadcast_text(
    message: Message, state: FSMContext
) -> None:
    """
    Step 2: Text received. Prompt for optional photo.
    """
    bc_text = message.text.strip()
    await state.update_data(text=bc_text)
    await state.set_state(AdminBroadcastSG.attaching_photo)

    prompt = (
        "<b>📢 Создание рассылки (Шаг 2 из 3)</b>\n\n"
        "Отправьте <b>фотографию</b> для прикрепления к сообщению\n"
        "или нажмите «Пропустить фото»:"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить фото",
                    callback_data="adm_bc:skip_photo",
                )
            ]
        ]
    )
    await message.answer(text=prompt, reply_markup=keyboard)


@router.message(AdminBroadcastSG.attaching_photo, F.photo)
async def msg_broadcast_photo(
    message: Message, state: FSMContext
) -> None:
    """
    Photo attached. Prompt for optional inline button.
    """
    photo_file_id = message.photo[-1].file_id
    await state.update_data(photo_file_id=photo_file_id)
    await prompt_button_step(message, state)


@router.callback_query(AdminBroadcastSG.attaching_photo, F.data == "adm_bc:skip_photo")
async def cb_broadcast_skip_photo(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """
    Skip photo step. Prompt for optional inline button.
    """
    await state.update_data(photo_file_id=None)
    await prompt_button_step(callback, state)


async def prompt_button_step(event: Message | CallbackQuery, state: FSMContext) -> None:
    """
    Prompt admin to add an optional inline button link.
    """
    await state.set_state(AdminBroadcastSG.setting_button)
    prompt = (
        "<b>📢 Создание рассылки (Шаг 3 из 3)</b>\n\n"
        "Хотите добавить кнопку со ссылкой под сообщением?\n"
        "Отправьте текст кнопки и ссылку через тире:\n"
        "<b>Текст кнопки - https://ваша-ссылка.ru</b>\n\n"
        "<i>Или нажмите «Пропустить кнопку»:</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить кнопку",
                    callback_data="adm_bc:skip_button",
                )
            ]
        ]
    )
    if isinstance(event, Message):
        await event.answer(text=prompt, reply_markup=keyboard)
    else:
        if event.message:
            await event.message.edit_text(text=prompt, reply_markup=keyboard)
        await event.answer()


@router.callback_query(AdminBroadcastSG.setting_button, F.data == "adm_bc:skip_button")
async def cb_broadcast_skip_button(
    callback: CallbackQuery, state: FSMContext, db_user: User, session: AsyncSession
) -> None:
    """
    Skip button step and show preview.
    """
    await state.update_data(button_text=None, button_url=None)
    await show_broadcast_preview(callback, state, db_user, session)


@router.message(AdminBroadcastSG.setting_button, F.text)
async def msg_broadcast_button(
    message: Message, state: FSMContext, db_user: User, session: AsyncSession
) -> None:
    """
    Parse button text and URL.
    """
    raw = message.text.strip()
    match = re.match(r"^(.+?)\s*-\s*(https?://\S+)$", raw)
    if not match:
        await message.answer(
            "⚠️ Неверный формат кнопки. Введите: <b>Текст кнопки - https://ссылка</b>\n"
            "или нажмите «Пропустить кнопку»:"
        )
        return

    b_text, b_url = match.groups()
    await state.update_data(button_text=b_text.strip(), button_url=b_url.strip())
    await show_broadcast_preview(message, state, db_user, session)


async def show_broadcast_preview(
    event: Message | CallbackQuery,
    state: FSMContext,
    db_user: User,
    session: AsyncSession,
) -> None:
    """
    Create campaign in DRAFT and render preview card.
    """
    data = await state.get_data()
    master_id = await LegacyTenantResolver.get_master_id(session)
    auth_service = MasterAuthorizationService(session)
    if not await auth_service.is_admin(master_id, db_user.id):
        if isinstance(event, Message):
            await event.answer("Доступ запрещен")
        else:
            await event.answer("Доступ запрещен", show_alert=True)
        return
    admin = await UserRepository(session).get_or_create_legacy_admin(db_user.id)
    broadcast_svc = BroadcastService(session)

    broadcast = await broadcast_svc.create_broadcast(
        master_id=master_id,
        text=data["text"],
        admin_id=admin.id,
        photo_file_id=data.get("photo_file_id"),
        button_text=data.get("button_text"),
        button_url=data.get("button_url"),
    )
    await state.update_data(broadcast_id=broadcast.id)
    await state.set_state(AdminBroadcastSG.confirming)

    info_header = (
        f"<b>👁 ПРЕДПРОСМОТР РАССЫЛКИ #{broadcast.id}</b>\n"
        f"👥 Получателей: <b>{broadcast.total_count} чел.</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Запустить отправку сейчас",
                    callback_data=f"adm_bc:send:{broadcast.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить рассылку",
                    callback_data="adm_bc:cancel",
                )
            ],
        ]
    )

    preview_text = info_header + broadcast.text

    if broadcast.photo_file_id:
        bot = event.bot if isinstance(event, Message) else event.message.bot
        chat_id = event.from_user.id
        await bot.send_photo(
            chat_id=chat_id,
            photo=broadcast.photo_file_id,
            caption=preview_text,
            reply_markup=confirm_keyboard,
        )
    else:
        if isinstance(event, Message):
            await event.answer(text=preview_text, reply_markup=confirm_keyboard)
        else:
            if event.message:
                await event.message.edit_text(text=preview_text, reply_markup=confirm_keyboard)
            await event.answer()


@router.callback_query(F.data.startswith("adm_bc:send:"))
async def cb_broadcast_execute(
    callback: CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession
) -> None:
    """
    Execute broadcast dispatch.
    """
    broadcast_id = int(callback.data.split(":")[2])
    await state.clear()

    if callback.message:
        if callback.message.caption:
            await callback.message.edit_caption(
                caption=f"{callback.message.caption}\n\n⏳ <b>Рассылка запущена в фоновом режиме...</b>"
            )
        else:
            await callback.message.edit_text(
                text=f"{callback.message.text}\n\n⏳ <b>Рассылка запущена в фоновом режиме...</b>"
            )

    await callback.answer("Рассылка запущена! 🚀", show_alert=False)

    master_id = await LegacyTenantResolver.get_master_id(session)
    broadcast_svc = BroadcastService(session)
    completed_bc = await broadcast_svc.execute_broadcast(
        master_id=master_id, broadcast_id=broadcast_id, bot=bot
    )

    if completed_bc.status == BroadcastStatus.SENDING:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=(
                f"⏳ Рассылка #{completed_bc.id} уже выполняется. "
                f"Отправлено: {completed_bc.success_count}, "
                f"ошибок: {completed_bc.fail_count}."
            ),
        )
        return

    summary_text = (
        f"🎉 <b>Рассылка #{completed_bc.id} успешно завершена!</b>\n\n"
        f"• Всего получателей: <b>{completed_bc.total_count}</b>\n"
        f"• Успешно доставлено: <b>{completed_bc.success_count}</b> ✅\n"
        f"• Ошибок (заблокировали): <b>{completed_bc.fail_count}</b> ❌"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ В панель мастера",
                    callback_data=AdminMenuCallback(action="dashboard").pack(),
                )
            ]
        ]
    )
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=summary_text,
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "adm_bc:cancel")
async def cb_broadcast_cancel(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """
    Cancel broadcast draft.
    """
    await state.clear()
    text = "Рассылка отменена."
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ В меню рассылок",
                    callback_data=AdminMenuCallback(action="broadcast").pack(),
                )
            ]
        ]
    )
    if callback.message:
        if callback.message.caption:
            await callback.message.edit_caption(caption=text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer("Рассылка отменена")
