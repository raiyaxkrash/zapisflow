"""Command and callback handlers for platform Manager Bot."""

from datetime import datetime, timedelta, timezone
from html import escape
import logging
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.security import mask_token
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstanceStatus, Master, MasterSettings, MasterStatus, SubscriptionStatus
from app.database.models.subscription import EffectiveSubscriptionStatus, SubscriptionPayment, SubscriptionPlan
from app.database.models.user import User
from app.manager_bot.keyboards import (
    admin_bot_detail_keyboard,
    admin_bots_keyboard,
    admin_dashboard_keyboard,
    admin_menu_keyboard,
    admin_metrics_keyboard,
    admin_payment_detail_keyboard,
    admin_payments_keyboard,
    admin_plan_detail_keyboard,
    admin_plans_keyboard,
    admin_project_detail_keyboard,
    admin_projects_keyboard,
    admin_subscriptions_keyboard,
    admin_user_detail_keyboard,
    admin_users_keyboard,
    bot_delete_confirm_keyboard,
    cancel_keyboard,
    confirm_connect_keyboard,
    confirm_disable_bot_keyboard,
    main_menu_keyboard,
    project_card_keyboard,
    project_list_keyboard,
    subscription_card_keyboard,
    subscription_canceled_keyboard,
    subscription_checkout_keyboard,
    subscription_payment_keyboard,
    subscription_pending_keyboard,
    subscription_projects_keyboard,
    subscription_success_keyboard,
)
from app.services.bot_registry import BotRegistry
from app.services.platform_admin_service import PlatformAdminService
from app.services.rate_limiter import check_rate_limit
from app.services.subscription_service import SubscriptionService
from app.database.session import async_session_factory
from app.services.billing.yookassa_checkout import YooKassaCheckoutService
from app.services.billing.yookassa_client import YooKassaClient, YooKassaGatewayError
from app.manager_bot.states import ConnectBotStates, CreateMasterStates, RotateTokenStates
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditEvent, AuditService
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.exceptions import (
    AccessDeniedError,
    BillingIDORViolationError,
    DuplicateBotError,
    InvalidBotTokenError,
    ManagerTokenCollisionError,
    ProvisioningWebhookError,
    SubscriptionError,
    TelegramGatewayError,
    TokenRotationBotMismatchError,
)
from app.services.master_readiness_service import MasterReadinessService
from app.services.telegram_provisioning_gateway import BotIdentity, TelegramProvisioningGateway

logger = logging.getLogger("app.manager_bot")

manager_router = Router(name="manager_bot_router")


# ---------------------------------------------------------------------------
# Helper: resolve owner User
# ---------------------------------------------------------------------------

async def _get_or_create_user(session: AsyncSession, from_user: Any) -> User:
    """Ensure user exists in database and return ORM instance."""
    user_repo = UserRepository(session)
    user, _ = await user_repo.get_or_create(
        telegram_id=from_user.id,
        first_name=from_user.first_name or "",
        last_name=from_user.last_name or "",
        username=from_user.username or "",
    )
    return user


# ---------------------------------------------------------------------------
# Navigation & Start
# ---------------------------------------------------------------------------

@manager_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Manager Bot /start entry point."""
    await state.clear()
    user = await _get_or_create_user(session, message.from_user)
    master_repo = MasterRepository(session)
    masters = await master_repo.list_by_owner_id(user.id)
    admin_svc = PlatformAdminService(session)
    is_admin = await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id)

    if not masters:
        text = (
            "👋 <b>Добро пожаловать в Beauty Bot Manager!</b>\n\n"
            "Здесь вы можете создать собственный проект и подключить Telegram-бота "
            "для автоматической записи клиентов в вашу студию красоты.\n\n"
            "Нажмите кнопку ниже, чтобы создать свой первый проект:"
        )
        await message.answer(text, reply_markup=main_menu_keyboard(is_platform_admin=is_admin))
    else:
        text = (
            f"👋 Здравствуйте, <b>{escape(user.first_name)}</b>!\n\n"
            "Выберите проект для управления или создайте новый:"
        )
        await message.answer(text, reply_markup=project_list_keyboard(masters))


@manager_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Manager Bot /cancel command to clear active FSM state."""
    await state.clear()
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session)
    is_admin = await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id)
    await message.answer("Действие отменено.", reply_markup=main_menu_keyboard(is_platform_admin=is_admin))


@manager_router.callback_query(F.data == "mgr:menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Return to main menu."""
    await state.clear()
    user = await _get_or_create_user(session, callback.from_user)
    admin_svc = PlatformAdminService(session)
    is_admin = await admin_svc.is_platform_admin(telegram_id=callback.from_user.id, user_id=user.id)
    text = f"<b>Beauty Bot Manager</b> — панель управления проектами ({escape(user.first_name)}):"
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_platform_admin=is_admin))
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


@manager_router.callback_query(F.data == "mgr:help")
async def cb_help(callback: CallbackQuery) -> None:
    """Help info about the manager bot."""
    text = (
        "❓ <b>Справка по управлению:</b>\n\n"
        "1. <b>Создайте проект</b> — укажите название вашей студии.\n"
        "2. <b>Подключите бота</b> — создайте бота в @BotFather и пришлите его токен.\n"
        "3. <b>Настройте услуги и расписание</b> — откройте созданного бота по deep link.\n"
        "4. <b>Запустите приём записей</b> — когда всё готово, активируйте бота в этом меню."
    )
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


@manager_router.callback_query(F.data == "mgr:projects")
async def cb_projects(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """List of all owned master projects."""
    await state.clear()
    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    masters = await master_repo.list_by_owner_id(user.id)

    await callback.answer()

    try:
        if not masters:
            text = "У вас пока нет созданных проектов. Создайте свой первый проект:"
            await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
        else:
            text = "📁 <b>Ваши проекты:</b>\nВыберите проект для управления или настройки бота:"
            await callback.message.edit_text(text, reply_markup=project_list_keyboard(masters))
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


# ---------------------------------------------------------------------------
# Project Creation Flow
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data == "mgr:master:new")
async def cb_new_master(callback: CallbackQuery, state: FSMContext) -> None:
    """Start project creation flow."""
    await state.set_state(CreateMasterStates.waiting_for_name)
    text = (
        "✏️ <b>Введите название нового проекта</b>\n\n"
        "Например: <i>Студия маникюра Анны</i> или <i>Barber Shop Deluxe</i>:"
    )
    await callback.message.edit_text(text, reply_markup=cancel_keyboard())
    await callback.answer()


