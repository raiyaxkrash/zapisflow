"""Command and callback handlers for platform Manager Bot."""

from datetime import datetime, timedelta, timezone
from html import escape
import logging
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.security import mask_token
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstanceStatus, Master, MasterSettings, MasterStatus, SubscriptionStatus
from app.database.models.portfolio import PortfolioCategory, PortfolioItem
from app.database.models.schedule import ScheduleTemplate
from app.database.models.service import DepositType, Service
from app.database.models.subscription import EffectiveSubscriptionStatus, SubscriptionPayment, SubscriptionPlan
from app.database.models.user import User
from app.manager_bot.keyboards import (
    ACTIVITY_TYPE_NAMES,
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
    crm_client_card_keyboard,
    crm_client_history_keyboard,
    crm_client_list_keyboard,
    crm_menu_keyboard,
    finances_period_keyboard,
    main_menu_keyboard,
    manager_contact_cancel_keyboard,
    manager_contacts_keyboard,
    manager_notification_settings_keyboard,
    manager_portfolio_categories_keyboard,
    manager_portfolio_item_detail_keyboard,
    manager_portfolio_items_keyboard,
    manager_prepayment_settings_keyboard,
    manager_schedule_menu_keyboard,
    manager_service_detail_keyboard,
    manager_services_list_keyboard,
    manager_settings_menu_keyboard,
    onboarding_activity_type_keyboard,
    onboarding_completion_keyboard,
    onboarding_schedule_keyboard,
    onboarding_skip_keyboard,
    onboarding_suggested_service_keyboard,
    project_card_keyboard,
    project_list_keyboard,
    reviews_keyboard,
    stats_period_keyboard,
    subscription_card_keyboard,
    subscription_canceled_keyboard,
    subscription_checkout_keyboard,
    subscription_payment_keyboard,
    subscription_pending_keyboard,
    subscription_projects_keyboard,
    subscription_success_keyboard,
)
from app.services.bot_registry import BotRegistry
from app.services.crm_service import MasterCrmService
from app.services.master_contacts import CONTACT_FIELD_LABELS, MasterContactsService
from app.services.platform_admin_service import PlatformAdminService
from app.services.rate_limiter import check_rate_limit
from app.services.subscription_service import SubscriptionService
from app.database.session import async_session_factory
from app.services.billing.yookassa_checkout import YooKassaCheckoutService
from app.services.billing.yookassa_client import YooKassaClient, YooKassaGatewayError
from app.manager_bot.states import (
    ConnectBotStates,
    CreateMasterStates,
    CrmNoteStates,
    CrmSearchStates,
    ManagerPortfolioStates,
    ManagerScheduleStates,
    ManagerServiceStates,
    ManagerSettingsStates,
    MasterContactStates,
    MasterOnboardingStates,
    RotateTokenStates,
)
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.portfolio_repository import PortfolioRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_repository import ServiceRepository
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

DEFAULT_PORTFOLIO_CATEGORIES: Dict[str, list[str]] = {
    "nails": ["Маникюр", "Педикюр", "Дизайн ногтей"],
    "lashes": ["Классика", "Объёмное наращивание (2D/3D)", "Ламинирование"],
    "brows": ["Коррекция и окрашивание", "Долговременная укладка"],
    "barber": ["Мужская стрижка", "Оформление бороды", "Комплекс"],
    "hair": ["Женская стрижка", "Окрашивание", "Укладка"],
    "makeup": ["Дневной макияж", "Вечерний макияж", "Свадебный образ"],
    "cosmetology": ["Чистка лица", "Пилинги", "Уходовые процедуры"],
    "other": ["Примеры работ", "До и После"],
}

