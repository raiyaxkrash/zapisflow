"""
Admin handlers for payment verification, receipt validation and deposit approvals.
"""

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import AdminMenuCallback, get_admin_back_keyboard
from app.config.settings import settings
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.settings_repository import SettingsRepository
from app.services.payment_service import PaymentService
from app.utils.formatters import format_datetime_ru, format_rub

router = Router(name="admin_payments")
router.callback_query.filter(IsAdminFilter())


@router.callback_query(AdminMenuCallback.filter(F.action == "payments"))
async def cb_payments_inbox(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """
    List of payments waiting for review (inbox).
    """
    payment_service = PaymentService(session)
    pending_payments = await payment_service.list_pending_inbox()

    if not pending_payments:
        text = "<b>💳 Входящие чеки</b>\n\nВсе чеки проверены! Очередь пуста ✅"
        keyboard = get_admin_back_keyboard()
    else:
        text = (
            f"<b>💳 Входящие чеки на проверку ({len(pending_payments)} шт.)</b>\n\n"
            "Нажмите на платеж для просмотра и подтверждения:"
        )
        buttons = []
        for p in pending_payments:
            client_name = p.user.first_name if p.user else "Клиент"
            svc_title = p.appointment.snapshot_service_title if p.appointment else "Услуга"
            amt_str = format_rub(p.amount)
            buttons.append([
                InlineKeyboardButton(
                    text=f"#{p.appointment_id} {client_name} — {amt_str} ({svc_title})",
                    callback_data=f"adm_pay:view:{p.id}",
                )
            ])
        buttons.append([
            InlineKeyboardButton(
                text="◀️ В панель мастера",
                callback_data=AdminMenuCallback(action="dashboard").pack(),
            )
        ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_pay:view:"))
async def cb_view_payment_detail(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """
    Display details of an individual pending payment receipt from inbox.
    """
    payment_id = int(callback.data.split(":")[2])
    payment_repo = PaymentRepository(session)
    payment = await payment_repo.get_by_id_with_proofs(payment_id)

    if not payment or not payment.appointment:
        await callback.answer("Платёж или запись не найдены", show_alert=True)
        return

    app = payment.appointment
    user = app.user
    client_name = user.first_name if user else "Клиент"
    phone_str = user.phone if (user and user.phone) else "не указан"
    username_str = f"@{user.username}" if (user and user.username) else "нет"

    caption = (
        f"<b>💳 Проверка оплаты по записи #{app.id}</b>\n\n"
        f"👤 <b>Клиент:</b> {client_name}\n"
        f"📞 <b>Телефон:</b> {phone_str}\n"
        f"💬 <b>Username:</b> {username_str}\n"
        f"🌸 <b>Услуга:</b> {app.snapshot_service_title}\n"
        f"🗓 <b>Дата записи:</b> {app.start_time.strftime('%d.%m.%Y %H:%M')}\n"
        f"💰 <b>Сумма предоплаты:</b> {format_rub(payment.amount)}\n"
        f"📊 <b>Статус:</b> {payment.status.value}\n"
    )

    proof = payment.proofs[0] if payment.proofs else None
    if proof and proof.user_comment:
        caption += f"💬 <b>Комментарий клиента:</b> {proof.user_comment}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить оплату",
                    callback_data=f"adm_pay:approve:{payment.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"adm_pay:reject:{payment.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад к списку чеков",
                    callback_data=AdminMenuCallback(action="payments").pack(),
                )
            ],
        ]
    )

    # If proof has photo or document file_id, we can show it
    if proof and proof.file_id:
        if proof.media_type.value == "photo":
            await bot.send_photo(
                chat_id=callback.from_user.id,
                photo=proof.file_id,
                caption=caption,
                reply_markup=keyboard,
            )
            if callback.message:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
            await callback.answer()
            return
        elif proof.media_type.value == "document":
            await bot.send_document(
                chat_id=callback.from_user.id,
                document=proof.file_id,
                caption=caption,
                reply_markup=keyboard,
            )
            if callback.message:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
            await callback.answer()
            return

    if callback.message:
        await callback.message.edit_text(text=caption, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_pay:approve:"))
async def cb_approve_payment(
    callback: CallbackQuery, db_user: User, bot: Bot, session: AsyncSession
) -> None:
    """
    1-click approve payment: confirm payment, confirm appointment, notify client.
    """
    payment_id = int(callback.data.split(":")[2])
    payment_service = PaymentService(session)

    appointment, payment = await payment_service.approve_payment(
        payment_id=payment_id,
        admin_id=db_user.id,
    )

    settings_repo = SettingsRepository(session)
    tz_str = await settings_repo.get_value("timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)
    studio_address = await settings_repo.get_value(
        "studio_address", "г. Москва, ул. Ленина, д. 25, студия 4"
    )

    # 1. Update admin message text and remove action buttons
    confirmed_text = (
        f"✅ <b>ОПЛАТА ПОДТВЕРЖДЕНА!</b>\n\n"
        f"Запись #{appointment.id} переведена в статус <b>CONFIRMED</b>.\n"
        f"Клиент уведомлён 🌸"
    )
    if callback.message:
        if callback.message.caption:
            await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n{confirmed_text}")
        else:
            await callback.message.edit_text(text=confirmed_text)

    await callback.answer("Оплата успешно подтверждена ✅", show_alert=False)

    # 2. Notify client
    if appointment.user and appointment.user.telegram_id:
        client_text = (
            f"🎉 <b>Ваша запись подтверждена!</b> ✅\n\n"
            f"🌸 <b>Услуга:</b> {appointment.snapshot_service_title}\n"
            f"🗓 <b>Дата и время:</b> {dt_str}\n"
            f"📍 <b>Адрес студии:</b> {studio_address}\n\n"
            "Предоплата успешно зачислена. С нетерпением ждём вас! ❤️"
        )
        try:
            await bot.send_message(
                chat_id=appointment.user.telegram_id,
                text=client_text,
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("adm_pay:reject:"))
async def cb_reject_payment_presets(
    callback: CallbackQuery,
) -> None:
    """
    Prompt admin with quick presets for rejection reason.
    """
    payment_id = int(callback.data.split(":")[2])

    text = "<b>Выберите причину отклонения оплаты:</b>"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Перевод не поступил",
                    callback_data=f"adm_pay:do_reject:{payment_id}:not_received",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Неверная сумма предоплаты",
                    callback_data=f"adm_pay:do_reject:{payment_id}:wrong_amount",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Нечитаемый скриншот / чек",
                    callback_data=f"adm_pay:do_reject:{payment_id}:bad_proof",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Отмена (назад)",
                    callback_data=AdminMenuCallback(action="payments").pack(),
                )
            ],
        ]
    )

    if callback.message:
        if callback.message.caption:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        else:
            await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_pay:do_reject:"))