@manager_router.message(CreateMasterStates.waiting_for_name)
async def msg_new_master_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Receive name and create new Master."""
    name = (message.text or "").strip()
    if not name or len(name) < 2 or len(name) > 128:
        await message.answer(
            "Название должно быть длиной от 2 до 128 символов. Попробуйте еще раз:",
            reply_markup=cancel_keyboard(),
        )
        return

    user = await _get_or_create_user(session, message.from_user)

    master = Master(
        owner_user_id=user.id,
        display_name=name,
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.EXPIRED,
        trial_ends_at=None,
        timezone="Europe/Moscow",
    )
    session.add(master)
    await session.flush()

    # Create associated default MasterSettings
    master_settings = MasterSettings(master_id=master.id)
    session.add(master_settings)
    await session.flush()

    # Atomically evaluate trial eligibility per user account
    sub_service = SubscriptionService(session)
    trial_granted = await sub_service.claim_user_trial_or_reject(
        user_id=user.id,
        master=master,
        trial_days=settings.trial_duration_days,
    )

    audit = AuditService(session)
    await audit.log_event(
        action=AuditEvent.MASTER_CREATED,
        actor_user_id=user.id,
        master_id=master.id,
        entity_type="Master",
        entity_id=master.id,
        payload_after={
            "display_name": name,
            "subscription_status": master.subscription_status.value,
            "trial_ends_at": master.trial_ends_at.isoformat() if master.trial_ends_at else None,
        },
    )
    if session.info.get("webhook_update_scope") is not None:
        session.info.setdefault("post_commit", []).append(state.clear)
    else:
        await state.clear()

    if trial_granted and master.trial_ends_at:
        trial_text = f"Пробный период действует до: <b>{master.trial_ends_at.strftime('%d.%m.%Y')}</b>.\n\n"
    else:
        trial_text = (
            "⚠️ <i>Пробный период для вашего аккаунта уже был использован ранее.</i>\n"
            "Для запуска приёма клиентов оформите подписку в разделе «Подписка».\n\n"
        )

    text = (
        f"✅ <b>Проект «{name}» успешно создан!</b>\n\n"
        f"{trial_text}"
        "Следующий шаг — подключите Telegram-бота для приёма записей клиентов."
    )
    bot_repo = BotInstanceRepository(session)
    bot_instance = await bot_repo.get_current_for_master(master.id)
    keyboard = project_card_keyboard(master, bot_instance)
    if session.info.get("webhook_update_scope") is not None:
        session.info.setdefault("post_commit", []).append(
            lambda: message.answer(text, reply_markup=keyboard)
        )
    else:
        await message.answer(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Project Card & Management
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:master:"))
async def cb_project_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Show details of a specific Master project."""
    await state.clear()
    master_id = int(callback.data.split(":")[2])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    bot_repo = BotInstanceRepository(session)
    bot_instance = await bot_repo.get_current_for_master(master.id)
    readiness_service = MasterReadinessService(session)
    is_ready, missing = await readiness_service.check(master.id)

    status_name = {
        BotInstanceStatus.PROVISIONING: "⏳ Подключение (PROVISIONING)",
        BotInstanceStatus.SETUP_REQUIRED: "🟡 Настройка (SETUP_REQUIRED)",
        BotInstanceStatus.ACTIVE: "🟢 Активен (ACTIVE)",
        BotInstanceStatus.ERROR: "🔴 Ошибка (ERROR)",
        BotInstanceStatus.DISABLED: "⚪️ Отключён (DISABLED)",
    }

    bot_info = "❌ Не подключён"
    bot_status_str = "—"
    if bot_instance:
        username_part = f"@{bot_instance.telegram_username}" if bot_instance.telegram_username else f"ID {bot_instance.telegram_bot_id}"
        bot_info = f"<b>{bot_instance.telegram_first_name or 'Бот'}</b> ({username_part})"
        bot_status_str = status_name.get(bot_instance.status, bot_instance.status.value)
        if bot_instance.last_error:
            bot_status_str += f"\n<i>Ошибка: {bot_instance.last_error}</i>"

    readiness_text = "5/5 (готов к приёму клиентов)" if is_ready else f"Нужно настроить: {len(missing)} пункт(ов)"

    sub_service = SubscriptionService(session)
    eff_sub = await sub_service.get_effective_status(master.id)
    if eff_sub.status == EffectiveSubscriptionStatus.TRIAL_ACTIVE:
        date_str = eff_sub.expires_at.strftime("%d.%m.%Y") if eff_sub.expires_at else "—"
        sub_info = f"🟡 Пробный период (до {date_str}, {eff_sub.days_remaining} дн.)"
    elif eff_sub.status == EffectiveSubscriptionStatus.PAID_ACTIVE:
        date_str = eff_sub.expires_at.strftime("%d.%m.%Y") if eff_sub.expires_at else "—"
        sub_info = f"🟢 Активна (до {date_str}, {eff_sub.days_remaining} дн.)"
    elif eff_sub.status == EffectiveSubscriptionStatus.EXPIRED:
        sub_info = "🔴 Истекла (запись приостановлена)"
    else:
        sub_info = "🚫 Заблокирована"

    text = (
        f"⚙️ <b>Управление проектом: {master.display_name}</b>\n\n"
        f"🤖 <b>Telegram-бот:</b> {bot_info}\n"
        f"📊 <b>Статус бота:</b> {bot_status_str}\n"
        f"💳 <b>Подписка:</b> {sub_info}\n"
        f"📋 <b>Готовность к запуску:</b> {readiness_text}\n"
        f"🌍 <b>Часовой пояс:</b> {master.timezone}\n"
    )
    await callback.message.edit_text(
        text,
        reply_markup=project_card_keyboard(master, bot_instance, is_ready),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Bot Onboarding: Connect & Token Input
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:bot:connect:"))
async def cb_connect_bot(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Prompt user with BotFather steps to connect a bot."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    await state.set_state(ConnectBotStates.waiting_for_token)
    await state.update_data(master_id=master_id)

    text = (
        "🤖 <b>Инструкция по подключению вашего бота:</b>\n\n"
        "1. Откройте диалог с официальным ботом: @BotFather\n"
        "2. Отправьте команду <code>/newbot</code>\n"
        "3. Введите название для бота (например, <i>Мастер Анна</i>)\n"
        "4. Введите юзернейм (обязательно оканчивается на <code>bot</code>)\n"
        "5. Скопируйте полученный <b>HTTP API Token</b>\n"
        "6. <b>Отправьте скопированный токен ответным сообщением сюда.</b>\n\n"
        "<i>⚠️ Сообщение с токеном будет автоматически удалено сразу после получения.</i>"
    )
    await callback.message.edit_text(text, reply_markup=cancel_keyboard())
    await callback.answer()


@manager_router.message(ConnectBotStates.waiting_for_token)
async def msg_receive_bot_token(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Receive and validate bot token from master. Deletes token message immediately."""
    raw_text = message.text or ""

    # 1. Best-effort immediate message deletion (Section 10)
    try:
        await message.delete()
    except Exception as exc:
        logger.debug("Could not delete token message: %s", exc)

    token = raw_text.strip()
    if not token or ":" not in token:
        await message.answer(
            "Некорректный формат токена. Токен обычно выглядит как <code>123456789:ABCdefGHI...</code>\n"
            "Попробуйте отправить токен еще раз:",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    if not master_id:
        await message.answer("Сессия истекла. Начните сначала.", reply_markup=main_menu_keyboard())
        await state.clear()
        return

    user = await _get_or_create_user(session, message.from_user)
    gateway = TelegramProvisioningGateway()
    service = BotProvisioningService(session=session, gateway=gateway)

    try:
        # Validate candidate token via Telegram getMe
        identity = await service.validate_candidate_token(
            actor_user_id=user.id,
            token=token,
        )
    except ManagerTokenCollisionError:
        await message.answer(
            "❌ <b>Ошибка:</b> Запрещено подключать токен управляющего бота платформы.",
            reply_markup=cancel_keyboard(),
        )
        return
    except DuplicateBotError:
        await message.answer(
            "❌ <b>Этот Telegram-бот уже подключён к платформе.</b>\n"
            "Один бот не может обслуживать одновременно два проекта.",
            reply_markup=cancel_keyboard(),
        )
        return
    except InvalidBotTokenError:
        await message.answer(
            "❌ <b>Токен недействителен.</b> Проверьте токен в @BotFather и попробуйте ещё раз.",
            reply_markup=cancel_keyboard(),
        )
        return
    except TelegramGatewayError as exc:
        await message.answer(
            f"❌ <b>Ошибка Telegram:</b> {exc.message}\nПопробуйте повторить попытку позже.",
            reply_markup=cancel_keyboard(),
        )
        return
    except Exception as exc:
        logger.error("Unexpected error validating token %s: %s", mask_token(token), exc)
        await message.answer(
            "❌ Произошла ошибка при проверке токена. Попробуйте еще раз.",
            reply_markup=cancel_keyboard(),
        )
        return

    # Encrypt candidate token with TokenCrypto before storing in FSM (zero plaintext in Redis/Memory)
    crypto = TokenCrypto()
    encrypted_candidate_token = crypto.encrypt(token, associated_data=identity.id)
    del token

    # Store candidate data in FSM for confirmation
    await state.set_state(ConnectBotStates.confirm_connect)
    await state.update_data(
        master_id=master_id,
        encrypted_candidate_token=encrypted_candidate_token,
        candidate_bot_id=identity.id,
        candidate_username=identity.username,
        candidate_first_name=identity.first_name,
    )

    confirm_text = (
        "✅ <b>Токен успешно проверен!</b>\n\n"
        f"🤖 <b>Найден бот:</b> {identity.first_name}\n"
        f"🔗 <b>Юзернейм:</b> @{identity.username or 'без username'}\n"
        f"🆔 <b>Telegram ID:</b> <code>{identity.id}</code>\n\n"
        "Подключить этого бота к вашему проекту?"
    )
    await message.answer(confirm_text, reply_markup=confirm_connect_keyboard())


@manager_router.callback_query(ConnectBotStates.confirm_connect, F.data == "mgr:bot:confirm_connect")
async def cb_confirm_bot_connection(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registry: Optional[BotRegistry] = None,
) -> None:
    """Commit bot provisioning and set webhook."""
    data = await state.get_data()
    master_id = data.get("master_id")
    encrypted_token = data.get("encrypted_candidate_token")
    bot_id = data.get("candidate_bot_id")
    bot_username = data.get("candidate_username")
    bot_first_name = data.get("candidate_first_name")

    if not master_id or not encrypted_token or not bot_id:
        await state.clear()
        await callback.answer("Данные устарели. Начните сначала.", show_alert=True)
        return

    # Keep the confirmation context until the processed-update marker commits.
    # A crash after the provisioning saga commits can then replay safely.
    if session.info.get("webhook_update_scope") is not None:
        session.info.setdefault("post_commit", []).append(state.clear)
    else:
        await state.clear()

    user = await _get_or_create_user(session, callback.from_user)

    # Fail-closed ownership check
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    # Decrypt candidate token only for provisioning
    crypto = TokenCrypto()
    try:
        token = crypto.decrypt(encrypted_token, associated_data=bot_id)
    except Exception as exc:
        logger.error("Failed to decrypt candidate token for bot %s: %s", bot_id, exc)
        await callback.answer("Ошибка расшифровки токена. Повторите попытку.", show_alert=True)
        return

    gateway = TelegramProvisioningGateway()
    service = BotProvisioningService(session=session, gateway=gateway, crypto=crypto, registry=registry)
    identity = BotIdentity(id=bot_id, username=bot_username, first_name=bot_first_name)

    try:
        bot_instance = await service.provision_bot(
            master_id=master_id,
            actor_user_id=user.id,
            token=token,
            bot_identity=identity,
        )
    except ProvisioningWebhookError as exc:
        await callback.message.edit_text(
            f"⚠️ <b>Бот сохранён, но Telegram не удалось подключить вебхук:</b>\n{exc.message}\n\n"
            f"Вы можете повторить подключение из карточки проекта. При затруднениях напишите в поддержку: {settings.support_tag} ({settings.support_url})",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return
    except Exception as exc:
        logger.error("Provisioning failed: %s", exc)
        await callback.message.edit_text(
            f"❌ Не удалось подключить бота. Попробуйте еще раз или обратитесь в поддержку: {settings.support_tag} ({settings.support_url})",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return
    finally:
        del token

    text = (
        f"🎉 <b>Бот @{bot_instance.telegram_username} успешно подключён!</b>\n\n"
        "Статус: 🟡 <b>Настройка (SETUP_REQUIRED)</b>\n\n"
        "Теперь откройте своего бота для настройки услуг, расписания и реквизитов:\n"
        f"👉 <a href='https://t.me/{bot_instance.telegram_username}?start=admin'>Открыть панель управления мастера</a>\n\n"
        "Когда закончите настройку, вернитесь сюда и нажмите <b>«Запустить приём записей»</b>."
    )
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    await callback.message.edit_text(text, reply_markup=project_card_keyboard(master, bot_instance))
    await callback.answer()


# ---------------------------------------------------------------------------
# Checklist & Activation
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:bot:checklist:"))
async def cb_checklist(callback: CallbackQuery, session: AsyncSession) -> None:
    """Display onboarding readiness checklist for master."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    readiness = MasterReadinessService(session)
    is_ready, missing = await readiness.check(master_id)

    lines = ["📋 <b>Чек-лист готовности к запуску:</b>\n"]
    items = [
        ("Название проекта/салона", bool(master.display_name)),
        ("Корректный часовой пояс", bool(master.timezone)),
    ]

    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_current_for_master(master_id)
    items.append(("Telegram-бот подключён", bool(bot and bot.status != BotInstanceStatus.DISABLED)))

    # Missing items breakdown
    text_content = ""
    if is_ready:
        text_content = "✅ <b>Все обязательные настройки выполнены!</b>\nВы можете активировать бота прямо сейчас."
    else:
        text_content = "⚠️ <b>Необходимо настроить следующие пункты:</b>\n"
        for idx, m in enumerate(missing, 1):
            text_content += f"{idx}. ❌ {m}\n"

    msg_text = (
        f"{lines[0]}\n"
        f"Проект: <b>{master.display_name}</b>\n\n"
        f"{text_content}\n\n"
        "<i>Настройку услуг и расписания можно выполнить прямо в боте мастера через команду /admin.</i>"
    )
    await callback.message.edit_text(msg_text, reply_markup=project_card_keyboard(master, bot, is_ready))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:activate:"))
async def cb_activate_bot(
    callback: CallbackQuery, session: AsyncSession, registry: Optional[BotRegistry] = None
) -> None:
    """Activate Master and BotInstance to ACTIVE state."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    service = BotProvisioningService(session=session, registry=registry)
    try:
        is_ready, missing = await service.activate_master_and_bot(master_id, user.id)
    except AccessDeniedError:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_current_for_master(master_id)

    if not is_ready:
        missing_str = "\n".join(f"• {m}" for m in missing)
        await callback.message.edit_text(
            f"❌ <b>Нельзя запустить приём записей!</b>\n\n"
            f"Пожалуйста, сначала выполните настройку:\n{missing_str}",
            reply_markup=project_card_keyboard(master, bot, False),
        )
    else:
        await callback.message.edit_text(
            f"🚀 <b>Приём записей успешно активирован!</b>\n\n"
            f"Бот <b>@{bot.telegram_username}</b> переведён в статус 🟢 <b>АКТИВЕН</b>.\n"
            "Клиенты теперь могут полноценно записываться на услуги!",
            reply_markup=project_card_keyboard(master, bot, True),
        )
    await callback.answer()


# ---------------------------------------------------------------------------
# Retry Provisioning, Disable, Enable & Token Rotation
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:bot:retry:"))
async def cb_retry_provisioning(
    callback: CallbackQuery, session: AsyncSession, registry: Optional[BotRegistry] = None
) -> None:
    """Retry setWebhook for an ERROR bot instance."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_current_for_master(master_id)
    if not bot:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    service = BotProvisioningService(session=session, registry=registry)
    try:
        await service.retry_provisioning(bot.id, user.id)
        await callback.message.edit_text(
            "✅ <b>Вебхук успешно подключён!</b> Бот переведён в статус настройки.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        await callback.message.edit_text(
            "❌ <b>Повторное подключение не удалось.</b> Обратитесь в поддержку.\n\n"
            f"Служба поддержки: {settings.support_tag} ({settings.support_url})",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:disable:"))
async def cb_disable_bot(callback: CallbackQuery, session: AsyncSession) -> None:
    """Show confirmation dialog before disconnecting/disabling a bot."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_current_for_master(master_id)
    if not bot:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    bot_name = f"@{bot.telegram_username}" if bot.telegram_username else (bot.telegram_first_name or f"ID {bot.telegram_bot_id}")
    text = (
        f"⚠️ <b>Вы действительно хотите отключить бота {bot_name}?</b>\n\n"
        "• Вебхук в Telegram будет удалён.\n"
        "• Бот перестанет принимать сообщения и записи клиентов.\n"
        "• Все данные проекта (клиенты, записи, история, подписка) <b>сохраняются в полной безопасности</b>.\n"
        "• Вы сможете включить бота обратно в любое время."
    )
    await callback.message.edit_text(text, reply_markup=confirm_disable_bot_keyboard(master_id))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:confirm_disable:"))
async def cb_confirm_disable_bot(
    callback: CallbackQuery,
    session: AsyncSession,
    registry: Optional[BotRegistry] = None,
) -> None:
    """Commit bot disabling after explicit user confirmation."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_current_for_master(master_id)
    if not bot:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    service = BotProvisioningService(session=session, registry=registry)
    try:
        await service.disable_bot(bot.id, user.id)
        updated_master = await master_repo.get_by_id(master_id)
        updated_bot = await bot_repo.get_current_for_master(master_id)
        text = (
            "⏸ <b>Бот успешно отключён!</b>\n\n"
            "Вебхук Telegram отозван, кэш очищен. Все данные вашего проекта сохранены.\n"
            "Вы можете включить бота обратно в любое удобное время."
        )
        await callback.message.edit_text(
            text,
            reply_markup=project_card_keyboard(updated_master or master, updated_bot),
        )
    except Exception as exc:
        logger.error("Failed to disable bot %s for master %s: %s", bot.id, master_id, exc)
        await callback.answer("Ошибка при отключении. Обратитесь в поддержку.", show_alert=True)
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:enable:"))
async def cb_enable_bot(
    callback: CallbackQuery,
    session: AsyncSession,
    registry: Optional[BotRegistry] = None,
) -> None:
    """Re-enable a disabled bot."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_current_for_master(master_id)
    if not bot:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    service = BotProvisioningService(session=session, registry=registry)
    try:
        await service.enable_bot(bot.id, user.id)
        updated_master = await master_repo.get_by_id(master_id)
        updated_bot = await bot_repo.get_current_for_master(master_id)
        readiness_service = MasterReadinessService(session)
        is_ready, _ = await readiness_service.check(master_id)
        await callback.message.edit_text(
            "▶️ <b>Бот успешно включён!</b> Вебхук восстановлен.",
            reply_markup=project_card_keyboard(updated_master or master, updated_bot, is_ready),
        )
    except Exception as exc:
        await callback.message.edit_text(
            "❌ <b>Не удалось включить бота.</b> Обратитесь в поддержку.",
            reply_markup=project_card_keyboard(master, bot),
        )
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:rotate:"))
async def cb_rotate_token_prompt(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Prompt for new token to rotate."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_current_for_master(master_id)
    if not bot:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    await state.set_state(RotateTokenStates.waiting_for_token)
    await state.update_data(master_id=master_id, bot_instance_id=bot.id)

    text = (
        f"♻️ <b>Замена токена для бота @{bot.telegram_username}:</b>\n\n"
        "Отправьте новый токен, полученный в @BotFather для <b>этого же бота</b>.\n\n"
        "<i>⚠️ Сообщение с токеном будет немедленно удалено.</i>"
    )
    await callback.message.edit_text(text, reply_markup=cancel_keyboard())
    await callback.answer()


@manager_router.message(RotateTokenStates.waiting_for_token)
async def msg_receive_rotated_token(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    registry: Optional[BotRegistry] = None,
) -> None:
    """Process token rotation."""
    try:
        await message.delete()
    except Exception:
        pass

    new_token = (message.text or "").strip()
    data = await state.get_data()
    bot_instance_id = data.get("bot_instance_id")
    await state.clear()

    if not bot_instance_id or not new_token:
        await message.answer("Ошибка сессии ротации токена.", reply_markup=main_menu_keyboard())
        return

    user = await _get_or_create_user(session, message.from_user)
    service = BotProvisioningService(session=session, registry=registry)

    try:
        await service.rotate_token(bot_instance_id, user.id, new_token)
        await message.answer(
            "✅ <b>Токен успешно заменён!</b> Вебхук обновлён, версия токена увеличена.",
            reply_markup=main_menu_keyboard(),
        )
    except TokenRotationBotMismatchError:
        await message.answer(
            "❌ <b>Ошибка:</b> Это токен другого Telegram-бота! При ротации необходимо указывать токен того же бота.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        await message.answer(
            "❌ <b>Ошибка ротации токена.</b> Обратитесь в поддержку.",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        del new_token


@manager_router.callback_query(F.data == "mgr:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Generic cancel callback."""
    await state.clear()
    await callback.message.edit_text("Действие отменено.", reply_markup=main_menu_keyboard())
    await callback.answer()


# ---------------------------------------------------------------------------
# Subscription Management
# ---------------------------------------------------------------------------

async def _show_subscription_screen(callback: CallbackQuery, master: Master, session: AsyncSession) -> None:
    """Render subscription management screen with dynamically resolved plan prices and support links."""
    can_pay = settings.can_use_yookassa_test_checkout(callback.from_user.id)
    sub_service = SubscriptionService(session)
    eff_sub = await sub_service.get_effective_status(master.id)
    plans = await sub_service.list_active_plans()
    if not plans:
        primary_plan = await sub_service.get_active_plan()
        plans = [primary_plan]
    else:
        primary_plan = plans[0]

    plan_name = escape(primary_plan.name)
    price_fmt = f"{primary_plan.price:,.2f}".replace(",", " ").removesuffix(".00") + " ₽"

    if eff_sub.status == EffectiveSubscriptionStatus.TRIAL_ACTIVE:
        date_str = eff_sub.expires_at.strftime("%d.%m.%Y") if eff_sub.expires_at else "—"
        status_line = (
            f"🟡 <b>Пробный период</b>\n"
            f"Осталось: <b>{eff_sub.days_remaining} дн.</b> (действует до {date_str})\n\n"
            f"После окончания пробного периода действует основной тариф <b>{plan_name}</b> ({price_fmt}/мес).\n"
            f"При оплате заранее оставшиеся дни триала сохранятся."
        )
    elif eff_sub.status == EffectiveSubscriptionStatus.PAID_ACTIVE:
        date_str = eff_sub.expires_at.strftime("%d.%m.%Y") if eff_sub.expires_at else "—"
        status_line = (
            f"🟢 <b>Подписка активна</b>\n"
            f"Текущий тариф: <b>{plan_name}</b> ({price_fmt}/мес)\n"
            f"Оплачено до: <b>{date_str}</b> (осталось {eff_sub.days_remaining} дн.)\n\n"
            f"При продлении новый срок суммируется с текущей датой окончания."
        )
    elif eff_sub.status == EffectiveSubscriptionStatus.EXPIRED:
        status_line = (
            f"🔴 <b>Подписка истекла</b>\n\n"
            f"⚠️ <b>Внимание:</b> приём новых записей клиентами временно приостановлен.\n"
            f"Все ваши данные, клиенты, расписание и настройки <b>сохранены</b>.\n\n"
            f"Стоимость восстановления доступа: <b>{price_fmt}</b> ({plan_name})."
        )
    else:
        status_line = (
            f"🚫 <b>Подписка заблокирована</b> администратором платформы.\n\n"
            f"Для выяснения деталей и разблокировки напишите в поддержку: "
            f"{settings.support_tag} ({settings.support_url})."
        )

    footer = (
        "Здесь отображается статус подписки вашего проекта. "
        f"По вопросам обращайтесь в поддержку: {settings.support_tag}."
        if not can_pay or settings.payment_provider.lower() not in {"manual", "yookassa_web"} or (
            settings.payment_provider.lower() == "manual" and settings.is_production
        )
        else "Выберите тариф для оплаты:"
    )
    text = (
        f"💳 <b>Подписка ZapisFlow</b>\n"
        f"Проект: {escape(master.display_name)}\n"
        f"Тариф: <b>{plan_name}</b> — {price_fmt} / {primary_plan.period_days} дней\n\n"
        f"📊 <b>Текущий статус:</b>\n{status_line}\n\n"
        f"{footer}"
    )

    await callback.message.edit_text(
        text,
        reply_markup=subscription_card_keyboard(
            master.id,
            plans,
            eff_sub.status,
            can_pay=can_pay,
        ),
    )
    await callback.answer()


@manager_router.callback_query(F.data == "mgr:sub:menu")
async def cb_subscription_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Subscription section from main menu."""
    await state.clear()
    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    masters = await master_repo.list_by_owner_id(user.id)

    if not masters:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать проект", callback_data="mgr:master:new")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="mgr:menu")],
            ]
        )
        await callback.message.edit_text(
            "💳 <b>Раздел подписки ZapisFlow</b>\n\n"
            "У вас пока нет созданных проектов.\n"
            "Создайте проект прямо сейчас и получите <b>14 дней бесплатного пробного периода</b> со всеми функциями!",
            reply_markup=kb,
        )
        await callback.answer()
        return

    if len(masters) == 1:
        await _show_subscription_screen(callback, masters[0], session)
        return

    await callback.message.edit_text(
        "💳 <b>Управление подпиской</b>\n\n"
        "Выберите проект для настройки или продления тарифа:",
        reply_markup=subscription_projects_keyboard(masters),
    )
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:sub:(\d+)$"))
async def cb_subscription_screen(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Detailed subscription management screen with IDOR verification."""
    await state.clear()
    master_id = int(callback.data.split(":")[2])
    user = await _get_or_create_user(session, callback.from_user)

    master = await session.get(Master, master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    await _show_subscription_screen(callback, master, session)


@manager_router.callback_query(F.data.startswith("mgr:sub:pay:"))
async def cb_subscription_pay(callback: CallbackQuery, session: AsyncSession) -> None:
    """Initiate subscription payment for a chosen plan."""
    parts = callback.data.split(":")
    if len(parts) != 5 or not parts[3].isdigit() or not parts[4]:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    master_id = int(parts[3])
    plan_code = parts[4]
    user = await _get_or_create_user(session, callback.from_user)

    master = await session.get(Master, master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    if settings.payment_provider.lower() == "yookassa_web":
        try:
            shop_id, secret_key = settings.yookassa_credentials
            checkout_service = YooKassaCheckoutService(
                async_session_factory, YooKassaClient(shop_id, secret_key)
            )
            order, redirect = await checkout_service.open_direct_checkout(
                actor_user_id=user.id,
                master_id=master.id,
                plan_code=plan_code,
            )
        except (SubscriptionError, YooKassaGatewayError, ValueError):
            await callback.answer(
                f"Не удалось открыть оплату. Поддержка: {settings.support_tag}", show_alert=True
            )
            return

        if redirect.confirmation_url is None:
            await callback.answer(
                "Этот платёж уже завершён. Обновите статус подписки.", show_alert=True
            )
            return
        amount = f"{order.amount:,.2f}".replace(",", " ").removesuffix(".00")
        text = (
            "💳 <b>Оплата подписки</b>\n\n"
            f"Тариф:\n<b>{escape(order.plan_name)}</b>\n\n"
            f"Стоимость:\n<b>{amount} ₽</b>\n\n"
            f"Период:\n<b>{order.period_days} дней</b>"
        )
        await callback.message.edit_text(
            text,
            reply_markup=subscription_checkout_keyboard(
                master.id, redirect.confirmation_url, order.payment_id
            ),
        )
        await callback.answer()
        return

    if settings.is_production:
        notice = (
            "Оплата в этом боте недоступна. Здесь отображается статус подписки."
            if settings.payment_provider.lower() == "yookassa_web"
            else "Автоматическая оплата временно недоступна."
        )
        await callback.message.edit_text(
            "💳 <b>Оплата подписки</b>\n\n"
            f"{notice} Для вопросов обратитесь в поддержку: {settings.support_tag}.",
            reply_markup=subscription_payment_keyboard(master.id, 0),
        )
        await callback.answer()
        return

    sub_service = SubscriptionService(session)
    try:
        payment, intent = await sub_service.create_subscription_payment(
            master_id=master.id,
            actor_user_id=user.id,
            plan_code=plan_code,
        )
    except Exception as exc:
        logger.exception("Failed to create subscription payment: %s", exc)
        await callback.answer(
            f"Ошибка создания платежа.\nСлужба поддержки: {settings.support_tag}",
            show_alert=True,
        )
        return

    plan = await sub_service.get_active_plan(plan_code)
    amount_int = int(payment.amount)

    note = "<i>Для тестирования и ручной активации в текущей среде нажмите кнопку подтверждения:</i>"

    text = (
        f"💳 <b>Оплата подписки: {master.display_name}</b>\n\n"
        f"Тариф: <b>{plan.name}</b> ({plan.period_days} дн.)\n"
        f"Сумма к оплате: <b>{amount_int:,} ₽</b>\n\n"
        f"{note}"
    ).replace(",", " ")

    await callback.message.edit_text(
        text,
        reply_markup=subscription_payment_keyboard(master.id, payment.id, intent.payment_url),
    )
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:sub:confirm:"))
async def cb_subscription_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    """Process manual/test confirmation of subscription payment."""
    if settings.is_production:
        await callback.answer(
            "Ошибка: самостоятельное подтверждение платежей запрещено в production-среде.",
            show_alert=True,
        )
        return

    parts = callback.data.split(":")
    master_id = int(parts[3])
    payment_id = int(parts[4])
    user = await _get_or_create_user(session, callback.from_user)

    master = await session.get(Master, master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    payment = await session.get(SubscriptionPayment, payment_id)
    if not payment or payment.master_id != master.id:
        await callback.answer("Платеж не найден.", show_alert=True)
        return

    sub_service = SubscriptionService(session)
    try:
        await sub_service.process_successful_payment(
            provider=payment.provider,
            provider_payment_id=payment.provider_payment_id,
        )
    except Exception as exc:
        logger.exception("Failed to process payment #%s: %s", payment_id, exc)
        await callback.answer(
            f"Ошибка обработки.\nСлужба поддержки: {settings.support_tag}",
            show_alert=True,
        )
        return

    eff_sub = await sub_service.get_effective_status(master.id)
    date_str = eff_sub.expires_at.strftime("%d.%m.%Y %H:%M UTC") if eff_sub.expires_at else "—"

    text = (
        f"🎉 <b>Подписка успешно продлена!</b>\n\n"
        f"Проект: <b>{master.display_name}</b>\n"
        f"Новый срок действия: <b>до {date_str}</b> ({eff_sub.days_remaining} дн.)\n\n"
        "Онлайн-запись и все сервисы студии полностью активны."
    )

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К проекту", callback_data=f"mgr:master:{master.id}")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="mgr:menu")],
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("Подписка продлена!", show_alert=False)


@manager_router.callback_query(F.data.startswith("mgr:sub:check:"))
async def cb_subscription_check(callback: CallbackQuery, session: AsyncSession) -> None:
    """Manual payment check via YooKassa fallback button with rate limiting and strict verification."""
    parts = callback.data.split(":")
    if len(parts) != 5 or not parts[3].isdigit() or not parts[4].isdigit():
        await callback.answer("Некорректный запрос", show_alert=True)
        return

    master_id = int(parts[3])
    payment_id = int(parts[4])
    user = await _get_or_create_user(session, callback.from_user)

    # 1. Rate limiting check (prevent spamming YooKassa API)
    rate_key = f"rate_limit:check_sub_payment:{user.id}:{payment_id}"
    redis_client = getattr(callback.bot, "redis", None)
    if not await check_rate_limit(redis_client, rate_key, cooldown_seconds=5):
        await callback.answer(
            "⏳ Пожалуйста, подождите несколько секунд перед повторной проверкой.",
            show_alert=True,
        )
        return

    # 2. Check payment via YooKassaCheckoutService
    try:
        shop_id, secret_key = settings.yookassa_credentials
        checkout_service = YooKassaCheckoutService(
            async_session_factory, YooKassaClient(shop_id, secret_key)
        )
        result = await checkout_service.check_payment(
            payment_id=payment_id,
            actor_user_id=user.id,
        )
    except BillingIDORViolationError:
        await callback.answer("Ошибка: доступ к проекту запрещён.", show_alert=True)
        return
    except SubscriptionError as exc:
        await callback.answer(f"{str(exc)}. Поддержка: {settings.support_tag}", show_alert=True)
        return
    except Exception as exc:
        logger.exception("Unexpected error checking payment #%s: %s", payment_id, exc)
        await callback.answer(f"Ошибка проверки оплаты. Поддержка: {settings.support_tag}", show_alert=True)
        return

    # 3. Handle results
    amount_str = f"{result.amount:,.2f}".replace(",", " ").removesuffix(".00") + " ₽"

    if result.status == "SUCCEEDED":
        paid_str = result.paid_until.strftime("%d.%m.%Y") if result.paid_until else "—"
        text = (
            "<b>🎉 Оплата подтверждена!</b>\n\n"
            f"Вы приобрели подписку <b>{escape(result.plan_name)}</b> на <b>{result.period_days} дней</b>.\n\n"
            f"💳 Стоимость: <b>{amount_str}</b>\n\n"
            f"📅 Подписка активна до:\n<b>{paid_str}</b>\n\n"
            "Теперь вы можете пользоваться ZapisFlow."
        )
        await callback.message.edit_text(
            text,
            reply_markup=subscription_success_keyboard(master_id),
        )
        await callback.answer("🎉 Оплата подтверждена!")
        return

    if result.status == "ALREADY_CONFIRMED":
        paid_str = result.paid_until.strftime("%d.%m.%Y") if result.paid_until else "—"
        text = (
            "<b>✅ Оплата уже подтверждена</b>\n\n"
            f"Тариф:\n<b>{escape(result.plan_name)}</b>\n\n"
            f"Действует до:\n<b>{paid_str}</b>\n\n"
            "Вы уже можете пользоваться ZapisFlow."
        )
        await callback.message.edit_text(
            text,
            reply_markup=subscription_success_keyboard(master_id),
        )
        await callback.answer("Оплата уже подтверждена.")
        return

    if result.status == "PENDING":
        text = (
            "⏳ <b>Оплата ещё не подтверждена</b>\n\n"
            "ЮKassa пока не сообщила об успешной оплате.\n\n"
            "Попробуйте проверить ещё раз немного позже."
        )
        await callback.message.edit_text(
            text,
            reply_markup=subscription_pending_keyboard(master_id, payment_id, result.confirmation_url),
        )
        await callback.answer("⏳ Оплата ещё не поступила. Попробуйте чуть позже.", show_alert=True)
        return

    if result.status == "CANCELLED":
        text = (
            "❌ <b>Оплата не завершена</b>\n\n"
            "Платёж был отменён или не состоялся."
        )
        await callback.message.edit_text(
            text,
            reply_markup=subscription_canceled_keyboard(master_id, result.plan_code),
        )
        await callback.answer("Платёж был отменён.", show_alert=True)
        return

    # GATEWAY_ERROR or temporary provider failure
    text = (
        "⚠️ <b>Не удалось проверить оплату</b>\n\n"
        "Сервис оплаты временно недоступен.\n\n"
        "Попробуйте ещё раз позже."
    )
    await callback.message.edit_text(
        text,
        reply_markup=subscription_pending_keyboard(master_id, payment_id, result.confirmation_url),
    )
    await callback.answer("Сервис оплаты временно недоступен. Попробуйте позже.", show_alert=True)


# ---------------------------------------------------------------------------
# Platform Admin & Owner Bot Deletion Handlers (Phase 2)
# ---------------------------------------------------------------------------

async def _ensure_platform_admin(
    session: AsyncSession, callback: CallbackQuery
) -> tuple[bool, Optional[User]]:
    """Helper to guard platform admin callbacks and prevent unauthorized access."""
    user = await _get_or_create_user(session, callback.from_user)
    admin_svc = PlatformAdminService(session)
    is_admin = await admin_svc.is_platform_admin(telegram_id=callback.from_user.id, user_id=user.id)
    if not is_admin:
        await callback.answer("⛔ Доступ запрещён. Требуются права Platform Admin.", show_alert=True)
        return False, user
    return True, user


@manager_router.callback_query(F.data.regexp(r"^mgr:bot:delete:confirm:(\d+)$"))
async def cb_bot_delete_confirm(
    callback: CallbackQuery, session: AsyncSession, bot_registry: Optional[BotRegistry] = None
) -> None:
    """Execute logical unlinking and webhook revocation of customer bot."""
    master_id = int(callback.data.split(":")[-1])
    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("У вас нет прав на управление данным проектом.", show_alert=True)
        return

    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_current_for_master(master_id)
    if not bot:
        await callback.answer("У проекта нет подключённого бота.", show_alert=True)
        return

    bot_svc = BotProvisioningService(session, registry=bot_registry)
    try:
        await bot_svc.delete_bot(bot.id, actor_user_id=user.id)
    except Exception as exc:
        logger.exception("Error deleting bot: %s", exc)
        await callback.answer("Не удалось удалить бота. Попробуйте позже.", show_alert=True)
        return

    await callback.answer("✅ Бот успешно отключён и удалён из проекта.", show_alert=True)
    master = await master_repo.get_by_id(master_id)
    text = (
        f"🏢 Проект: <b>{escape(master.display_name)}</b>\n\n"
        f"Статус: 🟡 Требуется настройка\n\n"
        "Telegram-бот не подключён. Нажмите кнопку ниже для подключения."
    )
    await callback.message.edit_text(text, reply_markup=project_card_keyboard(master, None))


@manager_router.callback_query(F.data.regexp(r"^mgr:bot:delete:(\d+)$"))
async def cb_bot_delete_prompt(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Prompt owner before unlinking / deleting a bot from project."""
    master_id = int(callback.data.split(":")[-1])
    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("У вас нет прав на управление данным проектом.", show_alert=True)
        return

    text = (
        f"⚠️ <b>Удаление бота из проекта «{escape(master.display_name)}»</b>\n\n"
        "Вы действительно хотите удалить этого бота?\n\n"
        "• Это отключит Telegram-бота и отзовёт webhook.\n"
        "• Все данные проекта (клиенты, записи, история, финансы) <b>будут сохранены</b>.\n"
        "• Вы сможете подключить нового бота в любой момент."
    )
    await callback.message.edit_text(text, reply_markup=bot_delete_confirm_keyboard(master_id))
    await callback.answer()


@manager_router.callback_query(F.data == "mgr:admin:menu")
async def cb_admin_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin main navigation."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    text = (
        "👑 <b>ZapisFlow Platform Admin</b>\n\n"
        "Централизованная панель мониторинга и администрирования платформы ZapisFlow.\n\n"
        "Выберите раздел:"
    )
    await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
    await callback.answer()


@manager_router.callback_query(F.data == "mgr:admin:dashboard")
async def cb_admin_dashboard(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin dashboard metrics."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    admin_svc = PlatformAdminService(session)
    stats = await admin_svc.get_dashboard_stats()
    text = (
        "📊 <b>ZapisFlow Dashboard</b>\n\n"
        f"👥 <b>Пользователей:</b> {stats['total_users']}\n"
        f"🏢 <b>Проектов:</b> {stats['total_masters']}\n"
        f"🤖 <b>Активных ботов:</b> {stats['active_bots']}\n"
        f"🎁 <b>Trial:</b> {stats['trial_masters']}\n"
        f"💳 <b>Paid:</b> {stats['paid_masters']}\n"
        f"🔴 <b>Expired:</b> {stats['expired_masters']}\n"
        f"💰 <b>MRR:</b> {stats['mrr']:,.2f} ₽\n"
        f"📈 <b>Новых за 7 дней:</b> {stats['new_users_7d']}\n"
        f"📊 <b>Конверсия Trial → Paid:</b> {stats['conversion_rate']}%\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_dashboard_keyboard())
    await callback.answer()


@manager_router.callback_query(F.data == "mgr:admin:metrics")
async def cb_admin_metrics(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin SaaS metrics."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    admin_svc = PlatformAdminService(session)
    m = await admin_svc.get_saas_metrics()
    text = (
        "📈 <b>ZapisFlow SaaS Metrics</b>\n\n"
        f"💰 <b>MRR:</b> {m['mrr']:,.2f} ₽\n"
        f"📈 <b>ARR:</b> {m['arr']:,.2f} ₽\n"
        f"💳 <b>Активных платных:</b> {m['paid_masters']}\n"
        f"🎁 <b>В триале:</b> {m['trial_masters']}\n"
        f"🔴 <b>Истекших:</b> {m['expired_masters']}\n"
        f"📊 <b>ARPU:</b> {m['arpu']:,.2f} ₽\n"
        f"💵 <b>Общая выручка:</b> {m['total_revenue']:,.2f} ₽ ({m['total_successful_payments']} платежей)\n\n"
        f"👥 <b>Новые пользователи:</b> 7д: +{m['new_users_7d']} | 30д: +{m['new_users_30d']}\n"
        f"🏢 <b>Новые проекты:</b> 7д: +{m['new_projects_7d']} | 30д: +{m['new_projects_30d']}\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_metrics_keyboard())
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:users"))
async def cb_admin_users(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin users list."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    page = 1
    if callback.data.startswith("mgr:admin:users:p:"):
        try:
            page = max(1, int(callback.data.split(":")[-1]))
        except ValueError:
            page = 1

    admin_svc = PlatformAdminService(session)
    users, total, total_pages = await admin_svc.list_users(page=page)

    text = (
        f"👥 <b>Пользователи платформы</b> (Всего: {total})\n\n"
        f"Страница {page} из {total_pages}. Выберите пользователя для просмотра деталей:"
    )
    await callback.message.edit_text(text, reply_markup=admin_users_keyboard(users, page, total_pages))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:user:"))
async def cb_admin_user_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin user detail view."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    user_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    u = await admin_svc.get_user_details(user_id)
    if not u:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    projects_text = ""
    if u["projects"]:
        for p in u["projects"]:
            bot_info = f" (@{p['bot_username']})" if p['bot_username'] else " (без бота)"
            projects_text += f"\n  • <b>{escape(p['display_name'])}</b>: {p['status']}{bot_info}"
    else:
        projects_text = "\n  <i>Нет созданных проектов</i>"

    admin_badge = "👑 Platform Admin" if u["is_platform_admin"] else "Обычный пользователь"

    text = (
        f"👤 <b>Пользователь #{u['id']}</b>\n\n"
        f"Имя: <b>{escape(u['first_name'])} {escape(u['last_name'] or '')}</b>\n"
        f"Username: @{u['username'] if u['username'] else '-'}\n"
        f"Telegram ID: <code>{u['telegram_id']}</code>\n"
        f"Телефон: {u['phone'] or 'Не указан'}\n"
        f"Роль: <b>{admin_badge}</b>\n"
        f"Регистрация: {u['first_seen_at'].strftime('%d.%m.%Y %H:%M') if u['first_seen_at'] else '-'}\n"
        f"Активность: {u['last_activity_at'].strftime('%d.%m.%Y %H:%M') if u['last_activity_at'] else '-'}\n\n"
        f"🏢 <b>Проекты ({len(u['projects'])}):</b>{projects_text}"
    )
    await callback.message.edit_text(text, reply_markup=admin_user_detail_keyboard(u["id"]))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:projects"))
async def cb_admin_projects(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin projects list."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    page = 1
    if callback.data.startswith("mgr:admin:projects:p:"):
        try:
            page = max(1, int(callback.data.split(":")[-1]))
        except ValueError:
            page = 1

    admin_svc = PlatformAdminService(session)
    projects, total, total_pages = await admin_svc.list_projects(page=page)

    text = (
        f"🏢 <b>Проекты мастеров</b> (Всего: {total})\n\n"
        f"Страница {page} из {total_pages}. Выберите проект для просмотра:"
    )
    await callback.message.edit_text(text, reply_markup=admin_projects_keyboard(projects, page, total_pages))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:project:suspend:"))
async def cb_admin_project_suspend(
    callback: CallbackQuery, session: AsyncSession, bot_registry: Optional[BotRegistry] = None
) -> None:
    """Toggle master project suspension."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    master_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session, registry=bot_registry)
    ok, msg = await admin_svc.toggle_project_suspension(master_id, actor_user_id=user.id)
    await callback.answer(msg, show_alert=True)

    # Refresh details
    p = await admin_svc.get_project_details(master_id)
    if not p:
        return
    is_suspended = p["status"] == "SUSPENDED"
    bot_info = f"@{p['bot']['username']} ({p['bot']['status']})" if p['bot']['username'] else "Не подключён"

    text = (
        f"🏢 <b>Проект #{p['id']}: {escape(p['display_name'])}</b>\n\n"
        f"Владелец: <b>{escape(p['owner']['first_name'] or '')}</b> (@{p['owner']['username'] or '-'})\n"
        f"Статус проекта: <b>{p['status']}</b>\n"
        f"Подписка: <b>{p['subscription_status']}</b>\n"
        f"Бот: <b>{bot_info}</b>\n"
        f"Клиентов: <b>{p['clients_count']}</b> | Записей: <b>{p['appointments_count']}</b>\n"
        f"Создан: {p['created_at'].strftime('%d.%m.%Y %H:%M') if p['created_at'] else '-'}\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_project_detail_keyboard(master_id, is_suspended))


@manager_router.callback_query(F.data.startswith("mgr:admin:project:"))
async def cb_admin_project_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin project detail view."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    master_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    p = await admin_svc.get_project_details(master_id)
    if not p:
        await callback.answer("Проект не найден.", show_alert=True)
        return

    is_suspended = p["status"] == "SUSPENDED"
    bot_info = f"@{p['bot']['username']} ({p['bot']['status']})" if p['bot']['username'] else "Не подключён"

    sub_end_text = "-"
    if p["paid_until"]:
        sub_end_text = f"Оплачен до {p['paid_until'].strftime('%d.%m.%Y')}"
    elif p["trial_ends_at"]:
        sub_end_text = f"Триал до {p['trial_ends_at'].strftime('%d.%m.%Y')}"

    text = (
        f"🏢 <b>Проект #{p['id']}: {escape(p['display_name'])}</b>\n\n"
        f"Владелец: <b>{escape(p['owner']['first_name'] or '')}</b> (@{p['owner']['username'] or '-'})\n"
        f"Telegram ID владельца: <code>{p['owner']['telegram_id']}</code>\n"
        f"Статус проекта: <b>{p['status']}</b>\n"
        f"Подписка: <b>{p['subscription_status']}</b> ({sub_end_text})\n"
        f"Бот: <b>{bot_info}</b>\n"
        f"Часовой пояс: {p['timezone']}\n"
        f"Клиентов: <b>{p['clients_count']}</b> | Записей: <b>{p['appointments_count']}</b>\n"
        f"Создан: {p['created_at'].strftime('%d.%m.%Y %H:%M') if p['created_at'] else '-'}\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_project_detail_keyboard(master_id, is_suspended))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:bots"))
async def cb_admin_bots(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin bots list."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    page = 1
    if callback.data.startswith("mgr:admin:bots:p:"):
        try:
            page = max(1, int(callback.data.split(":")[-1]))
        except ValueError:
            page = 1

    admin_svc = PlatformAdminService(session)
    bots, total, total_pages = await admin_svc.list_bots(page=page)

    text = (
        f"🤖 <b>Боты платформы</b> (Всего: {total})\n\n"
        f"Страница {page} из {total_pages}. Выберите бота для просмотра:"
    )
    await callback.message.edit_text(text, reply_markup=admin_bots_keyboard(bots, page, total_pages))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:bot:webhook:"))
async def cb_admin_bot_webhook(callback: CallbackQuery, session: AsyncSession) -> None:
    """Inspect live webhook info in Telegram."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    bot_id = int(callback.data.split(":")[-1])
    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_by_id(bot_id)
    if not bot or not bot.encrypted_token or not bot.telegram_bot_id:
        await callback.answer("Токен бота не настроен.", show_alert=True)
        return

    crypto = TokenCrypto()
    gateway = TelegramProvisioningGateway()
    try:
        raw_token = crypto.decrypt(bot.encrypted_token, associated_data=bot.telegram_bot_id)
        info = await gateway.get_webhook_info(raw_token)
        pending = getattr(info, "pending_update_count", 0)
        last_error = getattr(info, "last_error_message", None)
        msg = f"Вебхук активен.\nОчередь: {pending} апдейтов.\nОшибка TG: {last_error or 'Нет'}"
    except Exception as exc:
        msg = f"Ошибка получения статуса: {str(exc)[:150]}"

    await callback.answer(msg, show_alert=True)


@manager_router.callback_query(F.data.startswith("mgr:admin:bot:toggle:"))
async def cb_admin_bot_toggle(
    callback: CallbackQuery, session: AsyncSession, bot_registry: Optional[BotRegistry] = None
) -> None:
    """Toggle bot disabled/enabled status."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    bot_id = int(callback.data.split(":")[-1])
    bot_svc = BotProvisioningService(session, registry=bot_registry)
    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_by_id(bot_id)
    if not bot:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(bot.master_id)
    actor_id = master.owner_user_id if master else user.id

    try:
        if bot.status == BotInstanceStatus.DISABLED:
            await bot_svc.enable_bot(bot.id, actor_user_id=actor_id)
            await callback.answer("Бот успешно включён.", show_alert=True)
        else:
            await bot_svc.disable_bot(bot.id, actor_user_id=actor_id)
            await callback.answer("Бот успешно отключён.", show_alert=True)
    except Exception as exc:
        logger.exception("Error toggling bot status: %s", exc)
        await callback.answer(f"Ошибка: {str(exc)[:150]}", show_alert=True)

    # Refresh
    admin_svc = PlatformAdminService(session)
    b = await admin_svc.get_bot_details(bot_id)
    if b:
        is_active = b["status"] == "ACTIVE"
        text = (
            f"🤖 <b>Бот #{b['id']}</b>\n\n"
            f"Username: @{b['telegram_username'] or '-'}\n"
            f"Имя: {escape(b['telegram_first_name'] or '-')}\n"
            f"Статус: <b>{b['status']}</b>\n"
            f"Проект: <b>{escape(b['master_name'])}</b>\n"
            f"Версия токена: v{b['token_version']}\n"
            f"Ошибка: {b['last_error'] or 'Нет'}\n"
        )
        await callback.message.edit_text(text, reply_markup=admin_bot_detail_keyboard(bot_id, is_active))


@manager_router.callback_query(F.data.startswith("mgr:admin:bot:delete:"))
async def cb_admin_bot_delete(
    callback: CallbackQuery, session: AsyncSession, bot_registry: Optional[BotRegistry] = None
) -> None:
    """Platform Admin logical bot deletion."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    bot_id = int(callback.data.split(":")[-1])
    bot_svc = BotProvisioningService(session, registry=bot_registry)
    try:
        await bot_svc.delete_bot(bot_id, actor_user_id=user.id, is_platform_admin=True)
        await callback.answer("Бот успешно удалён из платформы.", show_alert=True)
    except Exception as exc:
        logger.exception("Error deleting bot: %s", exc)
        await callback.answer("Не удалось удалить бота.", show_alert=True)
        return

    # Return to bots list
    admin_svc = PlatformAdminService(session)
    bots, total, total_pages = await admin_svc.list_bots(page=1)
    text = (
        f"🤖 <b>Боты платформы</b> (Всего: {total})\n\n"
        f"Страница 1 из {total_pages}. Выберите бота для просмотра:"
    )
    await callback.message.edit_text(text, reply_markup=admin_bots_keyboard(bots, 1, total_pages))


@manager_router.callback_query(F.data.startswith("mgr:admin:bot:"))
async def cb_admin_bot_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin bot detail view."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    bot_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    b = await admin_svc.get_bot_details(bot_id)
    if not b:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    is_active = b["status"] == "ACTIVE"
    text = (
        f"🤖 <b>Бот #{b['id']}</b>\n\n"
        f"Username: @{b['telegram_username'] or '-'}\n"
        f"Имя: {escape(b['telegram_first_name'] or '-')}\n"
        f"Telegram Bot ID: <code>{b['telegram_bot_id'] or '-'}</code>\n"
        f"Статус: <b>{b['status']}</b>\n"
        f"Проект: <b>{escape(b['master_name'])}</b>\n"
        f"Владелец: @{b['owner_username'] or '-'}\n"
        f"Версия токена: v{b['token_version']}\n"
        f"Активен: {'Да' if b['is_current'] else 'Нет'}\n"
        f"Ошибка: {b['last_error'] or 'Нет'}\n"
        f"Подключён: {b['created_at'].strftime('%d.%m.%Y %H:%M') if b['created_at'] else '-'}\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_bot_detail_keyboard(bot_id, is_active))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:subscriptions"))
async def cb_admin_subscriptions(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin subscriptions list."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    page = 1
    if callback.data.startswith("mgr:admin:sub:p:"):
        try:
            page = max(1, int(callback.data.split(":")[-1]))
        except ValueError:
            page = 1

    admin_svc = PlatformAdminService(session)
    subs, total, total_pages = await admin_svc.list_subscriptions(page=page)

    text = (
        f"💳 <b>Подписки проектов</b> (Всего: {total})\n\n"
        f"Страница {page} из {total_pages}. Выберите проект для деталей:"
    )
    await callback.message.edit_text(text, reply_markup=admin_subscriptions_keyboard(subs, page, total_pages))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:payments"))
async def cb_admin_payments(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin payments list."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    page = 1
    if callback.data.startswith("mgr:admin:payments:p:"):
        try:
            page = max(1, int(callback.data.split(":")[-1]))
        except ValueError:
            page = 1

    admin_svc = PlatformAdminService(session)
    payments, total, total_pages = await admin_svc.list_payments(page=page)

    text = (
        f"💰 <b>Платежи платформы</b> (Всего: {total})\n\n"
        f"Страница {page} из {total_pages}. Выберите платёж для просмотра:"
    )
    await callback.message.edit_text(text, reply_markup=admin_payments_keyboard(payments, page, total_pages))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:payment:check:"))
async def cb_admin_payment_check(callback: CallbackQuery, session: AsyncSession) -> None:
    """Reconcile pending payment status from YooKassa."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    payment_id = int(callback.data.split(":")[-1])
    checkout_service = YooKassaCheckoutService(session=session)
    payment = await session.get(SubscriptionPayment, payment_id)
    if not payment:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    master = await session.get(Master, payment.master_id)
    actor_id = master.owner_user_id if master else 0

    result = await checkout_service.check_payment(payment_id=payment_id, actor_user_id=actor_id)
    await callback.answer(f"Статус платежа: {result.status}", show_alert=True)

    # Refresh details
    admin_svc = PlatformAdminService(session)
    p = await admin_svc.get_payment_details(payment_id)
    if p:
        text = (
            f"💰 <b>Платёж #{p['id']}</b>\n\n"
            f"Проект: <b>{escape(p['project_name'])}</b>\n"
            f"Владелец: <b>{escape(p['owner_name'])}</b>\n"
            f"Тариф: <b>{escape(p['plan_name'])}</b>\n"
            f"Сумма: <b>{p['amount']} {p['currency']}</b>\n"
            f"Провайдер: <b>{p['provider']}</b>\n"
            f"Статус: <b>{p['status']}</b>\n"
            f"ID в YooKassa: <code>{p['provider_payment_id']}</code>\n"
            f"Создан: {p['created_at'].strftime('%d.%m.%Y %H:%M') if p['created_at'] else '-'}\n"
            f"Оплачен: {p['paid_at'].strftime('%d.%m.%Y %H:%M') if p['paid_at'] else '-'}\n"
        )
        await callback.message.edit_text(text, reply_markup=admin_payment_detail_keyboard(p["id"], p["provider"], p["status"]))


@manager_router.callback_query(F.data.startswith("mgr:admin:payment:"))
async def cb_admin_payment_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin payment detail view."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    payment_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    p = await admin_svc.get_payment_details(payment_id)
    if not p:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    text = (
        f"💰 <b>Платёж #{p['id']}</b>\n\n"
        f"Проект: <b>{escape(p['project_name'])}</b>\n"
        f"Владелец: <b>{escape(p['owner_name'])}</b>\n"
        f"Тариф: <b>{escape(p['plan_name'])}</b>\n"
        f"Сумма: <b>{p['amount']} {p['currency']}</b>\n"
        f"Провайдер: <b>{p['provider']}</b>\n"
        f"Статус: <b>{p['status']}</b>\n"
        f"ID в YooKassa: <code>{p['provider_payment_id']}</code>\n"
        f"Период: {p['period_days']} дней\n"
        f"Создан: {p['created_at'].strftime('%d.%m.%Y %H:%M') if p['created_at'] else '-'}\n"
        f"Оплачен: {p['paid_at'].strftime('%d.%m.%Y %H:%M') if p['paid_at'] else '-'}\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_payment_detail_keyboard(p["id"], p["provider"], p["status"]))
    await callback.answer()


@manager_router.callback_query(F.data == "mgr:admin:plans")
async def cb_admin_plans(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin subscription plans catalog."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    admin_svc = PlatformAdminService(session)
    plans = await admin_svc.list_plans()

    text = (
        "📦 <b>Каталог тарифов платформы</b>\n\n"
        "Выберите тариф для просмотра или переключения активности:"
    )
    await callback.message.edit_text(text, reply_markup=admin_plans_keyboard(plans))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:admin:plan:toggle:"))
async def cb_admin_plan_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    """Toggle active status for a plan."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    plan_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    ok, msg = await admin_svc.toggle_plan_active(plan_id, actor_user_id=user.id)
    await callback.answer(msg, show_alert=True)

    # Refresh
    p = await admin_svc.get_plan_details(plan_id)
    if p:
        text = (
            f"📦 <b>Тариф «{escape(p.name)}»</b>\n\n"
            f"Код: <code>{p.code}</code>\n"
            f"Цена: <b>{p.price_rub} {p.currency}</b>\n"
            f"Период: <b>{p.duration_days} дней</b>\n"
            f"Статус: <b>{'🟢 Активен' if p.is_active else '🔴 Неактивен'}</b>\n"
            f"Порядок: {p.sort_order}\n"
            f"Описание: {escape(p.description or 'Нет описания')}\n"
        )
        await callback.message.edit_text(text, reply_markup=admin_plan_detail_keyboard(p.id, p.is_active))


@manager_router.callback_query(F.data.startswith("mgr:admin:plan:"))
async def cb_admin_plan_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin plan detail view."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    plan_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    p = await admin_svc.get_plan_details(plan_id)
    if not p:
        await callback.answer("Тариф не найден.", show_alert=True)
        return

    text = (
        f"📦 <b>Тариф «{escape(p.name)}»</b>\n\n"
        f"Код: <code>{p.code}</code>\n"
        f"Цена: <b>{p.price_rub} {p.currency}</b>\n"
        f"Период: <b>{p.duration_days} дней</b>\n"
        f"Статус: <b>{'🟢 Активен' if p.is_active else '🔴 Неактивен'}</b>\n"
        f"Порядок: {p.sort_order}\n"
        f"Описание: {escape(p.description or 'Нет описания')}\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_plan_detail_keyboard(p.id, p.is_active))
    await callback.answer()
