"""
Payment confirmation, receipt uploading and cancellation handlers.
"""

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import (
    BookingActionCallback,
    get_cancel_upload_keyboard,
    get_main_menu_keyboard,
)
from app.bot.states.client import ClientBookingSG
from app.config.settings import settings
from app.database.models.payment import MediaType
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.services.booking_service import BookingService
from app.services.master_authorization_service import MasterAuthorizationService
from app.services.payment_service import PaymentService
from app.services.telegram_outbox import (
    bound_bot_instance_id,
    enqueue_telegram_document,
    enqueue_telegram_message,
    enqueue_telegram_photo,
)
from app.utils.formatters import format_datetime_ru, format_rub

router = Router(name="client_payment")
logger = logging.getLogger(__name__)


@router.callback_query(BookingActionCallback.filter(F.action == "i_paid"))
async def cb_i_paid(
    callback: CallbackQuery,
    callback_data: BookingActionCallback,
    state: FSMContext,
) -> None:
    """
    Client pressed 'I paid': ask to upload proof (photo/document/screenshot).
    """
    appointment_id = callback_data.appointment_id
    await state.update_data(appointment_id=appointment_id)
    await state.set_state(ClientBookingSG.uploading_proof)

    text = (
        "🧾 <b>Подтверждение оплаты</b>\n\n"
        "Пожалуйста, отправьте <b>фотографию чека</b>, скриншот перевода или PDF-документ.\n\n"
        "Вы также можете добавить текстовый комментарий к фото (например, последние 4 цифры вашей карты)."
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_cancel_upload_keyboard(appointment_id),
        )
    await callback.answer()


@router.message(ClientBookingSG.uploading_proof, F.photo)
@router.message(ClientBookingSG.uploading_proof, F.document)
async def msg_receive_proof(
    message: Message,
    state: FSMContext,
    db_user: User,
    bot: Bot,
    session: AsyncSession, master_id: int,
) -> None:
    """
    Receive uploaded proof image or document, update booking status and notify admins.
    """
    data = await state.get_data()
    appointment_id = data.get("appointment_id")
    if not appointment_id:
        await message.answer(
            "Не удалось определить запись. Пожалуйста, откройте раздел «Мои записи».",
            reply_markup=get_main_menu_keyboard(),
        )
        await state.clear()
        return

    # Extract media file_id
    if message.photo:
        # Highest resolution photo
        telegram_file_id = message.photo[-1].file_id
        telegram_file_unique_id = message.photo[-1].file_unique_id
        media_type = MediaType.PHOTO
    elif message.document:
        telegram_file_id = message.document.file_id
        telegram_file_unique_id = message.document.file_unique_id
        media_type = MediaType.DOCUMENT
    else:
        await message.answer("Пожалуйста, отправьте фото чека или документ.")
        return

    user_comment = message.caption or None

    payment_service = PaymentService(session)
    appointment, payment, proof = await payment_service.submit_payment_proof(
        master_id=master_id,
        appointment_id=appointment_id,
        user_id=db_user.id,
        telegram_file_id=telegram_file_id,
        telegram_file_unique_id=telegram_file_unique_id,
        media_type=media_type,
        comment=user_comment,
    )

    # Prepare notification data while the transaction and loaded objects are available.
    settings_repo = MasterSettingsRepository(session)
    tz_str = await settings_repo.get_value(master_id, "timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)
    dep_str = format_rub(appointment.snapshot_deposit_amount)
    price_str = format_rub(appointment.snapshot_service_price)

    admin_caption = (
        f"🔥 <b>НОВАЯ ОПЛАТА ПО ЗАПИСИ #{appointment.id}</b>\n\n"
        f"👤 <b>Клиент:</b> {escape(db_user.first_name)} {escape(db_user.last_name or '')}\n"
        f"📱 <b>Телефон:</b> {escape(db_user.phone or 'Не указан')}\n"
        f"💬 <b>Username:</b> @{escape(db_user.username or 'отсутствует')}\n"
        f"🌸 <b>Услуга:</b> {escape(appointment.snapshot_service_title)}\n"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"💰 <b>Стоимость:</b> {price_str}\n"
        f"💳 <b>Предоплата:</b> {dep_str}\n"
    )
    if user_comment:
        admin_caption += f"💬 <b>Комментарий:</b> {escape(user_comment)}\n"

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    admin_keyboard = InlineKeyboardMarkup(
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
                    text="💬 Связаться с клиентом",
                    url=f"tg://user?id={db_user.telegram_id}",
                )
            ],
        ]
    )

    # The update ledger and proof are committed together by DbSessionMiddleware.
    # Clear Redis state only after that transaction succeeds.
    session.info.setdefault("post_commit", []).append(state.clear)

    bot_instance_id = bound_bot_instance_id(session, master_id)
    await enqueue_telegram_message(
        session,
        master_id=master_id,
        bot_instance_id=bot_instance_id,
        chat_id=db_user.telegram_id,
        text=(
            "Чек успешно получен! 🌸\n\n"
            "Мастер уже проверяет поступление перевода. "
            "Обычно это занимает от 5 до 30 минут.\n\n"
            "Как только оплата будет подтверждена, вам придёт уведомление ✅"
        ),
        idempotency_key=f"payment-proof:{proof.id}:client",
    )

    auth_service = MasterAuthorizationService(session)
    admin_recipients = await auth_service.get_admin_recipients(master_id=master_id)

    for admin_tg_id in admin_recipients:
        if media_type == MediaType.PHOTO:
            await enqueue_telegram_photo(
                session,
                master_id=master_id,
                bot_instance_id=bot_instance_id,
                chat_id=admin_tg_id,
                photo_file_id=telegram_file_id,
                caption=admin_caption,
                reply_markup=admin_keyboard,
                idempotency_key=f"payment-proof:{proof.id}:admin:{admin_tg_id}",
            )
        else:
            await enqueue_telegram_document(
                session,
                master_id=master_id,
                bot_instance_id=bot_instance_id,
                chat_id=admin_tg_id,
                document_file_id=telegram_file_id,
                caption=admin_caption,
                reply_markup=admin_keyboard,
                idempotency_key=f"payment-proof:{proof.id}:admin:{admin_tg_id}",
            )


@router.callback_query(BookingActionCallback.filter(F.action == "cancel_booking"))
async def cb_cancel_hold_booking(
    callback: CallbackQuery,
    callback_data: BookingActionCallback,
    state: FSMContext,
    db_user: User,
    session: AsyncSession, master_id: int,
) -> None:
    """
    Cancel booking on requisites screen before payment is confirmed.
    """
    booking_service = BookingService(session)

    try:
        await booking_service.cancel_booking_by_client(
            master_id=master_id,
            appointment_id=callback_data.appointment_id,
            user_id=db_user.id,
            reason="Отменено клиентом на экране оплаты",
        )
        session.info.setdefault("post_commit", []).append(state.clear)
        text = "Бронь успешно отменена. Слот освобождён ↩️"
    except Exception as e:
        text = f"Не удалось отменить запись: {e}"

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_main_menu_keyboard(),
        )
    await callback.answer()
