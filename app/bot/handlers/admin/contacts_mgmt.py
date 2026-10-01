"""Editable, tenant-scoped master contacts in the admin panel."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import AdminMenuCallback
from app.bot.states.admin import AdminContactsSG
from app.database.models.user import User
from app.services.master_authorization_service import MasterAuthorizationService
from app.services.master_contacts import CONTACT_FIELD_LABELS, MasterContactsService, render_contacts


router = Router(name="admin_contacts_mgmt")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


def _field(data: str | None, prefix: str) -> str | None:
    if not data or not data.startswith(prefix):
        return None
    field = data[len(prefix):]
    return field if field in CONTACT_FIELD_LABELS else None


async def _overview(session: AsyncSession, master_id: int) -> tuple[str, InlineKeyboardMarkup]:
    settings = await MasterContactsService(session).get(master_id)
    lines = ["<b>📞 Контакты мастера</b>", "", "Изменяйте каждое поле отдельно:", ""]
    for field, label in CONTACT_FIELD_LABELS.items():
        current = getattr(settings, field, None) if settings else None
        summary = current.strip() if current and current.strip() else None
        if summary and len(summary) > 160:
            summary = summary[:157] + "…"
        lines.append(f"<b>{label}:</b>\n{escape(summary) if summary else 'не заполнено'}\n")
    buttons = [
        [InlineKeyboardButton(text=f"{label} — изменить", callback_data=f"adm_contact:field:{field}")]
        for field, label in CONTACT_FIELD_LABELS.items()
    ]
    buttons.extend([
        [InlineKeyboardButton(text="👁 Предпросмотр", callback_data="adm_contact:preview")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminMenuCallback(action="settings").pack())],
    ])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "adm_contact:view")
async def cb_admin_contacts_view(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    await MasterAuthorizationService(session).require_admin(master_id, db_user.id)
    await state.clear()
    text, keyboard = await _overview(session, master_id)
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_contact:field:"))
async def cb_admin_contact_field(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    await MasterAuthorizationService(session).require_admin(master_id, db_user.id)
    await state.clear()
    field = _field(callback.data, "adm_contact:field:")
    if field is None:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    settings = await MasterContactsService(session).get(master_id)
    current = getattr(settings, field, None) if settings else None
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"adm_contact:start:{field}")],
        [InlineKeyboardButton(text="🗑 Очистить", callback_data=f"adm_contact:clear:{field}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_contact:view")],
    ])
    text = (
        f"<b>{CONTACT_FIELD_LABELS[field]}</b>\n\n"
        f"Текущее значение:\n{escape(current.strip()) if current and current.strip() else 'не заполнено'}"
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_contact:start:"))
async def cb_admin_contact_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    await MasterAuthorizationService(session).require_admin(master_id, db_user.id)
    field = _field(callback.data, "adm_contact:start:")
    if field is None:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    await state.set_state(AdminContactsSG.editing_value)
    await state.update_data(contact_field=field, contact_master_id=master_id)
    if callback.message:
        await callback.message.edit_text(
            text=f"<b>{CONTACT_FIELD_LABELS[field]}</b>\n\nОтправьте новое значение одним сообщением. Пустое поле можно удалить кнопкой «Очистить».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_contact:field:{field}")]
            ]),
        )
    await callback.answer()


@router.message(AdminContactsSG.editing_value, F.text)
async def msg_admin_contact_save(
    message: Message, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    data = await state.get_data()
    field = data.get("contact_field")
    if data.get("contact_master_id") != master_id or field not in CONTACT_FIELD_LABELS:
        await state.clear()
        await message.answer("Редактирование устарело. Откройте контакты заново.")
        return
    try:
        await MasterContactsService(session).update_field(master_id, db_user.id, field, message.text)
    except ValueError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}")
        return
    session.info.setdefault("post_commit", []).append(state.clear)
    text, keyboard = await _overview(session, master_id)
    session.info.setdefault("post_commit", []).append(
        lambda: message.answer(f"✅ Контакт сохранён.\n\n{text}", reply_markup=keyboard)
    )


@router.callback_query(F.data.startswith("adm_contact:clear:"))
async def cb_admin_contact_clear(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    field = _field(callback.data, "adm_contact:clear:")
    if field is None:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    await state.clear()
    await MasterContactsService(session).update_field(master_id, db_user.id, field, None)
    text, keyboard = await _overview(session, master_id)
    # The callback answer and refreshed card follow the middleware commit.
    async def show_saved() -> None:
        if callback.message:
            await callback.message.edit_text(text=text, reply_markup=keyboard)
        await callback.answer("Поле очищено")
    session.info.setdefault("post_commit", []).append(show_saved)


@router.callback_query(F.data == "adm_contact:preview")
async def cb_admin_contact_preview(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, master_id: int, db_user: User
) -> None:
    await MasterAuthorizationService(session).require_admin(master_id, db_user.id)
    await state.clear()
    text, keyboard = render_contacts(await MasterContactsService(session).get(master_id))
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()