async def cb_do_reject_payment(
    callback: CallbackQuery, db_user: User, bot: Bot, session: AsyncSession
) -> None:
    """
    Execute rejection: mark payment rejected, extend appointment hold by 15 min, notify client.
    """
    parts = callback.data.split(":")
    payment_id = int(parts[2])
    preset_key = parts[3]

    reasons = {
        "not_received": "Перевод не поступил на банковский счёт мастера",
        "wrong_amount": "Сумма перевода не соответствует требуемой сумме предоплаты",
        "bad_proof": "Скриншот или чек не читаем либо не содержит деталей перевода",
    }
    reason_text = reasons.get(preset_key, "Платёж не прошёл проверку")

    payment_service = PaymentService(session)
    appointment, payment = await payment_service.reject_payment(
        payment_id=payment_id,
        admin_id=db_user.id,
        reason=reason_text,
        extend_hold_minutes=15,
    )

    reject_text = f"❌ <b>Оплата отклонена:</b> {reason_text}\nБронь продлена клиенту на 15 мин."
    if callback.message:
        if callback.message.caption:
            await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n{reject_text}")
        else:
            await callback.message.edit_text(text=reject_text)

    await callback.answer("Оплата отклонена ❌")

    # Notify client
    if appointment.user and appointment.user.telegram_id:
        client_text = (
            f"⚠️ <b>Предоплата по записи #{appointment.id} не подтверждена</b>\n\n"
            f"<b>Причина:</b> {reason_text}.\n\n"
            "⏳ Мы продлили время вашей брони на <b>15 минут</b>. "
            "Пожалуйста, проверьте перевод и отправьте корректный чек в разделе «Мои записи» 🌸"
        )
        try:
            await bot.send_message(
                chat_id=appointment.user.telegram_id,
                text=client_text,
            )
        except Exception:
            pass
