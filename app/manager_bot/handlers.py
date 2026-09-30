"""Command and callback handlers for platform Manager Bot."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, Optional

from aiogram import F, Router
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
    cancel_keyboard,
    confirm_connect_keyboard,
    main_menu_keyboard,
    project_card_keyboard,
    project_list_keyboard,
    subscription_card_keyboard,
    subscription_payment_keyboard,
)
from app.services.subscription_service import SubscriptionService
from app.manager_bot.states import ConnectBotStates, CreateMasterStates, RotateTokenStates
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditEvent, AuditService
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.exceptions import (
    AccessDeniedError,
    DuplicateBotError,
    InvalidBotTokenError,
    ManagerTokenCollisionError,
    ProvisioningWebhookError,
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
    await session.commit()
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

    if not masters:
        text = (
            "👋 <b>Добро пожаловать в Beauty Bot Manager!</b>\n\n"
            "Здесь вы можете создать собственный проект и подключить Telegram-бота "
            "для автоматической записи клиентов в вашу студию красоты.\n\n"
            "Нажмите кнопку ниже, чтобы создать свой первый проект:"
        )
        await message.answer(text, reply_markup=main_menu_keyboard())
    else:
        text = (
            f"👋 Здравствуйте, <b>{user.first_name}</b>!\n\n"
            "Выберите проект для управления или создайте новый:"
        )
        await message.answer(text, reply_markup=project_list_keyboard(masters))


@manager_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Manager Bot /cancel command to clear active FSM state."""
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu_keyboard())


