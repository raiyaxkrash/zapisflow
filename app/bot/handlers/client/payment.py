"""
Payment confirmation, receipt uploading and cancellation handlers.
"""

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
from app.repositories.settings_repository import SettingsRepository
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.utils.formatters import format_datetime_ru, format_rub

router = Router(name="client_payment")


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
    session: AsyncSession,
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
        appointment_id=appointment_id,
        user_id=db_user.id,
        telegram_file_id=telegram_file_id,
        telegram_file_unique_id=telegram_file_unique_id,
        media_type=media_type,
        comment=user_comment,
    )

    await state.clear()

    # Inform client
    await message.answer(
        "Чек успешно получен! 🌸\n\n"
        "Мастер уже проверяет поступление перевода. "
        "Обычно это занимает от 5 до 30 минут.\n\n"
        "Как только оплата будет подтверждена, вам придёт уведомление ✅",
        reply_markup=get_main_menu_keyboard(),
    )

    # Notify admins about new payment submission
    settings_repo = SettingsRepository(session)
    tz_str = await settings_repo.get_value("timezone", settings.timezone)
    dt_str = format_datetime_ru(appointment.start_time, tz_name=tz_str)
    dep_str = format_rub(appointment.snapshot_deposit_amount)
    price_str = format_rub(appointment.snapshot_service_price)

    admin_caption = (
        f"🔥 <b>НОВАЯ ОПЛАТА ПО ЗАПИСИ #{appointment.id}</b>\n\n"
        f"👤 <b>Клиент:</b> {db_user.first_name} {db_user.last_name or ''}\n"
        f"📱 <b>Телефон:</b> {db_user.phone or 'Не указан'}\n"
        f"💬 <b>Username:</b> @{db_user.username or 'отсутствует'}\n"
        f"🌸 <b>Услуга:</b> {appointment.snapshot_service_title}\n"
        f"🗓 <b>Дата и время:</b> {dt_str}\n"
        f"💰 <b>Стоимость:</b> {price_str}\n"
        f"💳 <b>Предоплата:</b> {dep_str}\n"
    )
    if user_comment:
        admin_caption += f"💬 <b>Комментарий:</b> {user_comment}\n"

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

    for admin_tg_id in settings.admin_ids:
        try:
            if media_type == MediaType.PHOTO:
                await bot.send_photo(
                    chat_id=admin_tg_id,
                    photo=telegram_file_id,
                    caption=admin_caption,
                    reply_markup=admin_keyboard,
                )
            else:
                await bot.send_document(
                    chat_id=admin_tg_id,
                    document=telegram_file_id,
                    caption=admin_caption,
                    reply_markup=admin_keyboard,
                )
        except Exception:
            pass  # Admin chat may be inactive or blocked


@router.callback_query(BookingActionCallback.filter(F.action == "cancel_booking"))
async def cb_cancel_hold_booking(
    callback: CallbackQuery,
    callback_data: BookingActionCallback,
    state: FSMContext,
    db_user: User,
    session: AsyncSession,
) -> None:
    """
    Cancel booking on requisites screen before payment is confirmed.
    """
    await state.clear()
    booking_service = BookingService(session)

    try:
        await booking_service.cancel_booking_by_client(
            appointment_id=callback_data.appointment_id,
            user_id=db_user.id,
            reason="Отменено клиентом на экране оплаты",
        )
        text = "Бронь успешно отменена. Слот освобождён ↩️"
    except Exception as e:
        text = f"Не удалось отменить запись: {e}"

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_main_menu_keyboard(),
        )
    await callback.answer()
