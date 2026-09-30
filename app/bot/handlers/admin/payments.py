"""
Admin handlers for payment verification, receipt validation and deposit approvals.
"""

from html import escape
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import AdminMenuCallback, get_admin_back_keyboard
from app.config.settings import settings
from app.database.models.user import User
from app.database.models.payment import MediaType, PaymentStatus
from app.database.models.appointment import AppointmentStatus
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.user_repository import UserRepository
from app.services.payment_service import PaymentService
from app.services.exceptions import InvalidBookingStatusError
from app.services.tenant_context import LegacyTenantResolver
from app.utils.formatters import format_datetime_ru, format_rub

router = Router(name="admin_payments")
router.callback_query.filter(IsAdminFilter())
logger = logging.getLogger(__name__)


@router.callback_query(AdminMenuCallback.filter(F.action == "payments"))
async def cb_payments_inbox(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """
    List of payments waiting for review (inbox).
    """
    master_id = await LegacyTenantResolver.get_master_id(session)
    payment_service = PaymentService(session)
    pending_payments = await payment_service.list_pending_inbox(master_id=master_id)

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
            cancelled_mark = " [отменена]" if p.appointment.status == AppointmentStatus.CANCELLED_BY_CLIENT else ""
            buttons.append([
                InlineKeyboardButton(
                    text=f"#{p.appointment_id} {client_name} — {amt_str} ({svc_title}){cancelled_mark}",
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
    master_id = await LegacyTenantResolver.get_master_id(session)
    payment_repo = PaymentRepository(session)
    payment = await payment_repo.get_by_id_with_proofs(payment_id, master_id=master_id)

    if not payment or not payment.appointment:
        await callback.answer("Платёж или запись не найдены", show_alert=True)
        return

    app = payment.appointment
    user = app.user
    client_name = escape(user.first_name) if user else "Клиент"
    phone_str = escape(user.phone) if (user and user.phone) else "не указан"
    username_str = f"@{escape(user.username)}" if (user and user.username) else "нет"

    caption = (
        f"<b>💳 Проверка оплаты по записи #{app.id}</b>\n\n"
        f"👤 <b>Клиент:</b> {client_name}\n"
        f"📞 <b>Телефон:</b> {phone_str}\n"
        f"💬 <b>Username:</b> {username_str}\n"
        f"🌸 <b>Услуга:</b> {escape(app.snapshot_service_title)}\n"
        f"🗓 <b>Дата записи:</b> {app.start_time.strftime('%d.%m.%Y %H:%M')}\n"
        f"💰 <b>Сумма предоплаты:</b> {format_rub(payment.amount)}\n"
        f"📊 <b>Статус:</b> {payment.status.value}\n"
    )

    proof = max(payment.proofs, key=lambda item: item.id) if payment.proofs else None
    if payment.proofs:
        caption += f"🧾 <b>Загружено чеков:</b> {len(payment.proofs)} (показан последний)\n"
    if app.status == AppointmentStatus.CANCELLED_BY_CLIENT:
        caption += "⚠️ <b>Запись отменена клиентом; поступление денег ещё требует проверки.</b>\n"
    if proof and proof.user_comment:
        caption += f"💬 <b>Комментарий клиента:</b> {escape(proof.user_comment)}\n"

    action_buttons = []
    if payment.status == PaymentStatus.SUBMITTED:
        if app.status == AppointmentStatus.CANCELLED_BY_CLIENT:
            action_buttons = [
                InlineKeyboardButton(
                    text="✅ Перевод поступил — удержать",
                    callback_data=f"adm_pay:retain_cancelled:{payment.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Перевод не поступил",
                    callback_data=f"adm_pay:reject_cancelled:{payment.id}",
                ),
            ]
        elif app.status == AppointmentStatus.PAYMENT_PROOF_SENT:
            action_buttons = [
                InlineKeyboardButton(
                    text="✅ Подтвердить оплату",
                    callback_data=f"adm_pay:approve:{payment.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"adm_pay:reject:{payment.id}",
                ),
            ]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            *([action_buttons] if action_buttons else []),
            [
                InlineKeyboardButton(
                    text="◀️ Назад к списку чеков",
                    callback_data=AdminMenuCallback(action="payments").pack(),
                )
            ],
        ]
    )

    # If proof has photo or document file_id, we can show it
    if proof and proof.telegram_file_id:
        if proof.media_type == MediaType.PHOTO:
            await bot.send_photo(
                chat_id=callback.from_user.id,
                photo=proof.telegram_file_id,
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
        elif proof.media_type == MediaType.DOCUMENT:
            await bot.send_document(
                chat_id=callback.from_user.id,
                document=proof.telegram_file_id,
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
    admin = await UserRepository(session).get_active_admin_by_user_id(db_user.id)
    if admin is None:
        await callback.answer("Профиль администратора не найден", show_alert=True)
        return
    payment_service = PaymentService(session)

    master_id = await LegacyTenantResolver.get_master_id(session)
    try:
        decision = await payment_service.approve_payment(
            payment_id=payment_id,
            admin_id=admin.id,
            master_id=master_id,
        )
    except InvalidBookingStatusError:
        await callback.answer("Запись отменена. Проверьте перевод во входящих чеках.", show_alert=True)
        return
    if not decision.changed:
        response = (
            "Платёж уже подтверждён"
            if decision.payment.status in {PaymentStatus.CONFIRMED, PaymentStatus.RETAINED}
            else "Чек уже отклонён"
        )
        await callback.answer(response, show_alert=True)
        return
    appointment = decision.appointment

    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)
    studio_address = await settings_repo.get_value(
        master_id, "studio_address", "г. Москва, ул. Ленина, д. 25, студия 4"
    )
    await session.commit()

    # 1. Update admin message text and remove action buttons
    confirmed_text = (
        f"✅ <b>ОПЛАТА ПОДТВЕРЖДЕНА!</b>\n\n"
        f"Запись #{appointment.id} переведена в статус <b>CONFIRMED</b>.\n"
        "Изменения сохранены."
    )
    if callback.message:
        try:
            if callback.message.caption:
                await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n{confirmed_text}")
            else:
                await callback.message.edit_text(text=confirmed_text)
        except Exception:
            logger.exception("Failed to update admin approval message for payment %s", payment_id)

    try:
        await callback.answer("Оплата успешно подтверждена ✅", show_alert=False)
    except Exception:
        logger.exception("Failed to answer admin approval callback for payment %s", payment_id)

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
            logger.exception("Failed to notify client of approved payment %s", payment_id)


@router.callback_query(F.data.startswith("adm_pay:reject:"))
async def cb_reject_payment_presets(
    callback: CallbackQuery, session: AsyncSession,
) -> None:
    """
    Prompt admin with quick presets for rejection reason.
    """
    payment_id = int(callback.data.split(":")[2])
    master_id = await LegacyTenantResolver.get_master_id(session)
    payment = await PaymentRepository(session).get_by_id_with_proofs(payment_id, master_id=master_id)
    if payment is None:
        await callback.answer("Платёж не найден", show_alert=True)
        return
    if payment.status in {PaymentStatus.CONFIRMED, PaymentStatus.RETAINED}:
        await callback.answer("Платёж уже подтверждён", show_alert=True)
        return
    if payment.status == PaymentStatus.REJECTED:
        await callback.answer("Чек уже отклонён", show_alert=True)
        return
    if payment.status != PaymentStatus.SUBMITTED:
        await callback.answer("Чек ещё не отправлен на проверку", show_alert=True)
        return
    if payment.appointment.status == AppointmentStatus.CANCELLED_BY_CLIENT:
        await callback.answer("Запись отменена. Проверьте перевод во входящих чеках.", show_alert=True)
        return

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

    admin = await UserRepository(session).get_active_admin_by_user_id(db_user.id)
    if admin is None:
        await callback.answer("Профиль администратора не найден", show_alert=True)
        return

    master_id = await LegacyTenantResolver.get_master_id(session)
    payment_service = PaymentService(session)
    try:
        decision = await payment_service.reject_payment(
            payment_id=payment_id,
            admin_id=admin.id,
            reason=reason_text,
            extend_hold_minutes=15,
            master_id=master_id,
        )
    except InvalidBookingStatusError:
        await callback.answer("Запись отменена. Проверьте перевод во входящих чеках.", show_alert=True)
        return
    if not decision.changed:
        response = (
            "Платёж уже подтверждён"
            if decision.payment.status in {PaymentStatus.CONFIRMED, PaymentStatus.RETAINED}
            else "Чек уже отклонён"
        )
        await callback.answer(response, show_alert=True)
        return
    appointment = decision.appointment
    await session.commit()

    reject_text = f"❌ <b>Оплата отклонена:</b> {reason_text}\nБронь продлена клиенту на 15 мин."
    if callback.message:
        try:
            if callback.message.caption:
                await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n{reject_text}")
            else:
                await callback.message.edit_text(text=reject_text)
        except Exception:
            logger.exception("Failed to update admin rejection message for payment %s", payment_id)

    try:
        await callback.answer("Оплата отклонена ❌")
    except Exception:
        logger.exception("Failed to answer admin rejection callback for payment %s", payment_id)

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
            logger.exception("Failed to notify client of rejected payment %s", payment_id)


@router.callback_query(F.data.startswith("adm_pay:retain_cancelled:"))
@router.callback_query(F.data.startswith("adm_pay:reject_cancelled:"))
async def cb_resolve_cancelled_payment(
    callback: CallbackQuery, db_user: User, bot: Bot, session: AsyncSession
) -> None:
    """Resolve a submitted proof after client cancellation without inventing income."""
    payment_id = int(callback.data.rsplit(":", 1)[1])
    received = callback.data.startswith("adm_pay:retain_cancelled:")
    admin = await UserRepository(session).get_active_admin_by_user_id(db_user.id)
    if admin is None:
        await callback.answer("Профиль администратора не найден", show_alert=True)
        return

    master_id = await LegacyTenantResolver.get_master_id(session)
    decision = await PaymentService(session).resolve_cancelled_payment(
        payment_id=payment_id,
        admin_id=admin.id,
        received=received,
        master_id=master_id,
    )
    if not decision.changed:
        await callback.answer("Платёж уже обработан", show_alert=True)
        return
    await session.commit()

    result_text = (
        "✅ Перевод по отменённой записи подтверждён; предоплата удержана."
        if received
        else "❌ Перевод по отменённой записи не поступил."
    )
    if callback.message:
        try:
            if callback.message.caption:
                await callback.message.edit_caption(
                    caption=f"{callback.message.caption}\n\n{result_text}"
                )
            else:
                await callback.message.edit_text(text=result_text)
        except Exception:
            logger.exception("Failed to update cancelled payment message for %s", payment_id)
    try:
        await callback.answer("Платёж обработан", show_alert=False)
    except Exception:
        logger.exception("Failed to answer cancelled payment callback for %s", payment_id)

    user = decision.appointment.user
    if user and user.telegram_id > 0:
        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    "Предоплата по отменённой записи подтверждена и удержана согласно правилам."
                    if received
                    else "Перевод по отменённой записи не найден; предоплата не зачтена."
                ),
            )
        except Exception:
            logger.exception("Failed to notify client of cancelled payment %s", payment_id)