DEFAULT_SERVICE_SUGGESTIONS: Dict[str, Dict[str, Any]] = {
    "nails": {"title": "Маникюр с покрытием гель-лак", "price": 2000, "duration_min": 90, "buffer_min": 15},
    "lashes": {"title": "Классическое наращивание ресниц", "price": 2500, "duration_min": 120, "buffer_min": 15},
    "brows": {"title": "Комплекс: коррекция + окрашивание бровей", "price": 1500, "duration_min": 45, "buffer_min": 15},
    "barber": {"title": "Мужская стрижка", "price": 1800, "duration_min": 45, "buffer_min": 15},
    "hair": {"title": "Стрижка и укладка", "price": 2500, "duration_min": 60, "buffer_min": 15},
    "makeup": {"title": "Вечерний макияж", "price": 3000, "duration_min": 60, "buffer_min": 15},
    "cosmetology": {"title": "Комбинированная чистка лица", "price": 3500, "duration_min": 90, "buffer_min": 15},
    "other": {"title": "Основная услуга", "price": 2000, "duration_min": 60, "buffer_min": 15},
}


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
        f"✅ <b>Проект «{escape(name)}» успешно создан!</b>\n\n"
        f"{trial_text}"
        "🎯 <b>Шаг 1 из 4: Выберите сферу деятельности:</b>\n"
        "Это поможет настроить категории портфолио и примеры услуг:"
    )
    keyboard = onboarding_activity_type_keyboard(master.id)
    if session.info.get("webhook_update_scope") is not None:
        session.info.setdefault("post_commit", []).append(
            lambda: message.answer(text, reply_markup=keyboard)
        )
    else:
        await message.answer(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Guided Master Onboarding Flow
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:ob:act:"))
async def cb_onboarding_activity_type(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Step 1: Activity type selected. Populate default portfolio categories and ask for address."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    act_type = parts[4]

    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    master.activity_type = act_type
    await session.flush()

    # Seed default portfolio categories for this activity type
    port_repo = PortfolioRepository(session)
    existing_cats = await port_repo.list_categories(master_id, active_only=False)
    if not existing_cats:
        cats = DEFAULT_PORTFOLIO_CATEGORIES.get(act_type, DEFAULT_PORTFOLIO_CATEGORIES["other"])
        for idx, cat_title in enumerate(cats, start=1):
            await port_repo.create_category(master_id, cat_title, display_order=idx)

    await state.update_data(onboarding_master_id=master_id, activity_type=act_type)
    await state.set_state(MasterOnboardingStates.waiting_for_address)

    act_label = ACTIVITY_TYPE_NAMES.get(act_type, act_type)
    text = (
        f"🎯 Сфера: <b>{act_label}</b>\n\n"
        "📍 <b>Шаг 2 из 4: Адрес студии / кабинета</b>\n\n"
        "Напишите адрес или район, где вы принимаете клиентов\n"
        "(например: <i>г. Москва, ул. Тверская 12, оф. 305</i>):\n\n"
        "<i>Или нажмите «Пропустить», если принимаете на дому или адрес пока не готов:</i>"
    )
    await callback.message.edit_text(text, reply_markup=onboarding_skip_keyboard(master_id, "address"))
    await callback.answer()


@manager_router.message(MasterOnboardingStates.waiting_for_address)
async def msg_onboarding_address(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Step 2 (address input): Save address and ask for contact phone."""
    data = await state.get_data()
    master_id = data.get("onboarding_master_id")
    if not master_id:
        await state.clear()
        await message.answer("Сессия устарела. Откройте проект заново.", reply_markup=main_menu_keyboard())
        return

    user = await _get_or_create_user(session, message.from_user)
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await message.answer("Ошибка доступа.")
        return

    addr = (message.text or "").strip()
    if addr and len(addr) <= 500:
        settings_repo = MasterSettingsRepository(session)
        await settings_repo.update(master_id, studio_address=addr)

    await state.set_state(MasterOnboardingStates.waiting_for_phone)
    text = (
        "📞 <b>Шаг 2.1: Рабочий телефон / WhatsApp</b>\n\n"
        "Укажите номер телефона для связи с клиентами\n"
        "(например: <i>+79991234567</i>):"
    )
    await message.answer(text, reply_markup=onboarding_skip_keyboard(master_id, "phone"))


@manager_router.callback_query(F.data.startswith("mgr:ob:skip:"))
async def cb_onboarding_skip(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Handle skipping an optional onboarding step."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    step = parts[4]

    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    await callback.answer()

    if step == "address":
        await state.set_state(MasterOnboardingStates.waiting_for_phone)
        text = (
            "📞 <b>Шаг 2.1: Рабочий телефон / WhatsApp</b>\n\n"
            "Укажите номер телефона для связи с клиентами\n"
            "(например: <i>+79991234567</i>):"
        )
        await callback.message.edit_text(text, reply_markup=onboarding_skip_keyboard(master_id, "phone"))

    elif step == "phone":
        data = await state.get_data()
        act_type = data.get("activity_type") or master.activity_type or "other"
        sugg = DEFAULT_SERVICE_SUGGESTIONS.get(act_type, DEFAULT_SERVICE_SUGGESTIONS["other"])
        await state.update_data(
            suggested_title=sugg["title"],
            suggested_price=sugg["price"],
            suggested_duration=sugg["duration_min"],
        )
        await state.set_state(MasterOnboardingStates.waiting_for_service_title)

        text = (
            "💅 <b>Шаг 3 из 4: Первая услуга в прайс-листе</b>\n\n"
            f"Мы подготовили базовый вариант для вашей сферы:\n"
            f"• <b>Название:</b> {escape(sugg['title'])}\n"
            f"• <b>Стоимость:</b> {sugg['price']} ₽\n"
            f"• <b>Длительность:</b> {sugg['duration_min']} мин.\n\n"
            "Вы можете добавить её в 1 клик или ввести своё название:"
        )
        await callback.message.edit_text(text, reply_markup=onboarding_suggested_service_keyboard(master_id))

    elif step == "service":
        await state.set_state(MasterOnboardingStates.waiting_for_schedule)
        text = (
            "📅 <b>Шаг 4 из 4: График приёма клиентов</b>\n\n"
            "Базовый график:\n"
            "• <b>Понедельник — Пятница:</b> 10:00 – 19:00\n"
            "• <b>Перерыв:</b> 14:00 – 15:00\n"
            "• <b>Суббота и Воскресенье:</b> Выходной\n\n"
            "Принять базовый график?"
        )
        await callback.message.edit_text(text, reply_markup=onboarding_schedule_keyboard(master_id))

    elif step == "schedule":
        await state.clear()
        bot_repo = BotInstanceRepository(session)
        bot_instance = await bot_repo.get_current_for_master(master_id)
        text = (
            f"🎉 <b>Ваш проект «{escape(master.display_name)}» готов к работе!</b>\n\n"
            "Теперь ваши клиенты смогут:\n"
            "📅 Записываться на услуги онлайн\n"
            "💳 Вносить предоплату\n"
            "🔔 Получать автоматические напоминания\n"
            "⭐ Оставлять отзывы о визитах\n\n"
            "Подключите вашего бота, чтобы запустить онлайн-запись:"
        )
        await callback.message.edit_text(text, reply_markup=onboarding_completion_keyboard(master_id, bot_instance))


@manager_router.message(MasterOnboardingStates.waiting_for_phone)
async def msg_onboarding_phone(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Step 2.1 (phone input): Save phone and prompt for first service."""
    data = await state.get_data()
    master_id = data.get("onboarding_master_id")
    if not master_id:
        await state.clear()
        await message.answer("Сессия устарела. Откройте проект заново.", reply_markup=main_menu_keyboard())
        return

    user = await _get_or_create_user(session, message.from_user)
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await message.answer("Ошибка доступа.")
        return

    phone = (message.text or "").strip()
    if phone and len(phone) <= 64:
        settings_repo = MasterSettingsRepository(session)
        await settings_repo.update(master_id, studio_phone=phone, whatsapp_phone=phone)

    act_type = data.get("activity_type") or master.activity_type or "other"
    sugg = DEFAULT_SERVICE_SUGGESTIONS.get(act_type, DEFAULT_SERVICE_SUGGESTIONS["other"])
    await state.update_data(
        suggested_title=sugg["title"],
        suggested_price=sugg["price"],
        suggested_duration=sugg["duration_min"],
    )
    await state.set_state(MasterOnboardingStates.waiting_for_service_title)

    text = (
        "💅 <b>Шаг 3 из 4: Первая услуга в прайс-листе</b>\n\n"
        f"Мы подготовили базовый вариант для вашей сферы:\n"
        f"• <b>Название:</b> {escape(sugg['title'])}\n"
        f"• <b>Стоимость:</b> {sugg['price']} ₽\n"
        f"• <b>Длительность:</b> {sugg['duration_min']} мин.\n\n"
        "Вы можете добавить её в 1 клик или ввести своё название:"
    )
    await message.answer(text, reply_markup=onboarding_suggested_service_keyboard(master_id))


@manager_router.callback_query(F.data.startswith("mgr:ob:srv:accept:"))
async def cb_onboarding_accept_service(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Step 3: Accept suggested service."""
    master_id = int(callback.data.split(":")[4])
    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    data = await state.get_data()
    title = data.get("suggested_title") or "Основная услуга"
    price = data.get("suggested_price") or 2000
    dur = data.get("suggested_duration") or 60

    srv_repo = ServiceRepository(session)
    await srv_repo.create_service(
        master_id=master_id,
        title=title,
        price=price,
        duration_min=dur,
        buffer_min=15,
        deposit_type=DepositType.PERCENT,
        deposit_value=0,
        is_active=True,
    )

    await state.set_state(MasterOnboardingStates.waiting_for_schedule)
    text = (
        f"✅ Услуга <b>«{escape(title)}»</b> ({price} ₽) добавлена!\n\n"
        "📅 <b>Шаг 4 из 4: График приёма клиентов</b>\n\n"
        "Базовый график:\n"
        "• <b>Понедельник — Пятница:</b> 10:00 – 19:00\n"
        "• <b>Перерыв:</b> 14:00 – 15:00\n"
        "• <b>Суббота и Воскресенье:</b> Выходной\n\n"
        "Принять базовый график?"
    )
    await callback.message.edit_text(text, reply_markup=onboarding_schedule_keyboard(master_id))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:ob:srv:custom:"))
async def cb_onboarding_custom_service(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt for custom service title."""
    master_id = int(callback.data.split(":")[4])
    await state.set_state(MasterOnboardingStates.waiting_for_service_title)
    text = (
        "✏️ <b>Введите название вашей услуги</b>\n"
        "(например: <i>Снятие + Маникюр + Покрытие</i> или <i>Мужская стрижка Fade</i>):"
    )
    await callback.message.edit_text(text, reply_markup=onboarding_skip_keyboard(master_id, "service"))
    await callback.answer()


@manager_router.message(MasterOnboardingStates.waiting_for_service_title)
async def msg_onboarding_custom_service_title(message: Message, state: FSMContext) -> None:
    """Save custom service title and ask for price."""
    title = (message.text or "").strip()
    if not title or len(title) > 255:
        await message.answer("Название должно содержать от 2 до 255 символов. Попробуйте ещё раз:")
        return

    await state.update_data(custom_title=title)
    await state.set_state(MasterOnboardingStates.waiting_for_service_price)
    data = await state.get_data()
    master_id = data.get("onboarding_master_id", 0)
    text = f"Услуга: <b>{escape(title)}</b>\n\n💰 <b>Укажите стоимость услуги в рублях (число):</b>"
    await message.answer(text, reply_markup=onboarding_skip_keyboard(master_id, "service"))


@manager_router.message(MasterOnboardingStates.waiting_for_service_price)
async def msg_onboarding_custom_service_price(message: Message, state: FSMContext) -> None:
    """Save custom price and ask for duration."""
    raw = (message.text or "").strip().replace(" ", "").replace("₽", "").replace("руб", "")
    try:
        price = int(raw)
        if price < 0 or price > 1_000_000:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число (например, 2500):")
        return

    await state.update_data(custom_price=price)
    await state.set_state(MasterOnboardingStates.waiting_for_service_duration)
    data = await state.get_data()
    master_id = data.get("onboarding_master_id", 0)
    text = f"Стоимость: <b>{price} ₽</b>\n\n⏱ <b>Укажите длительность процедуры в минутах (например: 60, 90, 120):</b>"
    await message.answer(text, reply_markup=onboarding_skip_keyboard(master_id, "service"))


@manager_router.message(MasterOnboardingStates.waiting_for_service_duration)
async def msg_onboarding_custom_service_duration(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save duration, create service and prompt for schedule."""
    raw = (message.text or "").strip().replace("мин", "")
    try:
        dur = int(raw)
        if dur <= 0 or dur > 1440:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число минут (от 10 до 720):")
        return

    data = await state.get_data()
    master_id = data.get("onboarding_master_id")
    if not master_id:
        await state.clear()
        await message.answer("Ошибка сессии.", reply_markup=main_menu_keyboard())
        return

    title = data.get("custom_title", "Услуга")
    price = data.get("custom_price", 2000)

    srv_repo = ServiceRepository(session)
    await srv_repo.create_service(
        master_id=master_id,
        title=title,
        price=price,
        duration_min=dur,
        buffer_min=15,
        deposit_type=DepositType.PERCENT,
        deposit_value=0,
        is_active=True,
    )

    await state.set_state(MasterOnboardingStates.waiting_for_schedule)
    text = (
        f"✅ Услуга <b>«{escape(title)}»</b> ({price} ₽, {dur} мин) сохранена!\n\n"
        "📅 <b>Шаг 4 из 4: График приёма клиентов</b>\n\n"
        "Базовый график:\n"
        "• <b>Понедельник — Пятница:</b> 10:00 – 19:00\n"
        "• <b>Перерыв:</b> 14:00 – 15:00\n"
        "• <b>Суббота и Воскресенье:</b> Выходной\n\n"
        "Принять базовый график?"
    )
    await message.answer(text, reply_markup=onboarding_schedule_keyboard(master_id))


@manager_router.callback_query(F.data.startswith("mgr:ob:sch:accept:"))
async def cb_onboarding_accept_schedule(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Step 4: Accept standard schedule."""
    master_id = int(callback.data.split(":")[4])
    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    from datetime import time as dt_time
    sch_repo = ScheduleRepository(session)
    # Mon-Fri: 10:00-19:00 with break 14:00-15:00
    for day in range(5):
        await sch_repo.set_template(
            weekday=day,
            is_day_off=False,
            work_start=dt_time(10, 0),
            work_end=dt_time(19, 0),
            breaks=[(dt_time(14, 0), dt_time(15, 0))],
            master_id=master_id,
        )
    # Sat-Sun: Day off
    for day in range(5, 7):
        await sch_repo.set_template(
            weekday=day,
            is_day_off=True,
            master_id=master_id,
        )

    await state.clear()
    bot_repo = BotInstanceRepository(session)
    bot_instance = await bot_repo.get_current_for_master(master_id)

    text = (
        f"🎉 <b>Ваш проект «{escape(master.display_name)}» готов к работе!</b>\n\n"
        "Теперь ваши клиенты могут:\n"
        "📅 Записываться на услуги онлайн\n"
        "💳 Вносить предоплату\n"
        "🔔 Получать автоматические напоминания\n"
        "⭐ Оставлять отзывы о визитах\n\n"
        "Подключите вашего бота, чтобы запустить онлайн-запись:"
    )
    await callback.message.edit_text(text, reply_markup=onboarding_completion_keyboard(master_id, bot_instance))
    await callback.answer()


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

    crm_svc = MasterCrmService(session)
    dashboard = await crm_svc.get_today_dashboard(master.id)
    nxt_text = ""
    if dashboard.get("next_appointment"):
        nxt = dashboard["next_appointment"]
        nxt_text = (
            f"⏰ <b>Ближайшая запись:</b>\n"
            f"• Время: <b>{nxt['time_str']}</b>\n"
            f"• Клиент: <b>{escape(nxt['client_name'])}</b>\n"
            f"• Услуга: <b>{escape(nxt['service_title'])}</b>\n\n"
        )
    else:
        nxt_text = "⏰ <i>На сегодня больше нет предстоящих записей</i>\n\n"

    act_label = ACTIVITY_TYPE_NAMES.get(master.activity_type or "", "")
    act_suffix = f" • {act_label}" if act_label else ""

    text = (
        f"⚙️ <b>Проект: {escape(master.display_name)}</b>{act_suffix}\n\n"
        f"📊 <b>Сегодня:</b>\n"
        f"📅 Записей: <b>{dashboard['total_today']}</b>\n"
        f"💰 Выручка: <b>{int(dashboard['revenue_today'])} ₽</b>\n"
        f"👥 Клиентов: <b>{dashboard['unique_clients_today']}</b>\n"
        f"🆕 Новых: <b>{dashboard['new_clients_today']}</b>\n\n"
        f"{nxt_text}"
        f"🤖 <b>Telegram-бот:</b> {bot_info}\n"
        f"📊 <b>Статус бота:</b> {bot_status_str}\n"
        f"💳 <b>Подписка:</b> {sub_info}\n"
        f"📋 <b>Готовность к запуску:</b> {readiness_text}\n"
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


# ===========================================================================
# PHASE 3: CRM & MASTER BUSINESS FEATURE HANDLERS
# ===========================================================================


@manager_router.callback_query(F.data.startswith("mgr:crm:search:"))
async def cb_crm_search_prompt(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt user for client search text."""
    user = await _get_or_create_user(session, callback.from_user)
    master_id = int(callback.data.split(":")[-1])
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    await state.set_state(CrmSearchStates.waiting_for_query)
    await state.update_data(master_id=master_id)

    text = (
        "🔍 <b>Поиск клиента в CRM</b>\n\n"
        "Отправьте в чат имя, номер телефона или Telegram @username клиента для поиска:"
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:crm:{master_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(CrmSearchStates.waiting_for_query)
async def msg_crm_search_query(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Execute search query and return matching clients."""
    user = await _get_or_create_user(session, message.from_user)
    data = await state.get_data()
    master_id = data.get("master_id")
    if not master_id:
        await state.clear()
        return

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        await message.answer("Проект не найден или нет доступа.")
        return

    query_text = (message.text or "").strip()
    if not query_text:
        await message.answer("Введите текст для поиска:")
        return

    await state.clear()
    crm_svc = MasterCrmService(session)
    clients, total = await crm_svc.search_clients(master_id, query_text, page=1, page_size=5)

    if not clients:
        text = f"🔍 По запросу «<b>{escape(query_text)}</b>» клиентов не найдено."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Искать снова", callback_data=f"mgr:crm:search:{master_id}")],
                [InlineKeyboardButton(text="⬅️ Меню CRM", callback_data=f"mgr:crm:{master_id}")],
            ]
        )
        await message.answer(text, reply_markup=kb)
        return

    text = (
        f"🔍 Результаты поиска «<b>{escape(query_text)}</b>»:\n"
        f"Найдено клиентов: <b>{total}</b>\n\n"
        "Выберите клиента для просмотра профиля:"
    )
    kb = crm_client_list_keyboard(master_id, segment="all", page=1, total_count=total, clients=clients)
    await message.answer(text, reply_markup=kb)


@manager_router.callback_query(F.data.startswith("mgr:crm:seg:"))
async def cb_crm_segment(callback: CallbackQuery, session: AsyncSession) -> None:
    """View paginated clients for a segment."""
    user = await _get_or_create_user(session, callback.from_user)
    parts = callback.data.split(":")
    master_id = int(parts[3])
    segment = parts[4]
    page = int(parts[5]) if len(parts) > 5 else 1

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    crm_svc = MasterCrmService(session)
    clients, total = await crm_svc.list_clients_by_segment(master_id, segment, page=page, page_size=5)

    seg_names = {
        "all": "Все клиенты",
        "regular": "Постоянные клиенты (3+ визита)",
        "new": "Новые клиенты (до 30 дн.)",
        "inactive30": "Давно не были (30+ дн.)",
        "inactive60": "Давно не были (60+ дн.)",
    }
    seg_title = seg_names.get(segment, "Клиенты")

    if not clients:
        text = (
            f"👥 <b>{escape(seg_title)}</b>\n\n"
            "В этом сегменте пока нет клиентов."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в CRM", callback_data=f"mgr:crm:{master_id}")]]
        )
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer()
        return

    text = (
        f"👥 <b>{escape(seg_title)}</b>\n"
        f"Всего в сегменте: <b>{total}</b>\n\n"
        "Выберите клиента для просмотра карточки и истории:"
    )
    kb = crm_client_list_keyboard(master_id, segment, page, total, clients, page_size=5)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:crm:"))
async def cb_crm_main_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    """CRM main menu for master."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    master_id = int(parts[2])
    user = await _get_or_create_user(session, callback.from_user)

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    crm_svc = MasterCrmService(session)
    _, total_clients = await crm_svc.list_clients_by_segment(master_id, "all", page=1, page_size=1)
    _, regular_cnt = await crm_svc.list_clients_by_segment(master_id, "regular", page=1, page_size=1)
    _, new_cnt = await crm_svc.list_clients_by_segment(master_id, "new", page=1, page_size=1)
    _, inactive_cnt = await crm_svc.list_clients_by_segment(master_id, "inactive30", page=1, page_size=1)

    text = (
        f"👥 <b>CRM база клиентов: {escape(master.display_name)}</b>\n\n"
        f"Всего клиентов в базе: <b>{total_clients}</b>\n"
        f"⭐ Постоянных (3+ визита): <b>{regular_cnt}</b>\n"
        f"🆕 Новых за последний месяц: <b>{new_cnt}</b>\n"
        f"⏰ Не приходили более 30 дней: <b>{inactive_cnt}</b>\n\n"
        "Выберите сегмент или воспользуйтесь поиском:"
    )
    await callback.message.edit_text(text, reply_markup=crm_menu_keyboard(master_id))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:client:hist:"))
async def cb_client_history(callback: CallbackQuery, session: AsyncSession) -> None:
    """Paginated client appointments history."""
    user = await _get_or_create_user(session, callback.from_user)
    parts = callback.data.split(":")
    master_id = int(parts[3])
    client_user_id = int(parts[4])
    page = int(parts[5]) if len(parts) > 5 else 1
    segment = parts[6] if len(parts) > 6 else "all"
    client_page = int(parts[7]) if len(parts) > 7 else 1

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    crm_svc = MasterCrmService(session)
    appointments, total = await crm_svc.get_client_history(master_id, client_user_id, page=page, page_size=5)

    if not appointments:
        text = "📋 <b>История записей</b>\n\nУ клиента пока нет записей в этом проекте."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="⬅️ Назад к карточке",
                    callback_data=f"mgr:client:{master_id}:{client_user_id}:{segment}:{client_page}",
                )
            ]]
        )
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer()
        return

    tz = await crm_svc._get_timezone(master_id)
    lines = [f"📋 <b>История записей клиента (всего: {total}):</b>\n"]
    for a in appointments:
        local_time = a.start_time.astimezone(tz)
        dt_str = local_time.strftime("%d.%m.%Y в %H:%M")
        price_str = f"{a.snapshot_service_price:.0f} ₽"
        lines.append(
            f"• <b>{dt_str}</b>\n"
            f"  Услуга: {escape(a.snapshot_service_title)} ({price_str})\n"
            f"  Статус: {a.status.display_name}\n"
        )

    text = "\n".join(lines)
    kb = crm_client_history_keyboard(
        master_id, client_user_id, page, total, segment=segment, client_page=client_page, page_size=5
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:client:note:"))
async def cb_client_edit_note(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt master to edit client notes."""
    user = await _get_or_create_user(session, callback.from_user)
    parts = callback.data.split(":")
    master_id = int(parts[3])
    client_user_id = int(parts[4])
    segment = parts[5] if len(parts) > 5 else "all"
    page = int(parts[6]) if len(parts) > 6 else 1

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    await state.set_state(CrmNoteStates.waiting_for_note)
    await state.update_data(
        master_id=master_id,
        client_user_id=client_user_id,
        segment=segment,
        page=page,
    )

    text = (
        "✏️ <b>Редактирование заметки о клиенте</b>\n\n"
        "Отправьте текст заметки (особенности клиента, предпочтения по дизайну, аллергии и т.д.).\n"
        "Чтобы удалить заметку, отправьте <code>/clear</code>."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"mgr:client:{master_id}:{client_user_id}:{segment}:{page}",
            )
        ]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(CrmNoteStates.waiting_for_note)
async def msg_client_save_note(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save client note and display updated client card."""
    user = await _get_or_create_user(session, message.from_user)
    data = await state.get_data()
    master_id = data.get("master_id")
    client_user_id = data.get("client_user_id")
    segment = data.get("segment", "all")
    page = data.get("page", 1)

    if not master_id or not client_user_id:
        await state.clear()
        return

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        await message.answer("Проект не найден или нет доступа.")
        return

    raw_note = (message.text or "").strip()
    note_val = None if raw_note == "/clear" else raw_note

    crm_svc = MasterCrmService(session)
    try:
        await crm_svc.update_client_notes(
            master_id=master_id,
            user_id=client_user_id,
            actor_user_id=user.id,
            notes=note_val,
        )
    except Exception as exc:
        await message.answer(f"Ошибка сохранения: {exc}")
        return

    await state.clear()
    card = await crm_svc.get_client_card(master_id, client_user_id)
    text = _render_client_card_text(card)
    kb = crm_client_card_keyboard(master_id, client_user_id, segment, page)
    await message.answer("✅ Заметка обновлена!\n\n" + text, reply_markup=kb)


@manager_router.callback_query(F.data.startswith("mgr:client:"))
async def cb_client_card(callback: CallbackQuery, session: AsyncSession) -> None:
    """View client profile in CRM."""
    user = await _get_or_create_user(session, callback.from_user)
    parts = callback.data.split(":")
    master_id = int(parts[2])
    client_user_id = int(parts[3])
    segment = parts[4] if len(parts) > 4 else "all"
    page = int(parts[5]) if len(parts) > 5 else 1

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    crm_svc = MasterCrmService(session)
    try:
        card = await crm_svc.get_client_card(master_id, client_user_id)
    except LookupError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    text = _render_client_card_text(card)
    kb = crm_client_card_keyboard(master_id, client_user_id, segment, page)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


def _render_client_card_text(card: dict) -> str:
    """Format structured client profile card."""
    first_v = card["first_booking_at"].strftime("%d.%m.%Y") if card.get("first_booking_at") else "Нет данных"
    last_v = card["last_booking_at"].strftime("%d.%m.%Y") if card.get("last_booking_at") else "Нет данных"
    uname = f"@{escape(card['username'])}" if card.get("username") else "не указан"
    phone = escape(card["phone"]) if card.get("phone") else "не указан"
    marketing_icon = "🟢 Согласен" if card.get("is_marketing_allowed") else "🔴 Не согласен"
    blocked_icon = " (🚫 Заблокировал бота)" if card.get("is_bot_blocked") else ""

    return (
        f"👤 <b>Карточка клиента: {escape(card['full_name'])}</b>\n\n"
        f"📱 Телефон: <code>{phone}</code>\n"
        f"✈️ Telegram: {uname}\n"
        f"📅 Первый визит: <b>{first_v}</b>\n"
        f"🕒 Последний визит: <b>{last_v}</b>\n\n"
        f"📊 <b>Статистика визитов:</b>\n"
        f"• Всего записей: <b>{card['total_bookings']}</b>\n"
        f"• Выполнено: <b>{card['completed']}</b>\n"
        f"• Отменено: <b>{card['cancelled']}</b>\n"
        f"• Не пришел (No-Show): <b>{card['no_show']}</b>\n"
        f"💰 <b>LTV (общая сумма оплат): {card['total_spent']:.0f} ₽</b>\n\n"
        f"⭐️ <b>Любимая услуга:</b> {escape(card['favorite_service'])}\n"
        f"📢 Рассылки: {marketing_icon}{blocked_icon}\n\n"
        f"📝 <b>Заметка мастера:</b>\n"
        f"<i>{escape(card['notes']) if card.get('notes') else 'Нет заметок'}</i>"
    )


# ---------------------------------------------------------------------------
# STATISTICS & FINANCES
# ---------------------------------------------------------------------------


@manager_router.callback_query(F.data.startswith("mgr:stats:"))
async def cb_master_statistics(callback: CallbackQuery, session: AsyncSession) -> None:
    """Operational statistics dashboard for master."""
    user = await _get_or_create_user(session, callback.from_user)
    parts = callback.data.split(":")
    master_id = int(parts[2])
    period = parts[3] if len(parts) > 3 else "month"

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    crm_svc = MasterCrmService(session)
    stats = await crm_svc.get_master_statistics(master_id, period=period)

    text = (
        f"📊 <b>Статистика проекта: {escape(master.display_name)}</b>\n"
        f"Период: <b>{escape(stats['period_title'])}</b>\n\n"
        f"📅 Всего записей: <b>{stats['total_bookings']}</b>\n"
        f"✅ Выполнено визитов: <b>{stats['completed']}</b> ({stats['completion_rate']}%)\n"
        f"⏳ Подтверждено предстоящих: <b>{stats['confirmed']}</b>\n"
        f"❌ Отменено: <b>{stats['cancelled']}</b>\n"
        f"🚫 Не явились (No-Show): <b>{stats['no_show']}</b> ({stats['no_show_rate']}%)\n\n"
        f"👥 Уникальных клиентов: <b>{stats['unique_clients']}</b>\n"
        f"🆕 Новых клиентов: <b>{stats['new_clients']}</b>\n"
        f"🔄 Повторных клиентов: <b>{stats['returning_clients']}</b>\n\n"
        f"📈 Загрузка расписания: <b>{stats['occupancy_rate']}%</b>"
    )
    await callback.message.edit_text(text, reply_markup=stats_period_keyboard(master_id, current_period=period))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:finances:"))
async def cb_master_finances(callback: CallbackQuery, session: AsyncSession) -> None:
    """Master financial dashboard from bookings (strictly isolated from platform subscription billing)."""
    user = await _get_or_create_user(session, callback.from_user)
    parts = callback.data.split(":")
    master_id = int(parts[2])
    period = parts[3] if len(parts) > 3 else "month"

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    crm_svc = MasterCrmService(session)
    fin = await crm_svc.get_master_finances(master_id, period=period)

    lines = [
        f"💰 <b>Финансы мастера: {escape(master.display_name)}</b>",
        f"Период: <b>{escape(fin['period_title'])}</b>\n",
        f"💵 Выручка от записей: <b>{fin['revenue']:.0f} ₽</b>",
        f"💳 Удержанные предоплаты: <b>{fin['retained_deposits']:.0f} ₽</b>",
        f"📈 <b>Итого доход: {fin['total_income']:.0f} ₽</b>\n",
        f"🏷 Выполнено записей: <b>{fin['completed_count']}</b>",
        f"🧾 Средний чек: <b>{fin['avg_ticket']:.0f} ₽</b>\n",
    ]

    if fin["top_services"]:
        lines.append("🏆 <b>Топ услуг по выручке:</b>")
        for idx, s in enumerate(fin["top_services"], start=1):
            lines.append(f"{idx}. {escape(s['title'])} — {s['count']} виз. ({s['revenue']:.0f} ₽)")
    else:
        lines.append("<i>За выбранный период выполненных услуг нет.</i>")

    lines.append(
        "\nℹ️ <i>Примечание: здесь отображается ваша реальная выручка от клиентов за услуги. "
        "Она не связана со стоимостью подписки на платформу ZapisFlow.</i>"
    )

    text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=finances_period_keyboard(master_id, current_period=period))
    await callback.answer()


# ---------------------------------------------------------------------------
# REVIEWS & RATINGS
# ---------------------------------------------------------------------------


@manager_router.callback_query(F.data.startswith("mgr:reviews:"))
async def cb_master_reviews(callback: CallbackQuery, session: AsyncSession) -> None:
    """Master reviews dashboard with average rating and star distribution."""
    user = await _get_or_create_user(session, callback.from_user)
    master_id = int(callback.data.split(":")[-1])

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    crm_svc = MasterCrmService(session)
    summary = await crm_svc.get_reviews_summary(master_id, limit=5)

    lines = [
        f"⭐ <b>Отзывы и рейтинг: {escape(master.display_name)}</b>\n",
        f"Рейтинг: <b>⭐ {summary['avg_rating']:.2f} / 5.0</b> (всего отзывов: <b>{summary['total_count']}</b>)\n",
        f"⭐⭐⭐⭐⭐ 5 звёзд: <b>{summary['stars_5']}</b>",
        f"⭐⭐⭐⭐ 4 звезды: <b>{summary['stars_4']}</b>",
        f"⭐⭐⭐ 3 звезды: <b>{summary['stars_3']}</b>",
        f"⭐⭐ 2 звезды: <b>{summary['stars_2']}</b>",
        f"⭐ 1 звезда: <b>{summary['stars_1']}</b>\n",
    ]

    if summary["recent_reviews"]:
        lines.append("💬 <b>Последние отзывы:</b>")
        for r in summary["recent_reviews"]:
            stars = "⭐" * r["rating"]
            date_str = r["created_at"].strftime("%d.%m.%Y")
            comment = f"\n   «{escape(r['comment'])}»" if r.get("comment") else ""
            lines.append(f"• {stars} от <b>{escape(r['client_name'])}</b> ({date_str}){comment}")
    else:
        lines.append("<i>Отзывов пока нет. Клиенты смогут оценить визит после завершения записи.</i>")

    text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=reviews_keyboard(master_id))
    await callback.answer()


# ---------------------------------------------------------------------------
# MASTER CONTACTS MANAGEMENT IN MANAGER BOT
# ---------------------------------------------------------------------------


@manager_router.callback_query(F.data.startswith("mgr:contact:edit:"))
async def cb_mgr_contact_edit(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt master to edit contact field."""
    user = await _get_or_create_user(session, callback.from_user)
    parts = callback.data.split(":")
    master_id = int(parts[3])
    field = parts[4]

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    label = CONTACT_FIELD_LABELS.get(field, field)
    await state.set_state(MasterContactStates.waiting_for_value)
    await state.update_data(master_id=master_id, field=field)

    text = (
        f"✏️ <b>Изменение поля: {label}</b>\n\n"
        "Отправьте новое значение сообщением в чат.\n"
        "Чтобы очистить это поле, отправьте <code>/clear</code>."
    )
    await callback.message.edit_text(text, reply_markup=manager_contact_cancel_keyboard(master_id))
    await callback.answer()


@manager_router.message(MasterContactStates.waiting_for_value)
async def msg_mgr_contact_save(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save contact field edit."""
    user = await _get_or_create_user(session, message.from_user)
    data = await state.get_data()
    master_id = data.get("master_id")
    field = data.get("field")

    if not master_id or not field:
        await state.clear()
        return

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        await message.answer("Проект не найден или нет доступа.")
        return

    contacts_svc = MasterContactsService(session)
    try:
        await contacts_svc.update_field(master_id, user.id, field, message.text)
    except ValueError as exc:
        await message.answer(f"❌ {exc}\nПопробуйте ещё раз или отправьте <code>/clear</code>:")
        return

    await state.clear()
    settings_obj = await contacts_svc.get(master_id)
    text = _render_contacts_summary_text(master.display_name, settings_obj)
    await message.answer("✅ Контакты успешно обновлены!\n\n" + text, reply_markup=manager_contacts_keyboard(master_id))


@manager_router.callback_query(F.data.startswith("mgr:contacts:"))
async def cb_mgr_contacts(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Show contact details in Manager Bot."""
    await state.clear()
    user = await _get_or_create_user(session, callback.from_user)
    master_id = int(callback.data.split(":")[-1])

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Проект не найден или нет доступа.", show_alert=True)
        return

    contacts_svc = MasterContactsService(session)
    settings_obj = await contacts_svc.get(master_id)
    text = _render_contacts_summary_text(master.display_name, settings_obj)
    await callback.message.edit_text(text, reply_markup=manager_contacts_keyboard(master_id))
    await callback.answer()


def _render_contacts_summary_text(project_name: str, settings_obj: Optional[MasterSettings]) -> str:
    """Render structured contacts view."""
    get_val = lambda f: (getattr(settings_obj, f, None) or "").strip() if settings_obj else ""

    lines = [f"📞 <b>Контакты студии: {escape(project_name)}</b>\n"]
    for field, label in CONTACT_FIELD_LABELS.items():
        val = get_val(field)
        val_str = escape(val) if val else "<i>не заполнено</i>"
        lines.append(f"• <b>{label}:</b> {val_str}")

    lines.append("\nНажмите на кнопку ниже, чтобы изменить нужное поле:")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 4: CRM Export
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:crm:export:"))
async def cb_crm_export(callback: CallbackQuery, session: AsyncSession) -> None:
    """Export clients to CSV file strictly scoped to master_id."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    crm_svc = MasterCrmService(session)
    csv_str = await crm_svc.export_clients_csv(master_id)
    csv_bytes = csv_str.encode("utf-8-sig")

    filename = f"clients_{master_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    doc = BufferedInputFile(csv_bytes, filename=filename)

    await callback.answer("Формирую CSV выгрузку...")
    if callback.message:
        await callback.message.answer_document(
            document=doc,
            caption=f"📤 <b>Экспорт клиентской базы «{escape(master.display_name)}»</b>\nФайл CSV готов для открытия в Excel.",
        )


# ---------------------------------------------------------------------------
# Phase 4: Services Management
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:services:"))
async def cb_manager_services_list(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """List services for master."""
    await state.clear()
    master_id = int(callback.data.split(":")[2])
    user = await _get_or_create_user(session, callback.from_user)

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    srv_repo = ServiceRepository(session)
    services = await srv_repo.list_all_for_admin(master_id)
    active_count = sum(1 for s in services if s.is_active and not s.is_archived)

    text = (
        f"💅 <b>Услуги и прайс-лист проекта «{escape(master.display_name)}»</b>\n\n"
        f"Всего активных услуг: <b>{active_count}</b>\n\n"
        "🟢 — услуга доступна для онлайн-записи\n"
        "🔴 — услуга скрыта от клиентов\n\n"
        "Выберите услугу для настройки или добавьте новую:"
    )
    await callback.message.edit_text(text, reply_markup=manager_services_list_keyboard(master_id, services))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:srv:card:"))
async def cb_manager_service_card(callback: CallbackQuery, session: AsyncSession) -> None:
    """Service details card."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    service_id = int(parts[4])

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    srv_repo = ServiceRepository(session)
    service = await srv_repo.get_by_id(service_id, master_id)
    if not service:
        await callback.answer("Услуга не найдена.", show_alert=True)
        return

    status_str = "🟢 Активна (доступна для записи)" if service.is_active else "🔴 Выключена (скрыта)"
    dep_str = f"{int(service.deposit_value)}%" if service.deposit_type == DepositType.PERCENT else f"{int(service.deposit_value)} ₽"
    desc_str = service.description or "<i>нет описания</i>"

    text = (
        f"💅 <b>Услуга: {escape(service.title)}</b>\n\n"
        f"• <b>Статус:</b> {status_str}\n"
        f"• <b>Стоимость:</b> {int(service.price)} ₽\n"
        f"• <b>Длительность:</b> {service.duration_min} мин.\n"
        f"• <b>Буфер после услуги:</b> {service.buffer_min} мин.\n"
        f"• <b>Предоплата:</b> {dep_str}\n\n"
        f"📝 <b>Описание:</b>\n{escape(desc_str)}"
    )
    await callback.message.edit_text(text, reply_markup=manager_service_detail_keyboard(master_id, service))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:srv:toggle:"))
async def cb_manager_service_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    """Toggle service active status."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    service_id = int(parts[4])

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    srv_repo = ServiceRepository(session)
    new_status = await srv_repo.toggle_active(service_id, master_id)
    if new_status is None:
        await callback.answer("Услуга не найдена.", show_alert=True)
        return

    service = await srv_repo.get_by_id(service_id, master_id)
    msg = "Услуга включена 🟢" if new_status else "Услуга выключена 🔴"
    await callback.answer(msg)
    await cb_manager_service_card(callback, session)


@manager_router.callback_query(F.data.startswith("mgr:srv:delete:"))
async def cb_manager_service_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    """Soft-delete / archive service without breaking appointment history."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    service_id = int(parts[4])

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    srv_repo = ServiceRepository(session)
    await srv_repo.archive(service_id, master_id)
    await callback.answer("Услуга удалена (архивирована)", show_alert=True)

    # Return to services list
    services = await srv_repo.list_all_for_admin(master_id)
    text = (
        f"💅 <b>Услуги и прайс-лист проекта «{escape(master.display_name)}»</b>\n\n"
        "Услуга успешно удалена.\n\n"
        "Выберите услугу для настройки или добавьте новую:"
    )
    await callback.message.edit_text(text, reply_markup=manager_services_list_keyboard(master_id, services))


@manager_router.callback_query(F.data.startswith("mgr:srv:add:"))
async def cb_manager_service_add(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Start adding a service in Manager Bot."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    await state.update_data(srv_master_id=master_id)
    await state.set_state(ManagerServiceStates.waiting_for_title)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:services:{master_id}")]]
    )
    text = "💅 <b>Добавление новой услуги</b>\n\nВведите название услуги (например: <i>Smart-педикюр</i>):"
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerServiceStates.waiting_for_title)
async def msg_manager_service_add_title(message: Message, state: FSMContext) -> None:
    """Receive title and prompt for price."""
    title = (message.text or "").strip()
    if not title or len(title) > 255:
        await message.answer("Название должно быть от 2 до 255 символов. Попробуйте ещё раз:")
        return

    await state.update_data(srv_title=title)
    await state.set_state(ManagerServiceStates.waiting_for_price)
    data = await state.get_data()
    master_id = data.get("srv_master_id", 0)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:services:{master_id}")]]
    )
    await message.answer(f"Услуга: <b>{escape(title)}</b>\n\n💰 <b>Введите стоимость услуги в рублях (число):</b>", reply_markup=cancel_kb)


@manager_router.message(ManagerServiceStates.waiting_for_price)
async def msg_manager_service_add_price(message: Message, state: FSMContext) -> None:
    """Receive price and prompt for duration."""
    raw = (message.text or "").strip().replace(" ", "").replace("₽", "").replace("руб", "")
    try:
        price = int(raw)
        if price < 0 or price > 1_000_000:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число (например, 2500):")
        return

    await state.update_data(srv_price=price)
    await state.set_state(ManagerServiceStates.waiting_for_duration)
    data = await state.get_data()
    master_id = data.get("srv_master_id", 0)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:services:{master_id}")]]
    )
    await message.answer(f"Стоимость: <b>{price} ₽</b>\n\n⏱ <b>Укажите длительность процедуры в минутах (например: 60, 90, 120):</b>", reply_markup=cancel_kb)


@manager_router.message(ManagerServiceStates.waiting_for_duration)
async def msg_manager_service_add_duration(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Receive duration, create service and display card."""
    raw = (message.text or "").strip().replace("мин", "")
    try:
        dur = int(raw)
        if dur <= 0 or dur > 1440:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число минут (от 10 до 720):")
        return

    data = await state.get_data()
    master_id = data.get("srv_master_id")
    if not master_id:
        await state.clear()
        await message.answer("Ошибка сессии.", reply_markup=main_menu_keyboard())
        return

    title = data.get("srv_title", "Услуга")
    price = data.get("srv_price", 2000)

    srv_repo = ServiceRepository(session)
    service = await srv_repo.create_service(
        master_id=master_id,
        title=title,
        price=price,
        duration_min=dur,
        buffer_min=15,
        deposit_type=DepositType.PERCENT,
        deposit_value=0,
        is_active=True,
    )
    await state.clear()

    text = f"✅ Услуга <b>«{escape(title)}»</b> успешно создана!\nСтоимость: {price} ₽, длительность: {dur} мин."
    await message.answer(text, reply_markup=manager_service_detail_keyboard(master_id, service))


@manager_router.callback_query(F.data.startswith("mgr:srv:edit:"))
async def cb_manager_service_edit(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Prompt for editing a specific service field."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    service_id = int(parts[4])
    field = parts[5]

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    srv_repo = ServiceRepository(session)
    service = await srv_repo.get_by_id(service_id, master_id)
    if not service:
        await callback.answer("Услуга не найдена.", show_alert=True)
        return

    await state.update_data(srv_master_id=master_id, srv_id=service_id, srv_field=field)
    await state.set_state(ManagerServiceStates.waiting_for_edit_field_value)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:srv:card:{master_id}:{service_id}")]]
    )

    prompts = {
        "title": "Введите новое название услуги:",
        "price": "Введите новую стоимость услуги в рублях (число):",
        "duration": "Введите длительность услуги в минутах (например: 60):",
        "buffer": "Введите буферное время после услуги в минутах (например: 15):",
        "deposit": "Введите процент предоплаты от 0 до 100% (например: 30) или 0 для отключения:",
    }
    await callback.message.edit_text(prompts.get(field, "Введите новое значение:"), reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerServiceStates.waiting_for_edit_field_value)
async def msg_manager_service_edit_value(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save edited service field."""
    data = await state.get_data()
    master_id = data.get("srv_master_id")
    service_id = data.get("srv_id")
    field = data.get("srv_field")

    if not master_id or not service_id or not field:
        await state.clear()
        return

    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        await message.answer("Ошибка доступа.")
        return

    srv_repo = ServiceRepository(session)
    raw = (message.text or "").strip()

    updates: Dict[str, Any] = {}
    try:
        if field == "title":
            if not raw or len(raw) > 255:
                raise ValueError("Название должно быть от 2 до 255 символов")
            updates["title"] = raw
        elif field == "price":
            val = int(raw.replace(" ", "").replace("₽", "").replace("руб", ""))
            if val < 0 or val > 1_000_000:
                raise ValueError("Цена должна быть от 0 до 1 000 000 ₽")
            updates["price"] = val
        elif field == "duration":
            val = int(raw.replace("мин", ""))
            if val <= 0 or val > 1440:
                raise ValueError("Длительность должна быть от 10 до 720 минут")
            updates["duration_min"] = val
        elif field == "buffer":
            val = int(raw.replace("мин", ""))
            if val < 0 or val > 240:
                raise ValueError("Буфер должен быть от 0 до 240 минут")
            updates["buffer_min"] = val
        elif field == "deposit":
            val = int(raw.replace("%", "").strip())
            if val < 0 or val > 100:
                raise ValueError("Процент предоплаты должен быть от 0 до 100%")
            updates["deposit_type"] = DepositType.PERCENT
            updates["deposit_value"] = val
    except ValueError as exc:
        await message.answer(f"❌ {exc}. Попробуйте ещё раз:")
        return

    updated_srv = await srv_repo.update_service(service_id, master_id, **updates)
    await state.clear()
    await message.answer("✅ Услуга успешно обновлена!", reply_markup=manager_service_detail_keyboard(master_id, updated_srv))


# ---------------------------------------------------------------------------
# Phase 4: Portfolio Management
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:portfolio:"))
async def cb_manager_portfolio(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Portfolio categories list."""
    await state.clear()
    master_id = int(callback.data.split(":")[2])
    user = await _get_or_create_user(session, callback.from_user)

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    port_repo = PortfolioRepository(session)
    categories = await port_repo.list_categories(master_id, active_only=False)

    text = (
        f"🖼 <b>Портфолио проекта «{escape(master.display_name)}»</b>\n\n"
        "Категории позволяют клиентам быстро находить примеры работ.\n\n"
        "Выберите категорию для добавления или просмотра фотографий:"
    )
    await callback.message.edit_text(text, reply_markup=manager_portfolio_categories_keyboard(master_id, categories))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:port:cat:add:"))
async def cb_manager_portfolio_cat_add(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Prompt for adding a portfolio category."""
    master_id = int(callback.data.split(":")[4])
    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    await state.update_data(port_master_id=master_id)
    await state.set_state(ManagerPortfolioStates.waiting_for_category_title)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:portfolio:{master_id}")]]
    )
    text = "📁 <b>Новая категория портфолио</b>\n\nВведите название категории (например: <i>Свадебный макияж</i>):"
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerPortfolioStates.waiting_for_category_title)
async def msg_manager_portfolio_cat_add(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Save new portfolio category."""
    title = (message.text or "").strip()
    if not title or len(title) > 128:
        await message.answer("Название категории должно содержать от 2 до 128 символов:")
        return

    data = await state.get_data()
    master_id = data.get("port_master_id")
    if not master_id:
        await state.clear()
        return

    port_repo = PortfolioRepository(session)
    await port_repo.create_category(master_id, title)
    await state.clear()

    categories = await port_repo.list_categories(master_id, active_only=False)
    text = f"✅ Категория <b>«{escape(title)}»</b> успешно создана!\n\nВыберите категорию для добавления фото:"
    await message.answer(text, reply_markup=manager_portfolio_categories_keyboard(master_id, categories))


@manager_router.callback_query(F.data.startswith("mgr:port:cat:del:"))
async def cb_manager_portfolio_cat_del(callback: CallbackQuery, session: AsyncSession) -> None:
    """Delete a portfolio category."""
    parts = callback.data.split(":")
    master_id = int(parts[4])
    cat_id = int(parts[5])

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    port_repo = PortfolioRepository(session)
    await port_repo.delete_category(cat_id, master_id)
    await callback.answer("Категория удалена.")

    categories = await port_repo.list_categories(master_id, active_only=False)
    await callback.message.edit_text(
        f"🖼 <b>Портфолио проекта «{escape(master.display_name)}»</b>\n\nКатегория успешно удалена:",
        reply_markup=manager_portfolio_categories_keyboard(master_id, categories),
    )


@manager_router.callback_query(F.data.startswith("mgr:port:cat:"))
async def cb_manager_portfolio_category(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """View photos inside a category."""
    await state.clear()
    parts = callback.data.split(":")
    master_id = int(parts[3])
    cat_id = int(parts[4])

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    port_repo = PortfolioRepository(session)
    category = await port_repo.get_category_by_id(cat_id, master_id)
    if not category:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    items = await port_repo.list_items(cat_id, master_id, active_only=False)
    text = (
        f"📁 <b>Категория: {escape(category.title)}</b>\n\n"
        f"Всего фотографий: <b>{len(items)}</b>\n\n"
        "Нажмите «Добавить фото», чтобы загрузить новую работу:"
    )
    await callback.message.edit_text(text, reply_markup=manager_portfolio_items_keyboard(master_id, cat_id, items))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:port:item:add:"))
async def cb_manager_portfolio_item_add(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Prompt master to send a photo."""
    parts = callback.data.split(":")
    master_id = int(parts[4])
    cat_id = int(parts[5])

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    await state.update_data(port_master_id=master_id, port_cat_id=cat_id)
    await state.set_state(ManagerPortfolioStates.waiting_for_photo)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:port:cat:{master_id}:{cat_id}")]]
    )
    text = "📸 <b>Загрузка фотографии</b>\n\nПришлите фотографию вашей работы (с описанием или без):"
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerPortfolioStates.waiting_for_photo)
async def msg_manager_portfolio_item_photo(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Receive photo and save into portfolio."""
    if not message.photo:
        await message.answer("Пожалуйста, отправьте именно фотографию (как фото, а не файл):")
        return

    data = await state.get_data()
    master_id = data.get("port_master_id")
    cat_id = data.get("port_cat_id")
    if not master_id or not cat_id:
        await state.clear()
        return

    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        return

    photo = message.photo[-1]
    caption = (message.caption or "").strip() or None

    port_repo = PortfolioRepository(session)
    await port_repo.add_item(
        master_id=master_id,
        category_id=cat_id,
        telegram_file_id=photo.file_id,
        telegram_file_unique_id=photo.file_unique_id,
        caption=caption,
        title=caption[:100] if caption else None,
    )
    await state.clear()

    items = await port_repo.list_items(cat_id, master_id, active_only=False)
    text = "✅ <b>Фотография успешно добавлена в портфолио!</b>\nКлиенты увидят её в галерее работ."
    await message.answer(text, reply_markup=manager_portfolio_items_keyboard(master_id, cat_id, items))


@manager_router.callback_query(F.data.startswith("mgr:port:item:del:"))
async def cb_manager_portfolio_item_del(callback: CallbackQuery, session: AsyncSession) -> None:
    """Delete a photo item from portfolio."""
    parts = callback.data.split(":")
    master_id = int(parts[4])
    item_id = int(parts[5])

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    port_repo = PortfolioRepository(session)
    item = await port_repo.get_item_by_id(item_id, master_id)
    cat_id = item.category_id if item else None

    await port_repo.delete_item(item_id, master_id)
    await callback.answer("Фотография удалена.")

    if cat_id:
        items = await port_repo.list_items(cat_id, master_id, active_only=False)
        await callback.message.edit_text(
            "Фотография удалена из категории.",
            reply_markup=manager_portfolio_items_keyboard(master_id, cat_id, items),
        )
    else:
        categories = await port_repo.list_categories(master_id, active_only=False)
        await callback.message.edit_text(
            "Фотография удалена.",
            reply_markup=manager_portfolio_categories_keyboard(master_id, categories),
        )


@manager_router.callback_query(F.data.startswith("mgr:port:item:"))
async def cb_manager_portfolio_item_card(callback: CallbackQuery, session: AsyncSession) -> None:
    """View photo item details."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    item_id = int(parts[4])

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    port_repo = PortfolioRepository(session)
    item = await port_repo.get_item_by_id(item_id, master_id)
    if not item:
        await callback.answer("Фото не найдено.", show_alert=True)
        return

    desc = item.caption or "<i>без описания</i>"
    text = f"🖼 <b>Работа #{item.id}</b>\n\n📝 Описание: {escape(desc)}"
    await callback.message.edit_text(
        text, reply_markup=manager_portfolio_item_detail_keyboard(master_id, item.id, item.category_id)
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Phase 4: Schedule Management
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:schedule:"))
async def cb_manager_schedule(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Display weekly schedule and booking parameters."""
    await state.clear()
    master_id = int(callback.data.split(":")[2])
    user = await _get_or_create_user(session, callback.from_user)

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    sch_repo = ScheduleRepository(session)
    templates = await sch_repo.get_weekly_templates(master_id)
    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)

    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    tmpl_map = {t.day_of_week: t for t in templates}

    lines = [f"📅 <b>График работы: {escape(master.display_name)}</b>\n"]
    for d in range(7):
        t = tmpl_map.get(d)
        if not t or t.is_day_off:
            lines.append(f"• <b>{day_names[d]}:</b> <i>Выходной</i>")
        else:
            breaks_str = ""
            if t.breaks:
                b_texts = [f"{b.break_start.strftime('%H:%M')}–{b.break_end.strftime('%H:%M')}" for b in t.breaks]
                breaks_str = f" (перерыв: {', '.join(b_texts)})"
            lines.append(f"• <b>{day_names[d]}:</b> {t.work_start.strftime('%H:%M')} – {t.work_end.strftime('%H:%M')}{breaks_str}")

    adv_h = settings_obj.min_advance_hours if settings_obj else 2
    horiz_d = settings_obj.booking_horizon_days if settings_obj else 30
    lines.append(f"\n⏱ <b>Минимум до записи:</b> {adv_h} ч.")
    lines.append(f"📆 <b>Горизонт записи:</b> {horiz_d} дней")

    await callback.message.edit_text("\n".join(lines), reply_markup=manager_schedule_menu_keyboard(master_id, settings_obj))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:sch:hours:"))
async def cb_manager_schedule_hours(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt to set standard working hours."""
    master_id = int(callback.data.split(":")[3])
    await state.update_data(sch_master_id=master_id)
    await state.set_state(ManagerScheduleStates.waiting_for_hours)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:schedule:{master_id}")]]
    )
    text = (
        "🕒 <b>Настройка рабочих часов</b>\n\n"
        "Введите время начала и окончания рабочего дня для будней (Пн–Пт) через дефис\n"
        "(например: <code>10:00-19:00</code> или <code>09:00-18:00</code>):"
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerScheduleStates.waiting_for_hours)
async def msg_manager_schedule_hours(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save standard working hours."""
    from datetime import time as dt_time
    raw = (message.text or "").strip().replace(" ", "")
    if "-" not in raw:
        await message.answer("Пожалуйста, введите время в формате <code>10:00-19:00</code>:")
        return

    try:
        s_part, e_part = raw.split("-", 1)
        sh, sm = map(int, s_part.split(":"))
        eh, em = map(int, e_part.split(":"))
        start_t = dt_time(sh, sm)
        end_t = dt_time(eh, em)
        if start_t >= end_t:
            raise ValueError
    except Exception:
        await message.answer("Некорректное время. Введите в формате <code>10:00-19:00</code>:")
        return

    data = await state.get_data()
    master_id = data.get("sch_master_id")
    if not master_id:
        await state.clear()
        return

    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        return

    sch_repo = ScheduleRepository(session)
    for day in range(5):
        await sch_repo.set_template(
            weekday=day,
            is_day_off=False,
            work_start=start_t,
            work_end=end_t,
            breaks=[(dt_time(14, 0), dt_time(15, 0))],
            master_id=master_id,
        )
    await state.clear()
    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    await message.answer(f"✅ Рабочие часы обновлены: <b>{start_t.strftime('%H:%M')} – {end_t.strftime('%H:%M')}</b>!", reply_markup=manager_schedule_menu_keyboard(master_id, settings_obj))


@manager_router.callback_query(F.data.startswith("mgr:sch:breaks:"))
async def cb_manager_schedule_breaks(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt to set break time."""
    master_id = int(callback.data.split(":")[3])
    await state.update_data(sch_master_id=master_id)
    await state.set_state(ManagerScheduleStates.waiting_for_break)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:schedule:{master_id}")]]
    )
    text = (
        "☕️ <b>Настройка регулярного перерыва</b>\n\n"
        "Введите время перерыва через дефис (например: <code>14:00-15:00</code>)\n"
        "или отправьте <code>0</code>, чтобы убрать перерыв:"
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerScheduleStates.waiting_for_break)
async def msg_manager_schedule_breaks(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save break hours."""
    from datetime import time as dt_time
    raw = (message.text or "").strip().replace(" ", "")

    breaks_list = []
    if raw != "0":
        try:
            s_part, e_part = raw.split("-", 1)
            sh, sm = map(int, s_part.split(":"))
            eh, em = map(int, e_part.split(":"))
            b_start = dt_time(sh, sm)
            b_end = dt_time(eh, em)
            if b_start >= b_end:
                raise ValueError
            breaks_list.append((b_start, b_end))
        except Exception:
            await message.answer("Некорректное время. Введите в формате <code>14:00-15:00</code> или <code>0</code>:")
            return

    data = await state.get_data()
    master_id = data.get("sch_master_id")
    if not master_id:
        await state.clear()
        return

    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        return

    sch_repo = ScheduleRepository(session)
    templates = await sch_repo.get_weekly_templates(master_id)
    for t in templates:
        if not t.is_day_off:
            await sch_repo.set_template(
                weekday=t.day_of_week,
                is_day_off=False,
                work_start=t.work_start,
                work_end=t.work_end,
                breaks=breaks_list,
                master_id=master_id,
            )
    await state.clear()
    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    msg = "Перерыв убран." if not breaks_list else f"Перерыв установлен: <b>{raw}</b>!"
    await message.answer(f"✅ {msg}", reply_markup=manager_schedule_menu_keyboard(master_id, settings_obj))


@manager_router.callback_query(F.data.startswith("mgr:sch:dayoff:"))
async def cb_manager_schedule_dayoff(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt to add a holiday / day off for a specific date."""
    master_id = int(callback.data.split(":")[3])
    await state.update_data(sch_master_id=master_id)
    await state.set_state(ManagerScheduleStates.waiting_for_day_off_date)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:schedule:{master_id}")]]
    )
    text = (
        "🏖 <b>Добавить выходной день или отпуск</b>\n\n"
        "Введите дату в формате ДД.ММ.ГГГГ (например: <code>15.11.2026</code>):"
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerScheduleStates.waiting_for_day_off_date)
async def msg_manager_schedule_dayoff(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save date exception as day off."""
    from datetime import datetime as dt_datetime
    raw = (message.text or "").strip()
    try:
        t_date = dt_datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Некорректная дата. Введите в формате <code>ДД.ММ.ГГГГ</code> (например: <code>15.11.2026</code>):")
        return

    data = await state.get_data()
    master_id = data.get("sch_master_id")
    if not master_id:
        await state.clear()
        return

    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        return

    sch_repo = ScheduleRepository(session)
    await sch_repo.set_date_exception(
        target_date=t_date,
        is_day_off=True,
        comment="Выходной день мастера",
        master_id=master_id,
    )
    await state.clear()
    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    await message.answer(
        f"✅ Дата <b>{raw}</b> успешно отмечена как выходной! Запись на этот день заблокирована.",
        reply_markup=manager_schedule_menu_keyboard(master_id, settings_obj),
    )


@manager_router.callback_query(F.data.startswith("mgr:sch:advance:"))
async def cb_manager_schedule_advance(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt to edit min advance hours."""
    master_id = int(callback.data.split(":")[3])
    await state.update_data(sch_master_id=master_id)
    await state.set_state(ManagerScheduleStates.waiting_for_advance_hours)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:schedule:{master_id}")]]
    )
    text = (
        "⏱ <b>Минимальное время до записи</b>\n\n"
        "За сколько часов до визита клиент может записаться онлайн?\n"
        "Введите число часов (например: <code>2</code>, <code>4</code>, <code>12</code>, <code>24</code>):"
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerScheduleStates.waiting_for_advance_hours)
async def msg_manager_schedule_advance(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save min advance hours."""
    raw = (message.text or "").strip()
    try:
        hours = int(raw)
        if hours < 1 or hours > 168:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 1 до 168 часов:")
        return

    data = await state.get_data()
    master_id = data.get("sch_master_id")
    if not master_id:
        await state.clear()
        return

    await MasterSettingsRepository(session).update(master_id, min_advance_hours=hours)
    await state.clear()
    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    await message.answer(f"✅ Минимальное время до записи установлено: <b>{hours} ч.</b>", reply_markup=manager_schedule_menu_keyboard(master_id, settings_obj))


@manager_router.callback_query(F.data.startswith("mgr:sch:horizon:"))
async def cb_manager_schedule_horizon(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt to edit booking horizon days."""
    master_id = int(callback.data.split(":")[3])
    await state.update_data(sch_master_id=master_id)
    await state.set_state(ManagerScheduleStates.waiting_for_horizon_days)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:schedule:{master_id}")]]
    )
    text = (
        "📆 <b>Горизонт записи</b>\n\n"
        "На сколько дней вперёд открыта запись в календаре?\n"
        "Введите число дней (например: <code>14</code>, <code>30</code>, <code>60</code>):"
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerScheduleStates.waiting_for_horizon_days)
async def msg_manager_schedule_horizon(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save booking horizon days."""
    raw = (message.text or "").strip()
    try:
        days = int(raw)
        if days < 1 or days > 365:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 1 до 365 дней:")
        return

    data = await state.get_data()
    master_id = data.get("sch_master_id")
    if not master_id:
        await state.clear()
        return

    await MasterSettingsRepository(session).update(master_id, booking_horizon_days=days)
    await state.clear()
    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    await message.answer(f"✅ Горизонт записи установлен: <b>{days} дней</b>", reply_markup=manager_schedule_menu_keyboard(master_id, settings_obj))


# ---------------------------------------------------------------------------
# Phase 4: Settings Hub
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:settings:"))
async def cb_manager_settings(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Master project settings hub."""
    await state.clear()
    master_id = int(callback.data.split(":")[2])
    user = await _get_or_create_user(session, callback.from_user)

    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    text = (
        f"⚙️ <b>Настройки проекта «{escape(master.display_name)}»</b>\n\n"
        "Здесь собраны все параметры вашего бизнеса:\n"
        "• Контакты, адрес и рабочие часы\n"
        "• Услуги, прайс и портфолио\n"
        "• Уведомления и правила предоплаты\n\n"
        "Выберите нужный раздел:"
    )
    await callback.message.edit_text(text, reply_markup=manager_settings_menu_keyboard(master_id, settings_obj))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:set:name:"))
async def cb_manager_set_name(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt to edit project name."""
    master_id = int(callback.data.split(":")[3])
    await state.update_data(set_master_id=master_id)
    await state.set_state(ManagerSettingsStates.waiting_for_name)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:settings:{master_id}")]]
    )
    text = "🏷 <b>Изменение названия проекта</b>\n\nВведите новое название студии или профиля:"
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerSettingsStates.waiting_for_name)
async def msg_manager_set_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Save new project name."""
    name = (message.text or "").strip()
    if not name or len(name) < 2 or len(name) > 128:
        await message.answer("Название должно содержать от 2 до 128 символов:")
        return

    data = await state.get_data()
    master_id = data.get("set_master_id")
    if not master_id:
        await state.clear()
        return

    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        return

    master.display_name = name
    await session.flush()
    await state.clear()

    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    await message.answer(f"✅ Название проекта изменено на: <b>«{escape(name)}»</b>!", reply_markup=manager_settings_menu_keyboard(master_id, settings_obj))


@manager_router.callback_query(F.data.startswith("mgr:set:about:"))
async def cb_manager_set_about(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt to edit about text."""
    master_id = int(callback.data.split(":")[3])
    await state.update_data(set_master_id=master_id)
    await state.set_state(ManagerSettingsStates.waiting_for_description)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:settings:{master_id}")]]
    )
    text = "📝 <b>Описание проекта / О мастере</b>\n\nВведите текст о себе, вашем опыте и материалах:"
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(ManagerSettingsStates.waiting_for_description)
async def msg_manager_set_about(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Save about text."""
    desc = (message.text or "").strip()
    if len(desc) > 2000:
        await message.answer("Описание слишком длинное (максимум 2000 символов):")
        return

    data = await state.get_data()
    master_id = data.get("set_master_id")
    if not master_id:
        await state.clear()
        return

    await MasterSettingsRepository(session).update(master_id, about_text=desc)
    await state.clear()

    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    await message.answer("✅ Описание успешно обновлено!", reply_markup=manager_settings_menu_keyboard(master_id, settings_obj))


@manager_router.callback_query(F.data.startswith("mgr:set:notif:"))
async def cb_manager_set_notif(callback: CallbackQuery, session: AsyncSession) -> None:
    """Notification settings."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    r24 = settings_obj.reminder_24h_enabled if settings_obj else True
    r3 = settings_obj.reminder_3h_enabled if settings_obj else True

    text = (
        "🔔 <b>Настройка автоматических напоминаний</b>\n\n"
        "Бот автоматически отправляет напоминания клиентам перед визитом:\n"
        "• <b>За 24 часа</b> — подтверждение визита\n"
        "• <b>За 3 часа</b> — напоминание в день визита\n\n"
        "Нажмите на кнопку, чтобы включить или отключить:"
    )
    await callback.message.edit_text(text, reply_markup=manager_notification_settings_keyboard(master_id, r24, r3))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:notif:toggle:"))
async def cb_manager_notif_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    """Toggle reminder setting."""
    parts = callback.data.split(":")
    master_id = int(parts[3])
    r_type = parts[4]

    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    settings_repo = MasterSettingsRepository(session)
    settings_obj = await settings_repo.get_by_master_id(master_id)
    if not settings_obj:
        settings_obj = MasterSettings(master_id=master_id)
        session.add(settings_obj)
        await session.flush()

    if r_type == "24h":
        settings_obj.reminder_24h_enabled = not settings_obj.reminder_24h_enabled
    elif r_type == "3h":
        settings_obj.reminder_3h_enabled = not settings_obj.reminder_3h_enabled
    await session.flush()

    await callback.answer("Настройка обновлена.")
    await cb_manager_set_notif(callback, session)


@manager_router.callback_query(F.data.startswith("mgr:set:prepay:"))
async def cb_manager_set_prepay(callback: CallbackQuery, session: AsyncSession) -> None:
    """Prepayment rules."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    hold_m = settings_obj.hold_duration_minutes if settings_obj else 30
    cancel_h = settings_obj.cancel_policy_hours if settings_obj else 24

    text = (
        "💳 <b>Правила предоплаты и удержания</b>\n\n"
        f"• <b>Время на оплату чека:</b> {hold_m} мин. (после этого бронь аннулируется)\n"
        f"• <b>Бесплатная отмена за:</b> {cancel_h} ч. до визита\n\n"
        "<i>Размер предоплаты (процент или фиксированная сумма) настраивается индивидуально в каждой услуге в разделе «Услуги и прайс».</i>"
    )
    await callback.message.edit_text(text, reply_markup=manager_prepayment_settings_keyboard(master_id, settings_obj))
    await callback.answer()
