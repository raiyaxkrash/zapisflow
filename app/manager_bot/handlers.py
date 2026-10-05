"""Command and callback handlers for platform Manager Bot."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
import inspect
import json
import logging
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ManagedBotCreated,
    ManagedBotUpdated,
    Message,
    ReplyKeyboardRemove,
)
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
    admin_audit_log_detail_keyboard,
    admin_audit_logs_keyboard,
    admin_bot_detail_keyboard,
    admin_bot_hard_delete_confirm_keyboard,
    admin_bots_keyboard,
    admin_dashboard_keyboard,
    admin_menu_keyboard,
    admin_metrics_keyboard,
    admin_payment_detail_keyboard,
    admin_payments_keyboard,
    admin_plan_detail_keyboard,
    admin_plan_duration_presets_keyboard,
    admin_plan_features_keyboard,
    admin_plan_price_confirm_keyboard,
    admin_plans_keyboard,
    admin_project_detail_keyboard,
    admin_project_hard_delete_confirm_keyboard,
    admin_projects_keyboard,
    admin_subscription_detail_keyboard,
    admin_subscription_extend_presets_keyboard,
    admin_subscription_extend_reason_keyboard,
    admin_subscription_history_keyboard,
    admin_subscription_set_expiry_confirm_keyboard,
    admin_subscriptions_keyboard,
    admin_user_bots_keyboard,
    admin_user_detail_keyboard,
    admin_user_payments_keyboard,
    admin_user_projects_keyboard,
    admin_user_subscriptions_keyboard,
    admin_users_keyboard,
    bot_connect_method_choice_keyboard,
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
    managed_bot_prepare_keyboard,
    managed_bot_reply_keyboard,
    managed_bot_rotate_keyboard,
    managed_bot_success_keyboard,
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
    reviews_keyboard,
    staff_card_keyboard,
    staff_list_keyboard,
    staff_services_keyboard,
    stats_period_keyboard,
    subscription_card_keyboard,
    subscription_canceled_keyboard,
    subscription_checkout_keyboard,
    subscription_payment_keyboard,
    subscription_pending_keyboard,
    subscription_projects_keyboard,
    subscription_success_keyboard,
    support_button,
)
from app.services.bot_registry import BotRegistry
from app.services.crm_service import MasterCrmService
from app.services.master_authorization_service import AdminRole, MasterAuthorizationService
from app.services.master_contacts import CONTACT_FIELD_LABELS, MasterContactsService
from app.services.platform_admin_service import PlatformAdminService
from app.services.rate_limiter import check_rate_limit
from app.services.subscription_entitlement_service import SubscriptionEntitlementService
from app.services.subscription_service import SubscriptionService
from app.database.session import async_session_factory
from app.services.billing.yookassa_checkout import YooKassaCheckoutService
from app.services.billing.yookassa_client import YooKassaClient, YooKassaGatewayError
from app.manager_bot.states import (
    AdminBotStates,
    AdminPlanStates,
    AdminProjectStates,
    AdminSubscriptionStates,
    ConnectBotStates,
    CreateMasterStates,
    CrmNoteStates,
    CrmSearchStates,
    ManagedBotStates,
    ManagerPortfolioStates,
    ManagerScheduleStates,
    ManagerServiceStates,
    ManagerSettingsStates,
    ManagerStaffStates,
    MasterContactStates,
    MasterOnboardingStates,
    RotateTokenStates,
    SubscriptionCheckoutStates,
)
from app.database.models.staff import StaffMember
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.managed_bot_request_repository import ManagedBotRequestRepository
from app.repositories.master_repository import MasterRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.portfolio_repository import PortfolioRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.staff_repository import StaffRepository
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
from app.services.managed_bot_service import ManagedBotService
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


async def _get_accessible_or_owned_masters(master_repo: MasterRepository, user_id: int):
    """Safely return accessible masters with fallback for mock sessions in unit tests."""
    if hasattr(master_repo, "list_accessible_for_user"):
        res = master_repo.list_accessible_for_user(user_id)
        if inspect.isawaitable(res):
            return await res
    if hasattr(master_repo, "list_by_owner_id"):
        res_owner = master_repo.list_by_owner_id(user_id)
        if inspect.isawaitable(res_owner):
            return await res_owner
    return []


# ---------------------------------------------------------------------------
# Navigation & Start
# ---------------------------------------------------------------------------

@manager_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Manager Bot /start entry point with support for staff invite links."""
    await state.clear()
    user = await _get_or_create_user(session, message.from_user)
    master_repo = MasterRepository(session)

    # 1. Check for deep-link staff invite: /start inv_<token>
    try:
        cmd_text = getattr(message, "text", "") or ""
    except (AttributeError, TypeError):
        cmd_text = ""
    parts = cmd_text.split(maxsplit=1) if isinstance(cmd_text, str) else []
    if len(parts) > 1 and parts[1].startswith("inv_"):
        token_plain = parts[1][4:]
        staff_repo = StaffRepository(session)
        staff = await staff_repo.claim_invite_token_atomic(
            token_plain=token_plain,
            user_id=user.id,
            user_telegram_id=user.telegram_id,
        )
        if staff:
            master = await master_repo.get_by_id(staff.master_id)
            studio_name = master.display_name if master else "студии"
            await message.answer(
                f"🎉 <b>Добро пожаловать в команду!</b>\n\n"
                f"Вы успешно присоединились к проекту «<b>{escape(studio_name)}</b>» в качестве специалиста <b>{escape(staff.display_name)}</b>.\n\n"
                f"Вам открыт доступ к вашему расписанию, записям и клиентам:",
                reply_markup=project_list_keyboard([master] if master else []),
            )
            return
        else:
            await message.answer(
                "⚠️ <b>Ссылка-приглашение недействительна</b>\n\n"
                "Срок действия ссылки истёк (48 часов) или она уже была активирована.",
                reply_markup=main_menu_keyboard(),
            )
            return

    masters = await _get_accessible_or_owned_masters(master_repo, user.id)
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
    """List of all owned or assigned master projects."""
    await state.clear()
    user = await _get_or_create_user(session, callback.from_user)
    master_repo = MasterRepository(session)
    masters = await _get_accessible_or_owned_masters(master_repo, user.id)

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

    # Create primary StaffMember for new project
    staff_repo = StaffRepository(session)
    await staff_repo.create_staff(
        master_id=master.id,
        display_name=user.first_name or master.display_name,
        user_id=user.id,
        sort_order=0,
    )

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
        await settings_repo.update_settings(master_id, studio_address=addr)

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
        await settings_repo.update_settings(master_id, studio_phone=phone, whatsapp_phone=phone)

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
    if not master:
        await callback.answer("Проект не найден.", show_alert=True)
        return

    auth_svc = MasterAuthorizationService(session)
    role = await auth_svc.get_role(master_id, user.id)
    if role == AdminRole.NONE:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    is_staff_only = (role == AdminRole.STAFF)
    staff_id = await auth_svc.get_staff_id_for_user(master_id, user.id) if is_staff_only else None

    crm_svc = MasterCrmService(session)
    dashboard = await crm_svc.get_today_dashboard(master.id, staff_id=staff_id)
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

    if is_staff_only:
        text = (
            f"👤 <b>Кабинет специалиста: {escape(master.display_name)}</b>{act_suffix}\n\n"
            f"📊 <b>Ваши показатели сегодня:</b>\n"
            f"📅 Записей: <b>{dashboard['total_today']}</b>\n"
            f"💰 Выручка: <b>{int(dashboard['revenue_today'])} ₽</b>\n"
            f"👥 Клиентов: <b>{dashboard['unique_clients_today']}</b>\n"
            f"🆕 Новых: <b>{dashboard['new_clients_today']}</b>\n\n"
            f"{nxt_text}"
        )
        await callback.message.edit_text(
            text,
            reply_markup=project_card_keyboard(master, None, False, is_staff_only=True),
        )
        await callback.answer()
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

    staff_breakdown_text = ""
    if dashboard.get("staff_breakdown") and len(dashboard["staff_breakdown"]) > 1:
        staff_breakdown_text = "👥 <b>Загрузка специалистов сегодня:</b>\n"
        for sb in dashboard["staff_breakdown"]:
            staff_breakdown_text += f"• {escape(sb['name'])}: {sb['total_today']} зап. ({int(sb['revenue_today'])} ₽)\n"
        staff_breakdown_text += "\n"

    text = (
        f"⚙️ <b>Проект: {escape(master.display_name)}</b>{act_suffix}\n\n"
        f"📊 <b>Сегодня:</b>\n"
        f"📅 Записей: <b>{dashboard['total_today']}</b>\n"
        f"💰 Выручка: <b>{int(dashboard['revenue_today'])} ₽</b>\n"
        f"👥 Клиентов: <b>{dashboard['unique_clients_today']}</b>\n"
        f"🆕 Новых: <b>{dashboard['new_clients_today']}</b>\n\n"
        f"{nxt_text}"
        f"{staff_breakdown_text}"
        f"🤖 <b>Telegram-бот:</b> {bot_info}\n"
        f"📊 <b>Статус бота:</b> {bot_status_str}\n"
        f"💳 <b>Подписка:</b> {sub_info}\n"
        f"📋 <b>Готовность к запуску:</b> {readiness_text}\n"
    )
    await callback.message.edit_text(
        text,
        reply_markup=project_card_keyboard(master, bot_instance, is_ready, is_staff_only=False, can_configure_bot=role == AdminRole.OWNER),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Bot Onboarding: Connect & Token Input
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:bot:connect:"))
async def cb_connect_bot(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Show bot connection method choice: Managed Bot (1-click) or manual token."""
    master_id = int(callback.data.split(":")[3])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    await state.clear()
    await state.update_data(master_id=master_id)

    text = (
        f"🤖 <b>Подключение Telegram-бота к проекту «{escape(master.display_name)}»</b>\n\n"
        "Выберите удобный способ подключения:\n\n"
        "✨ <b>Создать нового бота в 1 клик (Рекомендуется)</b>\n"
        "Официальная функция Telegram Managed Bots. Бот регистрируется мгновенно прямо в интерфейсе Telegram — "
        "не нужно переходить в @BotFather, копировать токены и вводить команды вручную.\n\n"
        "🔑 <b>Подключить через токен (BotFather)</b>\n"
        "Традиционный способ: создание бота через диалог с @BotFather и отправка HTTP API токена."
    )
    await callback.message.edit_text(text, reply_markup=bot_connect_method_choice_keyboard(master_id))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:token:start:"))
async def cb_token_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Prompt user with BotFather steps to connect a bot manually via token."""
    master_id = int(callback.data.split(":")[4])
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


@manager_router.callback_query(F.data.startswith("mgr:bot:mg:prep:"))
async def cb_managed_bot_prepare(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Prepare Telegram Managed Bot onboarding screen with suggestions and deep link."""
    master_id = int(callback.data.split(":")[4])
    user = await _get_or_create_user(session, callback.from_user)

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    managed_svc = ManagedBotService()
    suggested_username, suggested_name = managed_svc.suggest_username_and_name(master.display_name)

    manager_username = settings.manager_bot_username
    if not manager_username:
        try:
            me = await callback.bot.get_me()
            manager_username = me.username or ""
        except Exception:
            manager_username = ""

    gateway = TelegramProvisioningGateway()
    manager_token = settings.manager_bot_token.strip()

    # Check manager bot mode if token is present
    can_manage = True
    if manager_token:
        try:
            can_manage = await gateway.check_manager_bot_mode(manager_token)
        except Exception as exc:
            logger.warning("Could not query check_manager_bot_mode: %s", exc)
            can_manage = True

    if not manager_username or not can_manage:
        logger.warning(
            "Manager bot not ready for managed bot creation (username=%s, can_manage=%s)",
            manager_username,
            can_manage,
        )
        text = (
            "⚠️ <b>Создание бота через Telegram временно недоступно</b>\n\n"
            "Платформенный бот ещё не настроен для автоматического создания ботов. "
            "Вы можете быстро подключить своего бота по токену из BotFather."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔑 Подключить по токену", callback_data=f"mgr:bot:token:start:{master_id}")],
                [InlineKeyboardButton(text="« Назад", callback_data=f"mgr:bot:connect:{master_id}")],
            ]
        )
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer()
        return

    creation_url = managed_svc.build_managed_bot_deep_link(
        manager_bot_username=manager_username,
        suggested_username=suggested_username,
        suggested_name=suggested_name,
    )

    req_repo = ManagedBotRequestRepository(session)
    await req_repo.create_or_renew_request(
        owner_user_id=user.id,
        telegram_owner_user_id=callback.from_user.id,
        master_id=master_id,
        suggested_name=suggested_name,
        suggested_username=suggested_username,
    )

    await state.set_state(ManagedBotStates.waiting_for_creation)
    await state.update_data(
        master_id=master_id,
        suggested_username=suggested_username,
        suggested_name=suggested_name,
    )

    text = (
        "✨ <b>Создание нового бота для записи клиентов</b>\n\n"
        f"🏢 Проект: <b>{escape(master.display_name)}</b>\n"
        f"🤖 Имя бота: <b>{escape(suggested_name)}</b>\n"
        f"🔗 Предложенный логин: <code>@{suggested_username}</code>\n\n"
        "Для создания бота нажмите кнопку <b>«🚀 Создать бота в Telegram»</b> ниже.\n"
        "Telegram откроет окно создания. После подтверждения бот будет мгновенно зарегистрирован и подключён к платформе ZapisFlow!\n\n"
        "<i>Вы также можете отправить кнопку прямо в чат или задать собственный логин.</i>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=managed_bot_prepare_keyboard(master_id, creation_url, suggested_username),
    )
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:bot:mg:reply:"))
async def cb_managed_bot_reply_btn(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Send reply keyboard with KeyboardButtonRequestManagedBot button."""
    data = await state.get_data()
    suggested_name = data.get("suggested_name", "Бот записи")
    suggested_username = data.get("suggested_username", "zapisflow_bot")

    reply_kb = managed_bot_reply_keyboard(suggested_name, suggested_username)
    await callback.message.answer(
        "👇 <b>Нажмите кнопку ниже под строкой ввода</b>, чтобы создать бота через интерфейс Telegram:",
        reply_markup=reply_kb,
    )
    await callback.answer("Кнопка добавлена на панель ввода")


@manager_router.callback_query(F.data.startswith("mgr:bot:mg:custom:"))
async def cb_managed_bot_custom(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Prompt master to input a custom handle for managed bot."""
    master_id = int(callback.data.split(":")[4])
    await state.set_state(ManagedBotStates.waiting_for_custom_username)
    await state.update_data(master_id=master_id)
    text = (
        "✏️ <b>Введите желаемый логин для бота:</b>\n\n"
        "Правила Telegram:\n"
        "• От 5 до 32 символов (латинские буквы, цифры, подчёркивание)\n"
        "• Обязательно заканчивается на <code>bot</code> (например, <code>beauty_anna_bot</code>)\n\n"
        "Отправьте желаемый логин ответным сообщением:"
    )
    await callback.message.edit_text(text, reply_markup=cancel_keyboard())
    await callback.answer()


@manager_router.message(ManagedBotStates.waiting_for_custom_username)
async def msg_receive_custom_bot_username(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate and apply custom username for managed bot."""
    raw_text = (message.text or "").strip()
    if raw_text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Главное меню:", reply_markup=main_menu_keyboard())
        return

    managed_svc = ManagedBotService()
    norm_username, err = managed_svc.normalize_username(raw_text)
    if err or not norm_username:
        variants = managed_svc.generate_username_variants(raw_text)
        variants_str = ", ".join(f"<code>@{v}</code>" for v in variants[:3])
        await message.answer(
            f"❌ <b>Некорректный логин:</b> {err}\n\n"
            f"Попробуйте один из вариантов: {variants_str}\n"
            "Или отправьте другой вариант:",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    if not master_id:
        await message.answer("Сессия истекла.", reply_markup=main_menu_keyboard())
        await state.clear()
        return

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    suggested_name = master.display_name if master else "Бот записи"

    manager_username = settings.manager_bot_username
    if not manager_username:
        try:
            me = await message.bot.get_me()
            manager_username = me.username or ""
        except Exception:
            manager_username = ""

    creation_url = managed_svc.build_managed_bot_deep_link(
        manager_bot_username=manager_username,
        suggested_username=norm_username,
        suggested_name=suggested_name,
    )

    user = await _get_or_create_user(session, message.from_user)
    req_repo = ManagedBotRequestRepository(session)
    await req_repo.create_or_renew_request(
        owner_user_id=user.id,
        telegram_owner_user_id=message.from_user.id,
        master_id=master_id,
        suggested_name=suggested_name,
        suggested_username=norm_username,
    )

    await state.set_state(ManagedBotStates.waiting_for_creation)
    await state.update_data(
        master_id=master_id,
        suggested_username=norm_username,
        suggested_name=suggested_name,
    )

    text = (
        "✨ <b>Создание нового бота для записи клиентов</b>\n\n"
        f"🏢 Проект: <b>{escape(suggested_name)}</b>\n"
        f"🔗 Выбранный логин: <code>@{norm_username}</code>\n\n"
        "Нажмите кнопку <b>«🚀 Создать бота в Telegram»</b> для завершения создания в Telegram:"
    )
    await message.answer(
        text,
        reply_markup=managed_bot_prepare_keyboard(master_id, creation_url, norm_username),
    )


@manager_router.message(F.text == "❌ Отмена")
async def msg_cancel_reply(message: Message, state: FSMContext) -> None:
    """Handle cancel button from native reply keyboard."""
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Главное меню:", reply_markup=main_menu_keyboard())


async def _handle_managed_bot_provisioning(
    session: AsyncSession,
    created_bot_user: Any,
    telegram_owner_user_id: int,
    state: Optional[FSMContext] = None,
    registry: Optional[BotRegistry] = None,
) -> tuple[Optional[Any], Optional[str]]:
    """Common logic for provisioning a newly created managed bot."""
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(telegram_owner_user_id)
    if not user:
        return None, "Пользователь не найден в системе."

    bot_repo = BotInstanceRepository(session)
    existing_bot = await bot_repo.get_by_telegram_bot_id(created_bot_user.id)
    if existing_bot:
        logger.info(
            "Bot %s already provisioned for master %s (idempotent event)",
            created_bot_user.id,
            existing_bot.master_id,
        )
        if state:
            await state.clear()
        return existing_bot, None

    req_repo = ManagedBotRequestRepository(session)
    pending_req = await req_repo.get_active_pending_request(
        telegram_owner_user_id=telegram_owner_user_id,
        for_update=True,
    )
    if not pending_req:
        return None, "Не удалось определить проект, для которого создавался бот. Начните подключение заново."

    master_id = pending_req.master_id
    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await req_repo.fail_request(pending_req.id)
        return None, "Проект не найден или доступ к нему запрещён."

    gateway = TelegramProvisioningGateway()
    manager_token = settings.manager_bot_token.strip()
    if not manager_token:
        await req_repo.fail_request(pending_req.id)
        return None, "Токен платформенного бота не настроен на сервере."

    try:
        token = await gateway.get_managed_bot_token(
            manager_token=manager_token,
            bot_id=created_bot_user.id,
        )
    except Exception as exc:
        await req_repo.fail_request(pending_req.id)
        logger.error(
            "Failed to get managed bot token for bot %s (owner %s): %s",
            created_bot_user.id,
            telegram_owner_user_id,
            exc,
        )
        return None, f"Не удалось получить токен созданного бота от Telegram: {exc}"

    service = BotProvisioningService(session=session, gateway=gateway, registry=registry)
    bot_identity = BotIdentity(
        id=created_bot_user.id,
        username=created_bot_user.username or "",
        first_name=created_bot_user.first_name,
    )

    try:
        bot_instance = await service.provision_managed_bot(
            master_id=master_id,
            actor_user_id=user.id,
            bot_identity=bot_identity,
            token=token,
            telegram_owner_user_id=telegram_owner_user_id,
        )
        await req_repo.complete_request(pending_req.id, telegram_bot_id=created_bot_user.id)
        if state:
            await state.clear()
        return bot_instance, None
    except DuplicateBotError as exc:
        await req_repo.fail_request(pending_req.id)
        return None, str(exc)
    except Exception as exc:
        await req_repo.fail_request(pending_req.id)
        logger.error("Error provisioning managed bot %s: %s", created_bot_user.id, exc)
        return None, f"Ошибка подключения бота: {exc}"


@manager_router.message(F.managed_bot_created)
async def msg_managed_bot_created(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    registry: Optional[BotRegistry] = None,
) -> None:
    """Handle native Telegram message update when a managed bot is created."""
    created_info = message.managed_bot_created
    if not created_info or not created_info.bot_user:
        return

    created_bot = created_info.bot_user
    bot_instance, error = await _handle_managed_bot_provisioning(
        session=session,
        created_bot_user=created_bot,
        telegram_owner_user_id=message.from_user.id,
        state=state,
        registry=registry,
    )

    if error:
        await message.answer(
            f"❌ <b>Не удалось подключить созданного бота:</b>\n{error}",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer("Главное меню:", reply_markup=main_menu_keyboard())
        return

    text = (
        "🎉 <b>Ваш Telegram-бот успешно создан и подключён!</b>\n\n"
        f"🤖 <b>Имя:</b> {escape(created_bot.first_name)}\n"
        f"🔗 <b>Логин:</b> @{created_bot.username}\n\n"
        "Вебхук настроен, служебные команды и кнопка меню установлены.\n"
        "Теперь вы можете открыть бота и завершить настройку услуг и расписания!"
    )
    await message.answer(text, reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "Управление ботом:",
        reply_markup=managed_bot_success_keyboard(bot_instance.master_id, created_bot.username or ""),
    )


@manager_router.managed_bot()
async def update_managed_bot(
    event: ManagedBotUpdated,
    session: AsyncSession,
    registry: Optional[BotRegistry] = None,
) -> None:
    """Handle ManagedBotUpdated event from Telegram Bot API."""
    if not event.user or not event.bot_user:
        return

    created_bot = event.bot_user
    bot_instance, error = await _handle_managed_bot_provisioning(
        session=session,
        created_bot_user=created_bot,
        telegram_owner_user_id=event.user.id,
        state=None,
        registry=registry,
    )
    if error:
        logger.warning("ManagedBotUpdated provisioning warning: %s", error)
    else:
        logger.info("ManagedBotUpdated: bot %s successfully provisioned for master %s", created_bot.id, bot_instance.master_id)


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

@manager_router.callback_query(F.data.startswith("mgr:bot:web:"))
async def cb_bot_web_booking(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await _get_or_create_user(session, callback.from_user)
    await _answer_bot_callback(callback)
    try:
        parts = callback.data.split(":")
        bot_id, action = int(parts[3]), parts[4]
        if action not in {"view", "on", "off"}:
            raise ValueError
        instance = await BotInstanceRepository(session).get_by_id(bot_id)
        if not instance:
            raise AccessDeniedError("Доступ запрещён")
        await MasterAuthorizationService(session).require_owner(instance.master_id, user.id)
        if action != "view":
            instance = await BotProvisioningService(session).set_web_booking_enabled(bot_id, user.id, action == "on")
        enabled = instance.web_booking_enabled
        text = "🌐 Запись через сайт\n" + ("🟢 Включена" if enabled else "⚪ Выключена")
        if enabled:
            text += "\n" + escape(settings.web_booking_base_url.rstrip("/") + "/book/" + str(instance.public_id))
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Выключить" if enabled else "Включить", callback_data=f"mgr:bot:web:{bot_id}:" + ("off" if enabled else "on"))],
            [InlineKeyboardButton(text="Назад", callback_data=f"mgr:master:{instance.master_id}")]])
        if callback.message:
            await callback.message.answer(text, reply_markup=keyboard)
    except (AccessDeniedError, ProvisioningWebhookError, ValueError, IndexError):
        if callback.message:
            await callback.message.answer("Веб-запись недоступна. Проверьте настройки платформы и права владельца.")


@manager_router.callback_query(F.data.startswith("mgr:bot:miniapp:"))
async def cb_bot_mini_app(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await _get_or_create_user(session, callback.from_user)
    parts = callback.data.split(":")
    try:
        bot_id, action = int(parts[3]), parts[4]
        if action not in {"view", "on", "off"}:
            raise ValueError
        instance = await BotInstanceRepository(session).get_by_id(bot_id)
        if not instance:
            raise AccessDeniedError("Бот недоступен")
        await MasterAuthorizationService(session).require_owner(instance.master_id, user.id)
        await _answer_bot_callback(callback)
        if action != "view":
            instance = await BotProvisioningService(session).set_mini_app_enabled(bot_id, user.id, action == "on")
        from app.bot.handlers.admin.bot_settings import mini_app_card
        text, keyboard = mini_app_card(instance, prefix=f"mgr:bot:miniapp:{bot_id}:", back=f"mgr:master:{instance.master_id}")
        if callback.message:
            await callback.message.answer(text, reply_markup=keyboard)
    except (AccessDeniedError, ProvisioningWebhookError, ValueError, IndexError):
        await _answer_bot_callback(callback, "Настройка доступна владельцу подключённого бота. Повторите позже.", show_alert=True)


@manager_router.callback_query(F.data.startswith("mgr:bot:resync:"))
async def cb_resync_webhook(callback: CallbackQuery, session: AsyncSession) -> None:
    """Owner/admin action for one instance; middleware owns the DB transaction."""
    user = await _get_or_create_user(session, callback.from_user)
    await _answer_bot_callback(callback)
    service = BotProvisioningService(session)
    try:
        await service.resync_webhook(int(callback.data.rsplit(":", 1)[1]), user.id)
        text = "✅ Webhook обновлён. Отправьте /start в клиентский бот для проверки."
    except (AccessDeniedError, ProvisioningWebhookError, ValueError):
        text = f"Не удалось обновить webhook. Проверьте права и состояние бота. Поддержка: {settings.support_tag}"
    if callback.message:
        await callback.message.answer(text, reply_markup=main_menu_keyboard())


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
        await service.retry_provisioning(bot.id, user.id, commit=False)
        await callback.message.edit_text(
            "✅ <b>Вебхук успешно подключён!</b> Можно проверить бота командой /start.",
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
        await service.disable_bot(bot.id, user.id, commit=False)
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
        await _answer_bot_callback(callback, "Ошибка при отключении. Обратитесь в поддержку.", show_alert=True)
        raise
    await _answer_bot_callback(callback)


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
        await service.enable_bot(bot.id, user.id, commit=False)
        updated_master = await master_repo.get_by_id(master_id)
        updated_bot = await bot_repo.get_current_for_master(master_id)
        readiness_service = MasterReadinessService(session)
        is_ready, _ = await readiness_service.check(master_id)
        await callback.message.edit_text(
            "▶️ <b>Бот успешно включён!</b> Вебхук восстановлен.",
            reply_markup=project_card_keyboard(updated_master or master, updated_bot, is_ready),
        )
    except Exception:
        await callback.message.edit_text(
            "❌ <b>Не удалось включить бота.</b> Обратитесь в поддержку.",
            reply_markup=project_card_keyboard(master, bot),
        )
        raise
    await _answer_bot_callback(callback)


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

    if bot.managed_by_platform:
        text = (
            f"♻️ <b>Смена токена для бота @{bot.telegram_username}</b>\n\n"
            "Этот бот управляется платформой ZapisFlow (Telegram Managed Bot).\n"
            "Вы можете обновить токен безопасности автоматически в 1 клик через официальный Bot API, "
            "либо ввести новый токен вручную."
        )
        await callback.message.edit_text(text, reply_markup=managed_bot_rotate_keyboard(master_id))
        await callback.answer()
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


@manager_router.callback_query(F.data.startswith("mgr:bot:mg:rot_confirm:"))
async def cb_rotate_managed_bot_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    registry: Optional[BotRegistry] = None,
) -> None:
    """Automatically rotate token for a platform-managed bot via replaceManagedBotToken."""
    master_id = int(callback.data.split(":")[5])
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
        await service.rotate_managed_bot_token(bot.id, user.id)
        await callback.message.edit_text(
            "✅ <b>Токен управляемого бота успешно обновлён!</b>\n\n"
            "Telegram выпустил новый токен, вебхук переустановлен, данные зашифрованы.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error("Error rotating managed bot token: %s", exc)
        await callback.message.edit_text(
            f"❌ <b>Ошибка обновления токена:</b> {exc}",
            reply_markup=main_menu_keyboard(),
        )
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
        if not can_pay or settings.payment_provider.lower() not in {"manual", "yookassa", "yookassa_web"} or (
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

    auth_svc = MasterAuthorizationService(session)
    role = await auth_svc.get_role(master_id, user.id)
    if role == AdminRole.STAFF:
        await callback.answer("Доступ ограничен. Обратитесь к владельцу проекта.", show_alert=True)
        return

    master = await session.get(Master, master_id)
    if not master or (master.owner_user_id != user.id and role != AdminRole.ADMIN):
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    await _show_subscription_screen(callback, master, session)


@manager_router.callback_query(F.data.startswith("mgr:sub:pay:"))
async def cb_subscription_pay(callback: CallbackQuery, session: AsyncSession, state: FSMContext | None = None) -> None:
    """Initiate subscription payment for a chosen plan."""
    parts = callback.data.split(":")
    if len(parts) != 5 or not parts[3].isdigit() or not parts[4]:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    master_id = int(parts[3])
    plan_code = parts[4]
    user = await _get_or_create_user(session, callback.from_user)

    auth_svc = MasterAuthorizationService(session)
    role = await auth_svc.get_role(master_id, user.id)
    if role == AdminRole.STAFF:
        await callback.answer("Доступ ограничен. Обратитесь к владельцу проекта.", show_alert=True)
        return

    master = await session.get(Master, master_id)
    if not master or master.owner_user_id != user.id:
        await callback.answer("Ошибка: доступ запрещён.", show_alert=True)
        return

    if settings.uses_yookassa:
        if settings.yookassa_fiscal_mode == "merchant_receipt":
            if state is None:
                await _answer_bot_callback(callback, "Откройте раздел подписки заново.", show_alert=True)
                return
            await state.update_data(billing_master_id=master.id, billing_plan_code=plan_code)
            await state.set_state(SubscriptionCheckoutStates.waiting_for_email)
            await callback.message.answer("Введите email для получения кассового чека. Для отмены: /cancel")
            await _answer_bot_callback(callback)
            return
        await _answer_bot_callback(callback)
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
        except (SubscriptionError, YooKassaGatewayError, ValueError) as exc:
            logger.warning("YooKassa checkout unavailable: %s", exc)
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            fallback_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [support_button("💬 Написать в поддержку")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data=f"mgr:sub:{master.id}")],
                ]
            )
            await callback.message.edit_text(
                "💳 <b>Оплата подписки</b>\n\n"
                "Оплата онлайн временно недоступна.\n"
                f"Для активации или продления подписки, пожалуйста, свяжитесь с поддержкой: {settings.support_tag}",
                reply_markup=fallback_kb,
            )
            await _answer_bot_callback(callback)
            return

        if redirect.confirmation_url is None:
            await callback.answer(
                ("Платёж ожидает списания. Проверьте статус позже."
                 if redirect.status == "WAITING_FOR_CAPTURE" else
                 "Этот платёж уже завершён. Обновите статус подписки."), show_alert=True
            )
            return
        amount = f"{order.amount:,.2f}".replace(",", " ").removesuffix(".00")
        text = (
            f"💳 <b>Оплата подписки {escape(order.plan_name)} ({order.period_days} дней)</b>\n\n"
            f"Сумма: <b>{amount} ₽</b>\n\n"
            "Нажмите кнопку ниже для перехода к оплате.\n"
            "После оплаты нажмите «Проверить оплату»."
        )
        await callback.message.edit_text(
            text,
            reply_markup=subscription_checkout_keyboard(
                master.id, redirect.confirmation_url, order.payment_id
            ),
        )
        await _answer_bot_callback(callback)
        return

    if settings.is_production:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        fallback_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [support_button("💬 Написать в поддержку")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data=f"mgr:sub:{master.id}")],
            ]
        )
        await callback.message.edit_text(
            "💳 <b>Оплата подписки</b>\n\n"
            "Автоматическая оплата временно недоступна. "
            f"Пожалуйста, напишите в поддержку {settings.support_tag}, чтобы активировать подписку.",
            reply_markup=fallback_kb,
        )
        await _answer_bot_callback(callback)
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
    await _answer_bot_callback(callback)


@manager_router.message(SubscriptionCheckoutStates.waiting_for_email, F.text)
async def msg_subscription_receipt_email(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Collect fiscal email; price and ownership are resolved again from DB."""
    from app.services.billing.checkout_session import _receipt_email
    try:
        email = _receipt_email(message.text or "")
    except SubscriptionError:
        await message.answer("Укажите корректный email для чека или /cancel.")
        return
    data = await state.get_data()
    user = await _get_or_create_user(session, message.from_user)
    try:
        if not settings.uses_yookassa or settings.yookassa_fiscal_mode != "merchant_receipt":
            raise SubscriptionError("Режим оплаты изменился; откройте подписку заново")
        shop_id, key = settings.yookassa_credentials
        service = YooKassaCheckoutService(async_session_factory, YooKassaClient(shop_id, key))
        order, redirect = await service.open_direct_checkout(
            actor_user_id=user.id, master_id=int(data.get("billing_master_id", 0)),
            plan_code=data.get("billing_plan_code", ""), receipt_email=email,
        )
    except (SubscriptionError, YooKassaGatewayError, ValueError):
        logger.warning("Merchant receipt checkout unavailable")
        await message.answer(f"Не удалось подготовить оплату. Поддержка: {settings.support_tag}")
        return
    await state.clear()
    if redirect.confirmation_url is None:
        await message.answer("Проверьте статус платежа в разделе подписки.")
        return
    await message.answer(
        f"💳 <b>{escape(order.plan_name)}</b>\n{order.amount:.2f} ₽ / {order.period_days} дней",
        reply_markup=subscription_checkout_keyboard(
            int(data["billing_master_id"]), redirect.confirmation_url, order.payment_id
        ),
    )


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

    if payment.provider != "MANUAL":
        await callback.answer("Платёж проверяется только через API провайдера.", show_alert=True)
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
        await bot_svc.delete_bot(bot.id, actor_user_id=user.id, commit=False)
    except Exception as exc:
        logger.exception("Error deleting bot: %s", exc)
        await _answer_bot_callback(callback, "Не удалось удалить бота. Попробуйте позже.", show_alert=True)
        raise

    await _answer_bot_callback(callback, "✅ Бот успешно отключён и удалён из проекта.", show_alert=True)
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


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:u_projects:(\d+)$"))
async def cb_admin_user_projects(callback: CallbackQuery, session: AsyncSession) -> None:
    """Show projects owned by a user."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    user_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    projects = await admin_svc.list_user_projects(user_id)
    text = (
        f"🏢 <b>Проекты пользователя #{user_id}</b> (Всего: {len(projects)})\n\n"
        "Выберите проект для управления:"
    )
    if not projects:
        text += "\n\n<i>У пользователя нет созданных проектов.</i>"
    await callback.message.edit_text(text, reply_markup=admin_user_projects_keyboard(projects, user_id))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:u_bots:(\d+)$"))
async def cb_admin_user_bots(callback: CallbackQuery, session: AsyncSession) -> None:
    """Show bots belonging to a user's projects."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    user_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    bots = await admin_svc.list_user_bots(user_id)
    text = (
        f"🤖 <b>Боты пользователя #{user_id}</b> (Всего: {len(bots)})\n\n"
        "Выберите бота для управления:"
    )
    if not bots:
        text += "\n\n<i>У проектов пользователя нет подключённых ботов.</i>"
    await callback.message.edit_text(text, reply_markup=admin_user_bots_keyboard(bots, user_id))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:u_subs:(\d+)$"))
async def cb_admin_user_subscriptions(callback: CallbackQuery, session: AsyncSession) -> None:
    """Show subscriptions for a user's projects."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    user_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    subs = await admin_svc.list_user_subscriptions(user_id)
    text = (
        f"💳 <b>Подписки пользователя #{user_id}</b> (Всего: {len(subs)})\n\n"
        "Выберите проект для управления подпиской:"
    )
    if not subs:
        text += "\n\n<i>Нет доступных подписок.</i>"
    await callback.message.edit_text(text, reply_markup=admin_user_subscriptions_keyboard(subs, user_id))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:u_payments:(\d+)$"))
async def cb_admin_user_payments(callback: CallbackQuery, session: AsyncSession) -> None:
    """Show payments made by a user."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    user_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    payments = await admin_svc.list_user_payments(user_id)
    text = (
        f"💰 <b>Платежи пользователя #{user_id}</b> (Всего: {len(payments)})\n\n"
        "Выберите платёж для просмотра деталей:"
    )
    if not payments:
        text += "\n\n<i>У пользователя нет платежей.</i>"
    await callback.message.edit_text(text, reply_markup=admin_user_payments_keyboard(payments, user_id))
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


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:project:(\d+)$"))
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


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:project:hard_delete:(\d+)$"))
async def cb_admin_project_hard_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    """Preview impact and confirm project hard deletion."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    master_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    p = await admin_svc.get_project_details(master_id)
    if not p:
        await callback.answer("Проект не найден.", show_alert=True)
        return
    impact = await admin_svc.get_project_impact_summary(master_id)
    text = (
        f"⚠️ <b>ПОЛНОЕ УДАЛЕНИЕ ПРОЕКТА</b>\n\n"
        f"Проект: <b>{escape(p['display_name'])}</b>\n"
        f"Master ID: <code>{master_id}</code>\n"
        f"Владелец: <b>{escape(p['owner']['first_name'] or '')}</b> (@{p['owner']['username'] or '-'})\n\n"
        f"Будет безвозвратно удалено:\n"
        f"• Боты: <b>{impact['bots']}</b>\n"
        f"• Сотрудники: <b>{impact['staff']}</b>\n"
        f"• Услуги: <b>{impact['services']}</b>\n"
        f"• Записи клиентов: <b>{impact['appointments']}</b>\n"
        f"• Клиентские связи: <b>{impact['clients']}</b>\n"
        f"• Платежи клиентов: <b>{impact['payments']}</b>\n"
        f"• Отзывы: <b>{impact['reviews']}</b>\n"
        f"• Портфолио: <b>{impact['portfolio']}</b>\n"
        f"• Рассылки: <b>{impact['broadcasts']}</b>\n"
        f"• Расписание и интервалы: <b>{impact['schedule']}</b>\n\n"
        f"❗️ Аккаунты пользователей (владелец, клиенты, сотрудники) <b>сохраняются</b>.\n"
        f"Операцию <b>нельзя отменить</b>!"
    )
    await callback.message.edit_text(text, reply_markup=admin_project_hard_delete_confirm_keyboard(master_id))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:project:hard_delete_prompt:(\d+)$"))
async def cb_admin_project_hard_delete_prompt(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt admin for typed text confirmation to delete project."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    master_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminProjectStates.waiting_for_delete_confirm)
    await state.update_data(master_id=master_id)

    text = (
        f"⚠️ <b>Подтверждение удаления проекта #{master_id}</b>\n\n"
        f"Для безвозвратного удаления проекта отправьте в чат точно:\n\n"
        f"<code>DELETE PROJECT {master_id}</code>\n\n"
        f"Любой другой текст отменит операцию."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:project:{master_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminProjectStates.waiting_for_delete_confirm)
async def msg_admin_project_hard_delete_confirm(
    message: Message, state: FSMContext, session: AsyncSession, bot_registry: Optional[BotRegistry] = None
) -> None:
    """Execute project hard delete upon typed confirmation."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session, registry=bot_registry)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    await state.clear()
    if not master_id:
        await message.answer("Действие отменено.")
        return

    expected = f"DELETE PROJECT {master_id}"
    if (message.text or "").strip() != expected:
        await message.answer(
            f"❌ Текст не совпал с «<code>{expected}</code>». Удаление проекта отменено.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🏢 К проекту", callback_data=f"mgr:admin:project:{master_id}")]]
            ),
        )
        return

    ok, msg = await admin_svc.hard_delete_project(master_id, actor_user_id=user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏢 К списку проектов", callback_data="mgr:admin:projects")]]
    )
    await message.answer(f"✅ {msg}" if ok else f"❌ {msg}", reply_markup=kb)


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:project:sub:(\d+)$"))
async def cb_admin_project_sub(callback: CallbackQuery, session: AsyncSession) -> None:
    """Project subscription card view."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    master_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    p = await admin_svc.get_project_details(master_id)
    if not p:
        await callback.answer("Проект не найден.", show_alert=True)
        return

    sub_svc = SubscriptionService(session)
    eff = await sub_svc.get_effective_status(master_id)
    is_suspended = p["status"] == "SUSPENDED"

    paid_until_str = eff.paid_until.strftime("%d.%m.%Y") if eff.paid_until else "—"
    trial_ends_str = eff.trial_ends_at.strftime("%d.%m.%Y") if eff.trial_ends_at else "—"

    text = (
        f"💳 <b>Подписка проекта «{escape(p['display_name'])}»</b>\n\n"
        f"Master ID: <code>{master_id}</code>\n"
        f"Владелец: <b>{escape(p['owner']['first_name'] or '')}</b> (@{p['owner']['username'] or '-'})\n\n"
        f"Эффективный статус: <b>{eff.status.value}</b>\n"
        f"Сохранённый статус: <b>{eff.stored_status.value}</b>\n"
        f"Дней осталось: <b>{eff.days_remaining}</b>\n"
        f"Оплачен до: <b>{paid_until_str}</b>\n"
        f"Триал до: <b>{trial_ends_str}</b>\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_subscription_detail_keyboard(master_id, is_suspended))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:sub:extend:(\d+)$"))
async def cb_admin_sub_extend_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    """Preset menu for subscription extension."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    master_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    p = await admin_svc.get_project_details(master_id)
    if not p:
        await callback.answer("Проект не найден.", show_alert=True)
        return

    text = (
        f"➕ <b>Продление подписки: {escape(p['display_name'])}</b>\n\n"
        f"Текущий статус: <b>{p['subscription_status']}</b>\n\n"
        "Выберите срок для продления подписки проекта:"
    )
    await callback.message.edit_text(text, reply_markup=admin_subscription_extend_presets_keyboard(master_id))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:sub:ext_preset:(\d+):(\d+)$"))
async def cb_admin_sub_ext_preset(callback: CallbackQuery, session: AsyncSession) -> None:
    """Handle preset duration click and prompt for reason."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    parts = callback.data.split(":")
    master_id, days = int(parts[4]), int(parts[5])

    master = await MasterRepository(session).get_by_id(master_id)
    if not master:
        await callback.answer("Проект не найден.", show_alert=True)
        return

    now_utc = datetime.now(timezone.utc)
    base_dt = master.paid_until if (master.paid_until and master.paid_until > now_utc) else now_utc
    base_ts = int(base_dt.timestamp())
    new_dt = base_dt + timedelta(days=days)

    cur_str = master.paid_until.strftime("%d.%m.%Y") if master.paid_until else "—"
    new_str = new_dt.strftime("%d.%m.%Y")

    text = (
        f"⚠️ <b>Подтверждение продления подписки</b>\n\n"
        f"Проект: <b>{escape(master.display_name)}</b>\n"
        f"Продление: <b>+{days} дн.</b>\n"
        f"Действует сейчас: <b>{cur_str}</b>\n"
        f"После продления: <b>{new_str}</b>\n\n"
        f"Выберите причину продления:"
    )
    await callback.message.edit_text(text, reply_markup=admin_subscription_extend_reason_keyboard(master_id, days, base_ts))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:sub:ext_custom_d:(\d+)$"))
async def cb_admin_sub_ext_custom_d(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt for custom number of days."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    master_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminSubscriptionStates.waiting_for_custom_days)
    await state.update_data(master_id=master_id)

    text = (
        "✏️ <b>Своё количество дней</b>\n\n"
        "Введите количество дней для продления подписки (целое число от 1 до 3650):"
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:sub:extend:{master_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminSubscriptionStates.waiting_for_custom_days)
async def msg_admin_sub_custom_days(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate custom days and prompt for reason."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    await state.clear()
    if not master_id:
        return

    text_val = (message.text or "").strip()
    if not text_val.isdigit() or int(text_val) <= 0 or int(text_val) > 3650:
        await message.answer("Некорректное число дней. Введите число от 1 до 3650.")
        return

    days = int(text_val)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master:
        await message.answer("Проект не найден.")
        return

    now_utc = datetime.now(timezone.utc)
    base_dt = master.paid_until if (master.paid_until and master.paid_until > now_utc) else now_utc
    base_ts = int(base_dt.timestamp())
    new_dt = base_dt + timedelta(days=days)

    cur_str = master.paid_until.strftime("%d.%m.%Y") if master.paid_until else "—"
    new_str = new_dt.strftime("%d.%m.%Y")

    prompt_text = (
        f"⚠️ <b>Подтверждение продления подписки</b>\n\n"
        f"Проект: <b>{escape(master.display_name)}</b>\n"
        f"Продление: <b>+{days} дн.</b>\n"
        f"Действует сейчас: <b>{cur_str}</b>\n"
        f"После продления: <b>{new_str}</b>\n\n"
        f"Выберите причину продления:"
    )
    await message.answer(
        prompt_text, reply_markup=admin_subscription_extend_reason_keyboard(master_id, days, base_ts)
    )


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:sub:ext_custom_m:(\d+)$"))
async def cb_admin_sub_ext_custom_m(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt for custom number of months."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    master_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminSubscriptionStates.waiting_for_custom_months)
    await state.update_data(master_id=master_id)

    text = (
        "✏️ <b>Своё количество месяцев</b>\n\n"
        "Введите количество месяцев (1 месяц = 30 дней, число от 1 до 120):"
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:sub:extend:{master_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminSubscriptionStates.waiting_for_custom_months)
async def msg_admin_sub_custom_months(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate custom months and prompt for reason."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    await state.clear()
    if not master_id:
        return

    text_val = (message.text or "").strip()
    if not text_val.isdigit() or int(text_val) <= 0 or int(text_val) > 120:
        await message.answer("Некорректное число месяцев. Введите число от 1 до 120.")
        return

    months = int(text_val)
    days = months * 30
    master = await MasterRepository(session).get_by_id(master_id)
    if not master:
        await message.answer("Проект не найден.")
        return

    now_utc = datetime.now(timezone.utc)
    base_dt = master.paid_until if (master.paid_until and master.paid_until > now_utc) else now_utc
    base_ts = int(base_dt.timestamp())
    new_dt = base_dt + timedelta(days=days)

    cur_str = master.paid_until.strftime("%d.%m.%Y") if master.paid_until else "—"
    new_str = new_dt.strftime("%d.%m.%Y")

    prompt_text = (
        f"⚠️ <b>Подтверждение продления подписки</b>\n\n"
        f"Проект: <b>{escape(master.display_name)}</b>\n"
        f"Продление: <b>+{months} мес. ({days} дн.)</b>\n"
        f"Действует сейчас: <b>{cur_str}</b>\n"
        f"После продления: <b>{new_str}</b>\n\n"
        f"Выберите причину продления:"
    )
    await message.answer(
        prompt_text, reply_markup=admin_subscription_extend_reason_keyboard(master_id, days, base_ts)
    )


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:sub:ext_do:(\d+):(\d+):(\d+):(\w+)$"))
async def cb_admin_sub_ext_do(callback: CallbackQuery, session: AsyncSession) -> None:
    """Execute manual subscription extension with idempotency check."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    parts = callback.data.split(":")
    master_id = int(parts[4])
    days = int(parts[5])
    base_ts = int(parts[6])
    reason_code = parts[7]

    reasons_map = {
        "partner": "Партнёрская подписка",
        "compensation": "Компенсация",
        "testing": "Тестовый период",
        "support": "Поддержка",
        "other": "Ручное продление администратором",
    }
    reason = reasons_map.get(reason_code, "Ручное продление")

    master = await MasterRepository(session).get_by_id(master_id)
    if not master:
        await callback.answer("Проект не найден.", show_alert=True)
        return

    # Double-click / retry protection: check if base timestamp shifted by more than 60s
    now_utc = datetime.now(timezone.utc)
    current_base = master.paid_until if (master.paid_until and master.paid_until > now_utc) else now_utc
    if abs(int(current_base.timestamp()) - base_ts) > 60:
        await callback.answer("Подписка уже была обновлена. Откройте карточку заново.", show_alert=True)
        return

    admin_svc = PlatformAdminService(session)
    ok, msg, new_date = await admin_svc.extend_subscription_manually(
        master_id=master_id,
        days=days,
        actor_user_id=user.id,
        reason=reason,
    )
    if not ok:
        await callback.answer(msg, show_alert=True)
        return

    await callback.answer("✅ Подписка успешно продлена!", show_alert=True)
    text = (
        f"✅ <b>Подписка успешно продлена</b>\n\n"
        f"Проект: <b>{escape(master.display_name)}</b>\n"
        f"Добавлено: <b>+{days} дней</b>\n"
        f"Новая дата окончания: <b>{new_date.strftime('%d.%m.%Y')}</b>\n"
        f"Причина: <i>{reason}</i>\n"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:admin:project:sub:{master_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=kb)


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:sub:set_date:(\d+)$"))
async def cb_admin_sub_set_date_prompt(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt for exact expiry date."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    master_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminSubscriptionStates.waiting_for_expiry_date)
    await state.update_data(master_id=master_id)

    text = (
        "📅 <b>Установка даты окончания подписки</b>\n\n"
        "Введите точную дату в формате <b>ДД.ММ.ГГГГ</b> (например, <code>31.12.2026</code>):"
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:project:sub:{master_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminSubscriptionStates.waiting_for_expiry_date)
async def msg_admin_sub_expiry_date(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate date and prompt confirmation."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    await state.clear()
    if not master_id:
        return

    raw_text = (message.text or "").strip()
    try:
        dt = datetime.strptime(raw_text, "%d.%m.%Y").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    except ValueError:
        await message.answer(
            "Некорректный формат даты. Введите дату в виде ДД.ММ.ГГГГ, например 31.12.2026.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:admin:project:sub:{master_id}")]]
            ),
        )
        return

    now_utc = datetime.now(timezone.utc)
    is_past = dt <= now_utc
    date_iso = dt.strftime("%Y-%m-%d")

    master = await MasterRepository(session).get_by_id(master_id)
    cur_str = master.paid_until.strftime("%d.%m.%Y") if master and master.paid_until else "—"

    if is_past:
        prompt_text = (
            f"⚠️ <b>Внимание: указанная дата уже прошла!</b>\n\n"
            f"Проект: <b>{escape(master.display_name if master else '-')}</b>\n"
            f"Текущая дата: <b>{cur_str}</b>\n"
            f"Новая дата: <b>{dt.strftime('%d.%m.%Y')}</b>\n\n"
            f"Подписка будет немедленно переведена в статус <b>EXPIRED</b> (завершена).\n"
            f"Вы уверены, что хотите завершить подписку?"
        )
    else:
        prompt_text = (
            f"📅 <b>Подтверждение изменения даты подписки</b>\n\n"
            f"Проект: <b>{escape(master.display_name if master else '-')}</b>\n"
            f"Текущая дата: <b>{cur_str}</b>\n"
            f"Новая дата: <b>{dt.strftime('%d.%m.%Y')}</b>\n\n"
            f"Установить новую дату окончания подписки?"
        )

    await message.answer(
        prompt_text,
        reply_markup=admin_subscription_set_expiry_confirm_keyboard(master_id, date_iso, is_past),
    )


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:sub:set_date_do:(\d+):([\d-]+)$"))
async def cb_admin_sub_set_date_do(callback: CallbackQuery, session: AsyncSession) -> None:
    """Execute exact subscription expiry date update."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    parts = callback.data.split(":")
    master_id = int(parts[4])
    date_iso = parts[5]

    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return

    admin_svc = PlatformAdminService(session)
    ok, msg, new_date = await admin_svc.set_subscription_expiry_manually(
        master_id=master_id,
        new_expiry_date=dt,
        actor_user_id=user.id,
        reason="Установка даты администратором",
    )
    if not ok:
        await callback.answer(msg, show_alert=True)
        return

    await callback.answer("✅ Дата подписки обновлена!", show_alert=True)
    text = (
        f"✅ <b>Дата подписки успешно установлена</b>\n\n"
        f"Новая дата окончания: <b>{new_date.strftime('%d.%m.%Y')}</b>\n"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:admin:project:sub:{master_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=kb)


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:sub:history:(\d+)$"))
async def cb_admin_sub_history(callback: CallbackQuery, session: AsyncSession) -> None:
    """Show audit history of subscription changes."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    master_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    history = await admin_svc.get_subscription_history(master_id)

    if not history:
        text = "📜 <b>История изменений подписки</b>\n\n<i>Записей не найдено.</i>"
    else:
        text = f"📜 <b>История изменений подписки (#{master_id})</b>:\n\n"
        for h in history:
            date_str = h["created_at"].strftime("%d.%m.%Y %H:%M")
            action = h["action"]
            after = h["payload_after"]
            reason = after.get("reason", "—")
            days = after.get("days")
            days_str = f" (+{days} дн.)" if days else ""
            actor_str = f"Admin ID: {h['actor_user_id']}" if h.get("actor_user_id") else "Система"
            text += f"• <b>{date_str}</b>: {action}{days_str}\n  {actor_str} | Причина: {reason}\n\n"

    await callback.message.edit_text(text, reply_markup=admin_subscription_history_keyboard(master_id))
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


async def _answer_bot_callback(callback: CallbackQuery, text: str = "", *, show_alert: bool = False) -> None:
    """An expired answerCallbackQuery must not roll back a successful state change."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "query is too old" not in message and "query id is invalid" not in message:
            raise


@manager_router.callback_query(F.data.startswith("mgr:admin:bot:toggle:"))
async def cb_admin_bot_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    """Legacy button is read-only: a replay must never invert the current state."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    await _answer_bot_callback(callback, "Кнопка устарела. Откройте карточку бота заново.", show_alert=True)


@manager_router.callback_query(F.data.startswith("mgr:admin:bot:disable:"))
@manager_router.callback_query(F.data.startswith("mgr:admin:bot:enable:"))
async def cb_admin_bot_set_state(
    callback: CallbackQuery, session: AsyncSession, bot_registry: Optional[BotRegistry] = None
) -> None:
    """Apply an explicit desired state within the webhook transaction."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    parts = callback.data.split(":")
    if len(parts) != 5 or parts[3] not in {"enable", "disable"} or not parts[4].isdigit():
        await _answer_bot_callback(callback, "Некорректная кнопка.", show_alert=True)
        return
    desired_action, bot_id = parts[3], int(parts[4])
    bot_svc = BotProvisioningService(session, registry=bot_registry)
    bot_repo = BotInstanceRepository(session)
    bot = await bot_repo.get_by_id(bot_id)
    if not bot:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    master_repo = MasterRepository(session)
    master = await master_repo.get_by_id(bot.master_id)
    actor_id = master.owner_user_id if master else user.id

    if desired_action == "enable":
        await bot_svc.enable_bot(bot.id, actor_user_id=actor_id, commit=False)
    else:
        await bot_svc.disable_bot(bot.id, actor_user_id=actor_id, commit=False)

    admin_svc = PlatformAdminService(session)
    b = await admin_svc.get_bot_details(bot_id)
    if b:
        is_active = b["status"] in {"ACTIVE", "SETUP_REQUIRED"}
        text = (
            f"🤖 <b>Бот #{b['id']}</b>\n\n"
            f"Username: @{escape(b['telegram_username'] or '-')}\n"
            f"Имя: {escape(b['telegram_first_name'] or '-')}\n"
            f"Статус: <b>{b['status']}</b>\n"
            f"Проект: <b>{escape(b['master_name'])}</b>\n"
            f"Версия токена: v{b['token_version']}\n"
            f"Ошибка: {escape(b['last_error'] or 'Нет')}\n"
        )
        await callback.message.edit_text(text, reply_markup=admin_bot_detail_keyboard(bot_id, is_active))
    await _answer_bot_callback(callback)


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
        await bot_svc.delete_bot(bot_id, actor_user_id=user.id, is_platform_admin=True, commit=False)
        await _answer_bot_callback(callback, "Бот успешно удалён из платформы.", show_alert=True)
    except Exception as exc:
        logger.exception("Error deleting bot: %s", exc)
        await _answer_bot_callback(callback, "Не удалось удалить бота.", show_alert=True)
        raise

    # Return to bots list
    admin_svc = PlatformAdminService(session)
    bots, total, total_pages = await admin_svc.list_bots(page=1)
    text = (
        f"🤖 <b>Боты платформы</b> (Всего: {total})\n\n"
        f"Страница 1 из {total_pages}. Выберите бота для просмотра:"
    )
    await callback.message.edit_text(text, reply_markup=admin_bots_keyboard(bots, 1, total_pages))


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:bot:(\d+)$"))
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

    is_active = b["status"] in {"ACTIVE", "SETUP_REQUIRED"}
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


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:bot:hard_delete:(\d+)$"))
async def cb_admin_bot_hard_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    """Preview warning and confirm bot hard deletion."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    bot_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    b = await admin_svc.get_bot_details(bot_id)
    if not b:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    text = (
        f"⚠️ <b>ПОЛНОЕ УДАЛЕНИЕ БОТА #{bot_id}</b>\n\n"
        f"Бот: @{escape(b['telegram_username'] or '-')}\n"
        f"Проект: <b>{escape(b['master_name'])}</b>\n\n"
        f"Вы собираетесь <b>БЕЗВОЗВРАТНО</b> удалить бота из базы данных:\n"
        f"• Будет отозван Webhook в Telegram.\n"
        f"• Будет удалена запись бота и зашифрованный токен.\n"
        f"• Будут очищены связанные сообщения в очереди Telegram Outbox.\n"
        f"• Проект мастера и все данные клиентов <b>сохраняются</b>.\n\n"
        f"Операцию <b>нельзя отменить</b>!"
    )
    await callback.message.edit_text(text, reply_markup=admin_bot_hard_delete_confirm_keyboard(bot_id))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:bot:hard_delete_prompt:(\d+)$"))
async def cb_admin_bot_hard_delete_prompt(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt admin for typed text confirmation to delete bot."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    bot_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminBotStates.waiting_for_delete_confirm)
    await state.update_data(bot_id=bot_id)

    text = (
        f"⚠️ <b>Подтверждение удаления бота #{bot_id}</b>\n\n"
        f"Для безвозвратного удаления бота отправьте в чат точно:\n\n"
        f"<code>DELETE BOT {bot_id}</code>\n\n"
        f"Любой другой текст отменит операцию."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:bot:{bot_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminBotStates.waiting_for_delete_confirm)
async def msg_admin_bot_hard_delete_confirm(
    message: Message, state: FSMContext, session: AsyncSession, bot_registry: Optional[BotRegistry] = None
) -> None:
    """Execute bot hard delete upon typed confirmation."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session, registry=bot_registry)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    data = await state.get_data()
    bot_id = data.get("bot_id")
    await state.clear()
    if not bot_id:
        await message.answer("Действие отменено.")
        return

    expected = f"DELETE BOT {bot_id}"
    if (message.text or "").strip() != expected:
        await message.answer(
            f"❌ Текст не совпал с «<code>{expected}</code>». Удаление бота отменено.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🤖 К боту", callback_data=f"mgr:admin:bot:{bot_id}")]]
            ),
        )
        return

    ok, msg = await admin_svc.hard_delete_bot(bot_id, actor_user_id=user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🤖 К списку ботов", callback_data="mgr:admin:bots")]]
    )
    await message.answer(f"✅ {msg}" if ok else f"❌ {msg}", reply_markup=kb)


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

    payment_id_text = callback.data.rsplit(":", 1)[-1]
    if not payment_id_text.isdecimal():
        await callback.answer("Некорректный запрос.", show_alert=True)
        return
    payment_id = int(payment_id_text)
    payment = await session.get(SubscriptionPayment, payment_id)
    if payment is None or payment.provider != "YOOKASSA":
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    master = await session.get(Master, payment.master_id)
    if master is None:
        await callback.answer("Проект платежа не найден.", show_alert=True)
        return

    try:
        shop_id, secret_key = settings.yookassa_credentials
        checkout_service = YooKassaCheckoutService(
            async_session_factory, YooKassaClient(shop_id, secret_key)
        )
        result = await checkout_service.check_payment(
            payment_id=payment_id, actor_user_id=master.owner_user_id
        )
    except (SubscriptionError, YooKassaGatewayError, ValueError):
        logger.warning("Platform payment reconciliation failed for payment_id=%s", payment_id)
        await callback.answer("Не удалось проверить платёж. Повторите позже или обратитесь в поддержку.", show_alert=True)
        return

    await callback.answer(f"Статус платежа: {result.status}", show_alert=True)

    # Refresh details
    session.expire(payment)
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
            f"ID в YooKassa: <code>{escape(p['provider_payment_id'])}</code>\n"
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
        f"ID в YooKassa: <code>{escape(p['provider_payment_id'])}</code>\n"
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


def _format_plan_card(p: SubscriptionPlan) -> str:
    feat_text = ""
    if p.features:
        feat_items = []
        labels = {
            "max_bots": "Боты",
            "max_staff": "Сотрудники",
            "custom_branding": "Брендинг",
            "broadcasts": "Рассылки",
            "analytics": "Аналитика",
            "priority_support": "Поддержка",
        }
        for k, v in p.features.items():
            label = labels.get(k, k)
            feat_items.append(f"{label}: {v}")
        if feat_items:
            feat_text = "\n\n🧩 <b>Опции:</b>\n• " + "\n• ".join(feat_items)

    return (
        f"📦 <b>Тариф «{escape(p.name)}»</b>\n\n"
        f"Код (неизменяемый): <code>{p.code}</code>\n"
        f"Цена: <b>{p.price_rub} {p.currency}</b>\n"
        f"Период: <b>{p.duration_days} дней</b>\n"
        f"Статус: <b>{'🟢 Активен' if p.is_active else '🔴 Неактивен'}</b>\n"
        f"Порядок: {p.sort_order}\n"
        f"Описание: {escape(p.description or 'Нет описания')}"
        f"{feat_text}"
    )


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:(\d+)$"))
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

    await callback.message.edit_text(_format_plan_card(p), reply_markup=admin_plan_detail_keyboard(p.id, p.is_active))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:name:(\d+)$"))
async def cb_admin_plan_name_prompt(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt for new plan name."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    plan_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminPlanStates.waiting_for_name)
    await state.update_data(plan_id=plan_id)

    text = "✏️ <b>Изменение названия тарифа</b>\n\nВведите новое название тарифа (до 64 символов):"
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:plan:{plan_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminPlanStates.waiting_for_name)
async def msg_admin_plan_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Handle new plan name input."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        return

    data = await state.get_data()
    plan_id = data.get("plan_id")
    await state.clear()
    if not plan_id:
        return

    name = (message.text or "").strip()
    if not name or len(name) > 64:
        await message.answer("Некорректное название. Длина должна быть от 1 до 64 символов.")
        return

    ok, msg, p = await admin_svc.update_plan(plan_id, actor_user_id=user.id, name=name)
    if not ok or not p:
        await message.answer(f"❌ {msg}")
        return

    await message.answer(
        f"✅ Название тарифа успешно обновлено!\n\n" + _format_plan_card(p),
        reply_markup=admin_plan_detail_keyboard(p.id, p.is_active),
    )


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:price:(\d+)$"))
async def cb_admin_plan_price_prompt(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt for new plan price."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    plan_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminPlanStates.waiting_for_price)
    await state.update_data(plan_id=plan_id)

    text = (
        "💰 <b>Изменение цены тарифа</b>\n\n"
        "Введите новую цену тарифа в рублях (например: <code>990</code> или <code>1490.50</code>):"
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:plan:{plan_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminPlanStates.waiting_for_price)
async def msg_admin_plan_price(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate price and ask confirmation."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        return

    data = await state.get_data()
    plan_id = data.get("plan_id")
    await state.clear()
    if not plan_id:
        return

    raw_val = (message.text or "").strip().replace(",", ".")
    try:
        dec_price = Decimal(raw_val).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer("Некорректный формат цены. Введите положительное число.")
        return

    if dec_price <= 0 or dec_price > Decimal("1000000.00"):
        await message.answer("Цена тарифа должна быть больше 0 и не более 1 000 000 ₽.")
        return

    p = await admin_svc.get_plan_details(plan_id)
    if not p:
        await message.answer("Тариф не найден.")
        return

    text = (
        f"⚠️ <b>Подтверждение изменения цены</b>\n\n"
        f"Тариф: <b>{escape(p.name)}</b>\n"
        f"Текущая цена: <b>{p.price_rub} {p.currency}</b>\n"
        f"Новая цена: <b>{dec_price} {p.currency}</b>\n\n"
        f"Вы уверены, что хотите обновить цену тарифа?"
    )
    await message.answer(text, reply_markup=admin_plan_price_confirm_keyboard(plan_id, str(dec_price)))


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:price_confirm:(\d+):([\d.]+)$"))
async def cb_admin_plan_price_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    """Execute confirmed price update."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[4])
    price_str = parts[5]

    try:
        dec_price = Decimal(price_str)
    except (InvalidOperation, ValueError):
        await callback.answer("Некорректная цена.", show_alert=True)
        return

    admin_svc = PlatformAdminService(session)
    ok, msg, p = await admin_svc.update_plan(plan_id, actor_user_id=user.id, price=dec_price)
    if not ok or not p:
        await callback.answer(msg, show_alert=True)
        return

    await callback.answer("✅ Цена успешно обновлена!", show_alert=True)
    await callback.message.edit_text(_format_plan_card(p), reply_markup=admin_plan_detail_keyboard(p.id, p.is_active))


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:dur:(\d+)$"))
async def cb_admin_plan_dur_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    """Show plan duration presets."""
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
        f"📅 <b>Срок действия тарифа «{escape(p.name)}»</b>\n\n"
        f"Текущий срок: <b>{p.duration_days} дней</b>\n\n"
        "Выберите готовый пресет или введите своё количество дней:"
    )
    await callback.message.edit_text(text, reply_markup=admin_plan_duration_presets_keyboard(plan_id))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:dur_set:(\d+):(\d+)$"))
async def cb_admin_plan_dur_set(callback: CallbackQuery, session: AsyncSession) -> None:
    """Apply preset duration to plan."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    parts = callback.data.split(":")
    plan_id = int(parts[4])
    days = int(parts[5])

    admin_svc = PlatformAdminService(session)
    ok, msg, p = await admin_svc.update_plan(plan_id, actor_user_id=user.id, period_days=days)
    if not ok or not p:
        await callback.answer(msg, show_alert=True)
        return

    await callback.answer(f"✅ Срок тарифа изменён на {days} дн.!", show_alert=True)
    await callback.message.edit_text(_format_plan_card(p), reply_markup=admin_plan_detail_keyboard(p.id, p.is_active))


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:dur_custom:(\d+)$"))
async def cb_admin_plan_dur_custom(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt for custom duration in days."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    plan_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminPlanStates.waiting_for_custom_duration)
    await state.update_data(plan_id=plan_id)

    text = "✏️ <b>Свой срок действия тарифа</b>\n\nВведите количество дней действия тарифа (целое число от 1 до 3650):"
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:plan:dur:{plan_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminPlanStates.waiting_for_custom_duration)
async def msg_admin_plan_custom_dur(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate custom duration input."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        return

    data = await state.get_data()
    plan_id = data.get("plan_id")
    await state.clear()
    if not plan_id:
        return

    val = (message.text or "").strip()
    if not val.isdigit() or int(val) <= 0 or int(val) > 3650:
        await message.answer("Некорректное число дней. Введите целое число от 1 до 3650.")
        return

    days = int(val)
    ok, msg, p = await admin_svc.update_plan(plan_id, actor_user_id=user.id, period_days=days)
    if not ok or not p:
        await message.answer(f"❌ {msg}")
        return

    await message.answer(
        f"✅ Срок тарифа установлен на {days} дн.!\n\n" + _format_plan_card(p),
        reply_markup=admin_plan_detail_keyboard(p.id, p.is_active),
    )


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:sort:(\d+)$"))
async def cb_admin_plan_sort_prompt(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt for plan sort order."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return
    plan_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminPlanStates.waiting_for_sort_order)
    await state.update_data(plan_id=plan_id)

    text = "🔢 <b>Порядок сортировки тарифа</b>\n\nВведите целое число для сортировки (например: <code>10</code>):"
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:plan:{plan_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb)
    await callback.answer()


@manager_router.message(AdminPlanStates.waiting_for_sort_order)
async def msg_admin_plan_sort(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate and set plan sort order."""
    user = await _get_or_create_user(session, message.from_user)
    admin_svc = PlatformAdminService(session)
    if not await admin_svc.is_platform_admin(telegram_id=message.from_user.id, user_id=user.id):
        await state.clear()
        return

    data = await state.get_data()
    plan_id = data.get("plan_id")
    await state.clear()
    if not plan_id:
        return

    val = (message.text or "").strip()
    if not val.lstrip("-").isdigit():
        await message.answer("Некорректное значение. Введите целое число.")
        return

    sort_order = int(val)
    ok, msg, p = await admin_svc.update_plan(plan_id, actor_user_id=user.id, sort_order=sort_order)
    if not ok or not p:
        await message.answer(f"❌ {msg}")
        return

    await message.answer(
        f"✅ Порядок сортировки установлен: {sort_order}\n\n" + _format_plan_card(p),
        reply_markup=admin_plan_detail_keyboard(p.id, p.is_active),
    )


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:feat:(\d+)$"))
async def cb_admin_plan_feat_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    """Show features toggle keyboard for plan."""
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
        f"🧩 <b>Настройка опций (Features) тарифа «{escape(p.name)}»</b>\n\n"
        "Нажмите на строку для переключения опции:"
    )
    await callback.message.edit_text(text, reply_markup=admin_plan_features_keyboard(plan_id, p.features or {}))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:plan:feat_toggle:(\d+):(\w+)$"))
async def cb_admin_plan_feat_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    """Toggle a feature option for a plan."""
    is_admin, user = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[4])
    feature_key = parts[5]

    admin_svc = PlatformAdminService(session)
    p = await admin_svc.get_plan_details(plan_id)
    if not p:
        await callback.answer("Тариф не найден.", show_alert=True)
        return

    features = dict(p.features or {})
    if feature_key == "max_bots":
        current = features.get("max_bots")
        cycle = [None, 1, 3, 5]
        try:
            idx = cycle.index(current)
            new_val = cycle[(idx + 1) % len(cycle)]
        except ValueError:
            new_val = 1
        if new_val is None:
            features.pop("max_bots", None)
        else:
            features["max_bots"] = new_val
    elif feature_key == "max_staff":
        current = features.get("max_staff")
        cycle = [None, 1, 3, 5, 10]
        try:
            idx = cycle.index(current)
            new_val = cycle[(idx + 1) % len(cycle)]
        except ValueError:
            new_val = 1
        if new_val is None:
            features.pop("max_staff", None)
        else:
            features["max_staff"] = new_val
    else:
        # Boolean toggle
        current_bool = bool(features.get(feature_key, False))
        features[feature_key] = not current_bool

    ok, msg, updated_p = await admin_svc.update_plan(plan_id, actor_user_id=user.id, features=features)
    if not ok or not updated_p:
        await callback.answer(msg, show_alert=True)
        return

    await callback.answer("Опция обновлена.")
    text = (
        f"🧩 <b>Настройка опций (Features) тарифа «{escape(updated_p.name)}»</b>\n\n"
        "Нажмите на строку для переключения опции:"
    )
    await callback.message.edit_text(text, reply_markup=admin_plan_features_keyboard(plan_id, updated_p.features or {}))


@manager_router.callback_query(F.data == "mgr:admin:audit")
@manager_router.callback_query(F.data.regexp(r"^mgr:admin:audit:p:(\d+)$"))
async def cb_admin_audit_logs(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin paginated audit log listing."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    page = 1
    if callback.data.startswith("mgr:admin:audit:p:"):
        try:
            page = max(1, int(callback.data.split(":")[-1]))
        except ValueError:
            page = 1

    admin_svc = PlatformAdminService(session)
    logs, total, total_pages = await admin_svc.list_audit_logs(page=page)

    text = (
        f"📝 <b>Журнал действий платформы (Audit Log)</b> (Всего: {total})\n\n"
        f"Страница {page} из {total_pages}. Выберите запись для просмотра:"
    )
    await callback.message.edit_text(text, reply_markup=admin_audit_logs_keyboard(logs, page, total_pages))
    await callback.answer()


@manager_router.callback_query(F.data.regexp(r"^mgr:admin:audit:(\d+)$"))
async def cb_admin_audit_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    """Platform Admin audit log event detail view."""
    is_admin, _ = await _ensure_platform_admin(session, callback)
    if not is_admin:
        return

    log_id = int(callback.data.split(":")[-1])
    admin_svc = PlatformAdminService(session)
    entry = await admin_svc.get_audit_log_details(log_id)
    if not entry:
        await callback.answer("Запись аудита не найдена.", show_alert=True)
        return

    actor_str = f"#{entry['actor_user_id']}"
    if entry.get("actor_username"):
        actor_str += f" (@{entry['actor_username']})"

    date_str = entry["created_at"].strftime("%d.%m.%Y %H:%M:%S") if entry.get("created_at") else "-"

    before_str = json.dumps(entry.get("payload_before") or {}, ensure_ascii=False, indent=2)
    after_str = json.dumps(entry.get("payload_after") or {}, ensure_ascii=False, indent=2)

    text = (
        f"📝 <b>Запись аудита #{entry['id']}</b>\n\n"
        f"⚡️ Действие: <code>{entry['action']}</code>\n"
        f"👤 Инициатор: <b>{actor_str}</b>\n"
        f"🏢 Проект ID: <code>{entry.get('master_id') or '—'}</code>\n"
        f"🎯 Сущность: <b>{entry.get('entity_type') or '—'} #{entry.get('entity_id') or '—'}</b>\n"
        f"🕒 Время (UTC): {date_str}\n\n"
        f"<b>До изменения:</b>\n<code>{escape(before_str[:500])}</code>\n\n"
        f"<b>После изменения:</b>\n<code>{escape(after_str[:500])}</code>\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_audit_log_detail_keyboard())
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


@manager_router.callback_query(F.data.startswith("mgr:sch:workdate:"))
async def cb_manager_schedule_workdate(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt to add a one-off working date overriding the weekly schedule."""
    master_id = int(callback.data.split(":")[3])
    await state.update_data(sch_master_id=master_id)
    await state.set_state(ManagerScheduleStates.waiting_for_work_date)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:schedule:{master_id}")]]
    )
    await callback.message.edit_text(
        "➕ <b>Отдельный рабочий день</b>\n\n"
        "Введите дату в формате ДД.ММ.ГГГГ (например: <code>10.10.2026</code>). "
        "Этот день будет рабочим независимо от обычного графика.",
        reply_markup=cancel_kb,
    )
    await callback.answer()


@manager_router.message(ManagerScheduleStates.waiting_for_work_date)
async def msg_manager_schedule_workdate(message: Message, state: FSMContext) -> None:
    from datetime import date as dt_date, datetime as dt_datetime

    raw = (message.text or "").strip()
    try:
        target_date = dt_datetime.strptime(raw, "%d.%m.%Y").date()
        if target_date < dt_date.today():
            raise ValueError
    except ValueError:
        await message.answer("Введите сегодняшнюю или будущую дату в формате <code>ДД.ММ.ГГГГ</code>:")
        return
    await state.update_data(sch_work_date=target_date.isoformat())
    await state.set_state(ManagerScheduleStates.waiting_for_work_hours)
    await message.answer(
        "Введите рабочий интервал, например <code>10:00-18:00</code>. "
        "Интервал должен заканчиваться позже начала."
    )


@manager_router.message(ManagerScheduleStates.waiting_for_work_hours)
async def msg_manager_schedule_work_hours(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    from datetime import date as dt_date, datetime as dt_datetime, time as dt_time

    raw = (message.text or "").strip()
    try:
        start_raw, end_raw = raw.split("-", 1)
        start_time = dt_time.fromisoformat(start_raw.strip())
        end_time = dt_time.fromisoformat(end_raw.strip())
        if end_time <= start_time:
            raise ValueError
    except ValueError:
        await message.answer("Введите интервал в формате <code>10:00-18:00</code>:")
        return

    data = await state.get_data()
    master_id = data.get("sch_master_id")
    date_raw = data.get("sch_work_date")
    if not master_id or not date_raw:
        await state.clear()
        return
    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        await message.answer("Ошибка: доступ запрещён.")
        return

    target_date = dt_date.fromisoformat(date_raw)
    await ScheduleRepository(session).set_date_exception(
        target_date=target_date,
        is_day_off=False,
        work_start=start_time,
        work_end=end_time,
        comment="Отдельный рабочий день",
        master_id=master_id,
    )
    await state.clear()
    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    await message.answer(
        f"✅ <b>{target_date.strftime('%d.%m.%Y')}</b> добавлен как рабочий день: "
        f"<b>{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}</b>.",
        reply_markup=manager_schedule_menu_keyboard(master_id, settings_obj),
    )


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

    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        await message.answer("Ошибка: доступ запрещён.")
        return

    await MasterSettingsRepository(session).update_settings(master_id, min_advance_hours=hours)
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

    user = await _get_or_create_user(session, message.from_user)
    master = await MasterRepository(session).get_by_id(master_id)
    if not master or master.owner_user_id != user.id:
        await state.clear()
        await message.answer("Ошибка: доступ запрещён.")
        return

    await MasterSettingsRepository(session).update_settings(master_id, booking_horizon_days=days)
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

    await MasterSettingsRepository(session).update_settings(master_id, about_text=desc)
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
    bank = settings_obj.bank_name if settings_obj else None
    card = settings_obj.bank_card_number if settings_obj else None
    recipient = settings_obj.bank_recipient_name if settings_obj else None
    requisites_ready = all(value and value.strip() for value in (bank, card, recipient))

    text = (
        "💳 <b>Правила предоплаты и удержания</b>\n\n"
        f"• <b>Время на оплату чека:</b> {hold_m} мин. (после этого бронь аннулируется)\n"
        f"• <b>Бесплатная отмена за:</b> {cancel_h} ч. до визита\n\n"
        f"• <b>Реквизиты:</b> {'настроены' if requisites_ready else 'не настроены'}\n"
        "<i>Размер предоплаты (процент или фиксированная сумма) настраивается индивидуально в каждой услуге в разделе «Услуги и прайс».</i>"
    )
    await callback.message.edit_text(text, reply_markup=manager_prepayment_settings_keyboard(master_id, settings_obj))
    await callback.answer()


@manager_router.callback_query(F.data.startswith("mgr:prepay:hold:"))
@manager_router.callback_query(F.data.startswith("mgr:prepay:cancel:"))
@manager_router.callback_query(F.data.startswith("mgr:prepay:edit:"))
async def cb_manager_prepay_edit(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Prompt for one tenant-scoped prepayment rule or payment requisite."""
    parts = (callback.data or "").split(":")
    action = parts[2]
    if action == "edit":
        if len(parts) != 5:
            await callback.answer("Настройка недоступна.", show_alert=True)
            return
        field = parts[3]
        master_id = int(parts[4])
        allowed = {
            "bank_name": "название банка",
            "bank_card_number": "номер карты для предоплаты",
            "bank_recipient_name": "имя получателя перевода",
        }
        if field not in allowed:
            await callback.answer("Настройка недоступна.", show_alert=True)
            return
        prompt = f"Введите {allowed[field]}. Отправьте «-», чтобы очистить поле:"
        data_field = field
    else:
        if len(parts) != 4:
            await callback.answer("Настройка недоступна.", show_alert=True)
            return
        master_id = int(parts[3])
        if action == "hold":
            data_field = "hold_duration_minutes"
            prompt = "Введите время удержания слота в минутах (от 5 до 1440):"
        elif action == "cancel":
            data_field = "cancel_policy_hours"
            prompt = "Введите срок бесплатной отмены в часах (от 0 до 720):"
        else:
            await callback.answer("Настройка недоступна.", show_alert=True)
            return

    user = await _get_or_create_user(session, callback.from_user)
    if not await MasterAuthorizationService(session).is_admin(master_id, user.id):
        await callback.answer("У вас нет прав администратора.", show_alert=True)
        return

    await state.set_state(ManagerSettingsStates.waiting_for_prepay_value)
    await state.update_data(prepay_master_id=master_id, prepay_field=data_field)
    await callback.message.edit_text(
        prompt,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"mgr:set:prepay:{master_id}",
            )]]
        ),
    )
    await callback.answer()


@manager_router.message(ManagerSettingsStates.waiting_for_prepay_value, F.text)
async def msg_manager_prepay_save(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Persist a single prepayment setting for the verified project owner/admin."""
    data = await state.get_data()
    master_id = data.get("prepay_master_id")
    field = data.get("prepay_field")
    if not isinstance(master_id, int) or field not in {
        "bank_name", "bank_card_number", "bank_recipient_name",
        "hold_duration_minutes", "cancel_policy_hours",
    }:
        await state.clear()
        await message.answer("Сессия настройки истекла. Откройте раздел предоплаты заново.")
        return

    user = await _get_or_create_user(session, message.from_user)
    if not await MasterAuthorizationService(session).is_admin(master_id, user.id):
        await state.clear()
        await message.answer("У вас нет прав администратора для этого проекта.")
        return

    raw = (message.text or "").strip()
    if field in {"hold_duration_minutes", "cancel_policy_hours"}:
        try:
            value = int(raw)
        except ValueError:
            await message.answer("Введите целое число в указанном диапазоне.")
            return
        lower, upper = (5, 1440) if field == "hold_duration_minutes" else (0, 720)
        if not lower <= value <= upper:
            await message.answer(f"Введите число от {lower} до {upper}.")
            return
    else:
        value = None if raw == "-" else raw
        limit = 64 if field == "bank_card_number" else 128
        if value is not None and (not value or len(value) > limit):
            await message.answer(f"Значение должно содержать от 1 до {limit} символов или «-» для очистки.")
            return

    await MasterSettingsRepository(session).update_settings(master_id, **{field: value})
    await state.clear()
    settings_obj = await MasterSettingsRepository(session).get_by_master_id(master_id)
    await message.answer(
        "✅ Настройка сохранена.\n\n"
        "Если услуга требует предоплату, новый клиентский hold будет доступен только после заполнения банка, карты и получателя.",
        reply_markup=manager_prepayment_settings_keyboard(master_id, settings_obj),
    )


# ---------------------------------------------------------------------------
# MULTI-STAFF MANAGEMENT (PHASE 5)
# ---------------------------------------------------------------------------

@manager_router.callback_query(F.data.startswith("mgr:staff:"))
async def cb_manager_staff_router(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Route staff management callbacks."""
    parts = callback.data.split(":")
    action = parts[2]
    user = await _get_or_create_user(session, callback.from_user)
    auth_svc = MasterAuthorizationService(session)

    # 1. Staff List: mgr:staff:<master_id>
    if len(parts) == 3:
        master_id = int(action)
        if not await auth_svc.is_admin(master_id, user.id):
            await callback.answer("У вас нет прав администратора.", show_alert=True)
            return
        master = await MasterRepository(session).get_by_id(master_id)
        staff_repo = StaffRepository(session)
        staff_members = await staff_repo.list_all(master_id)
        text = (
            f"👥 <b>Сотрудники студии «{escape(master.display_name)}»</b>\n\n"
            f"Всего мастеров в проекте: <b>{len(staff_members)}</b>\n\n"
            "Выберите сотрудника для настройки услуг, графика и персональной ссылки-приглашения:"
        )
        await callback.message.edit_text(text, reply_markup=staff_list_keyboard(master_id, staff_members))
        await callback.answer()
        return

    # 2. Add Staff: mgr:staff:add:<master_id>
    if action == "add":
        master_id = int(parts[3])
        if not await auth_svc.is_admin(master_id, user.id):
            await callback.answer("У вас нет прав администратора.", show_alert=True)
            return
        entitlement_svc = SubscriptionEntitlementService(session)
        can_add, reason = await entitlement_svc.can_create_staff(master_id)
        if not can_add:
            await callback.answer(reason or "Добавление сотрудников недоступно.", show_alert=True)
            return
        await state.set_state(ManagerStaffStates.waiting_for_name)
        await state.update_data(staff_master_id=master_id)
        text = (
            "➕ <b>Добавление нового сотрудника</b>\n\n"
            "Введите имя мастера (например: <i>Анна</i> или <i>Дмитрий Смирнов</i>):"
        )
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:staff:{master_id}")]]
        )
        await callback.message.edit_text(text, reply_markup=cancel_kb)
        await callback.answer()
        return

    # 3. Staff Card: mgr:staff:card:<master_id>:<staff_id>
    if action == "card":
        master_id = int(parts[3])
        staff_id = int(parts[4])
        if not await auth_svc.is_admin(master_id, user.id):
            await callback.answer("У вас нет прав администратора.", show_alert=True)
            return
        staff_repo = StaffRepository(session)
        staff = await staff_repo.get_by_id(staff_id, master_id)
        if not staff:
            await callback.answer("Сотрудник не найден.", show_alert=True)
            return
        services = await staff_repo.list_services_for_staff(staff_id, master_id)
        status_text = "🟢 Принимает записи" if staff.is_active else "⚪️ Отключён"
        user_text = f"@{staff.user.username}" if staff.user and staff.user.username else ("Привязан" if staff.user_id else "Не привязан к Telegram")
        spec_text = staff.specialization or "Не указана"

        text = (
            f"👤 <b>Специалист: {escape(staff.display_name)}</b>\n\n"
            f"✂️ Специализация: <b>{escape(spec_text)}</b>\n"
            f"📊 Статус: <b>{status_text}</b>\n"
            f"💅 Привязано услуг: <b>{len(services)}</b>\n"
            f"✈️ Telegram: <b>{escape(user_text)}</b>\n"
        )
        await callback.message.edit_text(text, reply_markup=staff_card_keyboard(master_id, staff))
        await callback.answer()
        return

    # 4. Toggle Active: mgr:staff:toggle:<master_id>:<staff_id>
    if action == "toggle":
        master_id = int(parts[3])
        staff_id = int(parts[4])
        if not await auth_svc.is_admin(master_id, user.id):
            await callback.answer("У вас нет прав администратора.", show_alert=True)
            return
        staff_repo = StaffRepository(session)
        staff = await staff_repo.get_by_id(staff_id, master_id)
        if staff:
            staff.is_active = not staff.is_active
            await session.flush()
            await callback.answer(f"Статус сотрудника: {'Активирован' if staff.is_active else 'Деактивирован'}")
        # Re-render card
        updated_staff = await staff_repo.get_by_id(staff_id, master_id)
        services = await staff_repo.list_services_for_staff(staff_id, master_id)
        status_text = "🟢 Принимает записи" if updated_staff.is_active else "⚪️ Отключён"
        user_text = f"@{updated_staff.user.username}" if updated_staff.user and updated_staff.user.username else ("Привязан" if updated_staff.user_id else "Не привязан к Telegram")
        spec_text = updated_staff.specialization or "Не указана"

        text = (
            f"👤 <b>Специалист: {escape(updated_staff.display_name)}</b>\n\n"
            f"✂️ Специализация: <b>{escape(spec_text)}</b>\n"
            f"📊 Статус: <b>{status_text}</b>\n"
            f"💅 Привязано услуг: <b>{len(services)}</b>\n"
            f"✈️ Telegram: <b>{escape(user_text)}</b>\n"
        )
        await callback.message.edit_text(text, reply_markup=staff_card_keyboard(master_id, updated_staff))
        return

    # 5. Staff Services: mgr:staff:services:<master_id>:<staff_id>
    if action == "services":
        master_id = int(parts[3])
        staff_id = int(parts[4])
        if not await auth_svc.is_admin(master_id, user.id):
            await callback.answer("У вас нет прав администратора.", show_alert=True)
            return
        staff_repo = StaffRepository(session)
        staff = await staff_repo.get_by_id(staff_id, master_id)
        all_services = await ServiceRepository(session).list_active(master_id)
        assigned_ids = await staff_repo.list_services_for_staff(staff_id, master_id)
        text = (
            f"💅 <b>Услуги специалиста: {escape(staff.display_name)}</b>\n\n"
            "Отметьте услуги, которые может выполнять данный мастер:\n"
            "<i>(Зелёная галочка ✅ означает, что услуга доступна для записи)</i>"
        )
        await callback.message.edit_text(
            text, reply_markup=staff_services_keyboard(master_id, staff_id, all_services, assigned_ids)
        )
        await callback.answer()
        return

    # 6. Service Toggle: mgr:staff:svc_toggle:<master_id>:<staff_id>:<svc_id>
    if action == "svc_toggle":
        master_id = int(parts[3])
        staff_id = int(parts[4])
        svc_id = int(parts[5])
        if not await auth_svc.is_admin(master_id, user.id):
            await callback.answer("У вас нет прав администратора.", show_alert=True)
            return
        staff_repo = StaffRepository(session)
        assigned = set(await staff_repo.list_services_for_staff(staff_id, master_id))
        if svc_id in assigned:
            assigned.remove(svc_id)
        else:
            assigned.add(svc_id)
        await staff_repo.set_staff_services(staff_id, master_id, list(assigned))
        all_services = await ServiceRepository(session).list_active(master_id)
        await callback.message.edit_reply_markup(
            reply_markup=staff_services_keyboard(master_id, staff_id, all_services, list(assigned))
        )
        await callback.answer()
        return

    # 7. Generate Invite: mgr:staff:invite:<master_id>:<staff_id>
    if action == "invite":
        master_id = int(parts[3])
        staff_id = int(parts[4])
        if not await auth_svc.is_admin(master_id, user.id):
            await callback.answer("У вас нет прав администратора.", show_alert=True)
            return
        staff_repo = StaffRepository(session)
        staff = await staff_repo.get_by_id(staff_id, master_id)
        token_plain = await staff_repo.create_invite_token(staff_id, master_id)

        bot_username = "ZapisFlow_Bot"
        try:
            bot_user = await callback.bot.get_me()
            bot_username = bot_user.username or bot_username
        except Exception:
            pass

        invite_link = f"https://t.me/{bot_username}?start=inv_{token_plain}"
        text = (
            f"🔗 <b>Приглашение для специалиста {escape(staff.display_name)}</b>\n\n"
            f"Передайте эту персональную ссылку сотруднику:\n"
            f"<code>{invite_link}</code>\n\n"
            f"⏳ Ссылка действует <b>48 часов</b> и является одноразовой.\n"
            f"После перехода мастер получит доступ к личному расписанию и клиентам."
        )
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ К карточке специалиста", callback_data=f"mgr:staff:card:{master_id}:{staff_id}")]]
        )
        await callback.message.edit_text(text, reply_markup=back_kb)
        await callback.answer()
        return

    # 8. Edit Field: mgr:staff:edit:<field>:<master_id>:<staff_id>
    if action == "edit":
        field = parts[3]
        master_id = int(parts[4])
        staff_id = int(parts[5])
        if not await auth_svc.is_admin(master_id, user.id):
            await callback.answer("У вас нет прав администратора.", show_alert=True)
            return
        await state.set_state(ManagerStaffStates.waiting_for_edit_field_value)
        await state.update_data(staff_master_id=master_id, staff_id=staff_id, staff_edit_field=field)
        field_name = "имя сотрудника" if field == "name" else "специализацию"
        text = f"✏️ Введите новое {field_name}:"
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:staff:card:{master_id}:{staff_id}")]]
        )
        await callback.message.edit_text(text, reply_markup=cancel_kb)
        await callback.answer()
        return


@manager_router.message(ManagerStaffStates.waiting_for_name)
async def msg_manager_staff_name(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Handle new staff name."""
    name = (message.text or "").strip()
    if not name or len(name) < 2 or len(name) > 128:
        await message.answer("Имя должно быть от 2 до 128 символов. Попробуйте еще раз:")
        return
    await state.update_data(new_staff_name=name)
    await state.set_state(ManagerStaffStates.waiting_for_specialization)
    text = (
        f"Имя: <b>{escape(name)}</b>\n\n"
        "✂️ Введите специализацию мастера\n"
        "(например: <i>Top Brow-Master</i>, <i>Lashmaker</i>, <i>Колорист</i>)\n\n"
        "<i>Или отправьте «-», чтобы оставить пустой:</i>"
    )
    await message.answer(text)


@manager_router.message(ManagerStaffStates.waiting_for_specialization)
async def msg_manager_staff_specialization(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Handle new staff specialization, create staff and auto-bind active services."""
    spec_raw = (message.text or "").strip()
    spec = None if spec_raw in ("-", "—", "пропустить", "нет") else spec_raw
    if spec and len(spec) > 128:
        await message.answer("Специализация слишком длинная (максимум 128 символов):")
        return

    data = await state.get_data()
    master_id = data.get("staff_master_id")
    name = data.get("new_staff_name")
    if not master_id or not name:
        await state.clear()
        await message.answer("Ошибка сессии.", reply_markup=main_menu_keyboard())
        return

    staff_repo = StaffRepository(session)
    staff = await staff_repo.create_staff(
        master_id=master_id,
        display_name=name,
        specialization=spec,
    )

    # Auto-assign all active project services to the newly created staff member
    active_services = await ServiceRepository(session).list_active(master_id)
    if active_services:
        await staff_repo.set_staff_services(
            staff_id=staff.id,
            master_id=master_id,
            service_ids=[s.id for s in active_services],
        )

    await state.clear()
    text = (
        f"✅ Специалист <b>«{escape(staff.display_name)}»</b> успешно добавлен!\n\n"
        f"✂️ Специализация: <b>{escape(staff.specialization or '—')}</b>\n"
        f"💅 Привязано услуг: <b>{len(active_services)}</b>\n\n"
        "Теперь вы можете настроить его индивидуальные услуги или отправить приглашение:"
    )
    await message.answer(text, reply_markup=staff_card_keyboard(master_id, staff))


@manager_router.message(ManagerStaffStates.waiting_for_edit_field_value)
async def msg_manager_staff_edit_field(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save edited staff attribute (name or specialization)."""
    val = (message.text or "").strip()
    if not val or len(val) > 128:
        await message.answer("Значение должно быть длиной до 128 символов:")
        return

    data = await state.get_data()
    master_id = data.get("staff_master_id")
    staff_id = data.get("staff_id")
    field = data.get("staff_edit_field")
    if not master_id or not staff_id or not field:
        await state.clear()
        return

    staff_repo = StaffRepository(session)
    if field == "name":
        await staff_repo.update_staff(staff_id, master_id, display_name=val)
    elif field == "spec":
        await staff_repo.update_staff(staff_id, master_id, specialization=val)

    await state.clear()
    staff = await staff_repo.get_by_id(staff_id, master_id)
    await message.answer("✅ Данные специалиста обновлены!", reply_markup=staff_card_keyboard(master_id, staff))