@manager_router.callback_query(F.data == "mgr:menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Return to main menu."""
    await state.clear()
    user = await _get_or_create_user(session, callback.from_user)
    text = f"<b>Beauty Bot Manager</b> — панель управления проектами ({user.first_name}):"
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    await callback.answer()


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
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    await callback.answer()


@manager_router.callback_query(F.data == "mgr:projects")
async def cb_projects(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """List of all owned master projects."""
    await state.clear()
    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    masters = await master_repo.list_by_owner_id(user.id)

    if not masters:
        text = "У вас пока нет созданных проектов. Создайте свой первый проект:"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    else:
        text = "📁 <b>Ваши проекты:</b>\nВыберите проект для управления или настройки бота:"
        await callback.message.edit_text(text, reply_markup=project_list_keyboard(masters))
    await callback.answer()


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
    trial_ends = datetime.now(timezone.utc) + timedelta(days=settings.trial_duration_days)

    master = Master(
        owner_user_id=user.id,
        display_name=name,
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.TRIAL,
        trial_ends_at=trial_ends,
        timezone="Europe/Moscow",
    )
    session.add(master)
    await session.flush()

    # Create associated default MasterSettings
    master_settings = MasterSettings(master_id=master.id)
    session.add(master_settings)
    await session.flush()

    audit = AuditService(session)
    await audit.log_event(
        action=AuditEvent.MASTER_CREATED,
        actor_user_id=user.id,
        master_id=master.id,
        entity_type="Master",
        entity_id=master.id,
        payload_after={"display_name": name, "trial_ends_at": trial_ends.isoformat()},
    )
    await session.commit()
    await state.clear()

    text = (
        f"✅ <b>Проект «{name}» успешно создан!</b>\n\n"
        f"Пробный период действует до: <b>{trial_ends.strftime('%d.%m.%Y')}</b>.\n\n"
        "Следующий шаг — подключите Telegram-бота для приёма записей клиентов."
    )
    bot_repo = BotInstanceRepository(session)
    bot_instance = await bot_repo.get_current_for_master(master.id)
    await message.answer(text, reply_markup=project_card_keyboard(master, bot_instance))


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
) -> None:
    """Commit bot provisioning and set webhook."""
    data = await state.get_data()
    master_id = data.get("master_id")
    encrypted_token = data.get("encrypted_candidate_token")
    bot_id = data.get("candidate_bot_id")
    bot_username = data.get("candidate_username")
    bot_first_name = data.get("candidate_first_name")

    # Immediately clear FSM before provisioning (0 residual data in Redis/Memory)
    await state.clear()

    if not master_id or not encrypted_token or not bot_id:
        await callback.answer("Данные устарели. Начните сначала.", show_alert=True)
        return

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
    service = BotProvisioningService(session=session, gateway=gateway, crypto=crypto)
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
            "Вы можете повторить подключение из карточки проекта.",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return
    except Exception as exc:
        logger.error("Provisioning failed: %s", exc)
        await callback.message.edit_text(
            "❌ Не удалось подключить бота. Попробуйте еще раз.",
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
async def cb_activate_bot(callback: CallbackQuery, session: AsyncSession) -> None:
    """Activate Master and BotInstance to ACTIVE state."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    service = BotProvisioningService(session=session)
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
async def cb_retry_provisioning(callback: CallbackQuery, session: AsyncSession) -> None:
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

    service = BotProvisioningService(session=session)
    try:
        await service.retry_provisioning(bot.id, user.id)
        await callback.message.edit_text(
            "✅ <b>Вебхук успешно подключён!</b> Бот переведён в статус настройки.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"❌ <b>Повторное подключение не удалось:</b>\n{str(exc)[:200]}",
            reply_markup=main_menu_keyboard(),
        )
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:disable:"))
async def cb_disable_bot(callback: CallbackQuery, session: AsyncSession) -> None:
    """Disable customer bot."""
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

    service = BotProvisioningService(session=session)
    try:
        await service.disable_bot(bot.id, user.id)
        await callback.message.edit_text(
            "⏸ <b>Бот успешно отключён.</b> Приём новых записей и вебхуки приостановлены.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        await callback.answer(f"Ошибка: {exc}", show_alert=True)
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:enable:"))
async def cb_enable_bot(callback: CallbackQuery, session: AsyncSession) -> None:
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

    service = BotProvisioningService(session=session)
    try:
        await service.enable_bot(bot.id, user.id)
        await callback.message.edit_text(
            "▶️ <b>Бот успешно включён!</b> Вебхук восстановлен.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"❌ <b>Не удалось включить бота:</b> {str(exc)[:200]}",
            reply_markup=main_menu_keyboard(),
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
async def msg_receive_rotated_token(message: Message, state: FSMContext, session: AsyncSession) -> None:
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
    service = BotProvisioningService(session=session)

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
            f"❌ <b>Ошибка ротации токена:</b> {str(exc)[:200]}",
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

    sub_service = SubscriptionService(session)
    eff_sub = await sub_service.get_effective_status(master.id)
    plans = await sub_service.list_active_plans()

    if eff_sub.status == EffectiveSubscriptionStatus.TRIAL_ACTIVE:
        date_str = eff_sub.expires_at.strftime("%d.%m.%Y %H:%M UTC") if eff_sub.expires_at else "—"
        status_line = f"🟡 <b>Пробный период</b>\n<i>(осталось {eff_sub.days_remaining} дн., действует до {date_str})</i>"
    elif eff_sub.status == EffectiveSubscriptionStatus.PAID_ACTIVE:
        date_str = eff_sub.expires_at.strftime("%d.%m.%Y %H:%M UTC") if eff_sub.expires_at else "—"
        status_line = f"🟢 <b>Активна</b>\n<i>(оплачено до {date_str}, осталось {eff_sub.days_remaining} дн.)</i>"
    elif eff_sub.status == EffectiveSubscriptionStatus.EXPIRED:
        status_line = "🔴 <b>Истекла</b>\n<i>(доступ клиентов к записи ограничен, все ваши данные сохранены)</i>"
    else:
        status_line = "🚫 <b>Заблокирована</b> администратором платформы"

    text = (
        f"💳 <b>Управление подпиской: {master.display_name}</b>\n\n"
        f"📊 <b>Текущий статус:</b>\n{status_line}\n\n"
        "Продлите подписку для бесперебойного приёма онлайн-записей ваших клиентов.\n"
        "Выберите желаемый период продления:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=subscription_card_keyboard(master.id, plans),
    )
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:sub:pay:"))
async def cb_subscription_pay(callback: CallbackQuery, session: AsyncSession) -> None:
    """Initiate subscription payment for a chosen plan."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    plan_code = parts[4]
    user = await _get_or_create_user(session, callback.from_user)

    master = await session.get(Master, master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    sub_service = SubscriptionService(session)
    try:
        payment, intent = await sub_service.create_subscription_payment(
            master_id=master.id,
            actor_user_id=user.id,
            plan_code=plan_code,
        )
        await session.commit()
    except Exception as exc:
        logger.exception("Failed to create subscription payment: %s", exc)
        await callback.answer(f"Ошибка создания платежа: {str(exc)[:100]}", show_alert=True)
        return

    plan = await sub_service.get_active_plan(plan_code)
    amount_int = int(payment.amount)

    if settings.is_production:
        note = (
            "<i>Автоматический платёжный шлюз находится на этапе подключения. "
            "Для активации коммерческого тарифа в пилотном режиме обратитесь к платформенному администратору.</i>"
        )
    else:
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
        await session.commit()
    except Exception as exc:
        logger.exception("Failed to process payment #%s: %s", payment_id, exc)
        await callback.answer(f"Ошибка обработки: {str(exc)[:100]}", show_alert=True)
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

