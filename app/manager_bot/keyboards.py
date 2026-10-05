"""Inline keyboards for platform Manager Bot."""

from collections.abc import Sequence
from typing import Optional
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestManagedBot,
    ReplyKeyboardMarkup,
)

from app.config.settings import settings
from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterSettings
from app.database.models.portfolio import PortfolioCategory, PortfolioItem
from app.database.models.service import DepositType, Service
from app.database.models.staff import StaffMember
from app.database.models.subscription import EffectiveSubscriptionStatus, SubscriptionPlan


def main_menu_keyboard(is_platform_admin: bool = False) -> InlineKeyboardMarkup:
    """Main menu of Manager Bot."""
    buttons = []
    if is_platform_admin:
        buttons.append([InlineKeyboardButton(text="👑 ZapisFlow Admin", callback_data="mgr:admin:menu")])
    buttons.extend([
        [InlineKeyboardButton(text="🏠 Мои проекты", callback_data="mgr:projects")],
        [InlineKeyboardButton(text="➕ Создать проект", callback_data="mgr:master:new")],
        [
            InlineKeyboardButton(text="💳 Подписка", callback_data="mgr:sub:menu"),
            InlineKeyboardButton(text="🆘 Поддержка", url=settings.support_url),
        ],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="mgr:help")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def project_list_keyboard(masters: Sequence[Master]) -> InlineKeyboardMarkup:
    """List of user's registered master projects."""
    buttons = []
    for master in masters:
        status_icon = "🟢" if master.status.value == "ACTIVE" else "🟡"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {master.display_name}",
                callback_data=f"mgr:master:{master.id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="➕ Новый проект", callback_data="mgr:master:new")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="mgr:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def project_card_keyboard(
    master: Master,
    bot_instance: Optional[BotInstance] = None,
    is_ready: bool = False,
    is_staff_only: bool = False,
    can_configure_bot: bool = True,
) -> InlineKeyboardMarkup:
    """Action buttons for a single Master project."""
    rows = []

    if is_staff_only:
        # Restricted view for regular specialists
        rows.append([
            InlineKeyboardButton(text="📅 Моё расписание", callback_data=f"mgr:schedule:{master.id}"),
            InlineKeyboardButton(text="👥 Мои клиенты", callback_data=f"mgr:crm:{master.id}"),
        ])
        rows.append([
            InlineKeyboardButton(text="💅 Услуги", callback_data=f"mgr:services:{master.id}"),
            InlineKeyboardButton(text="⭐ Отзывы", callback_data=f"mgr:reviews:{master.id}"),
        ])
        rows.append([
            InlineKeyboardButton(text="📊 Моя статистика", callback_data=f"mgr:stats:{master.id}"),
            InlineKeyboardButton(text="🖼 Портфолио", callback_data=f"mgr:portfolio:{master.id}"),
        ])
        rows.append([InlineKeyboardButton(text="⬅️ Назад к проектам", callback_data="mgr:projects")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if bot_instance is None:
        # No bot connected yet
        rows.append([
            InlineKeyboardButton(
                text="🤖 Подключить Telegram-бота",
                callback_data=f"mgr:bot:connect:{master.id}",
            )
        ])
    elif bot_instance.status == BotInstanceStatus.SETUP_REQUIRED:
        if bot_instance.telegram_username:
            rows.append([
                InlineKeyboardButton(
                    text="🔗 Настроить в боте (Deep Link)",
                    url=f"https://t.me/{bot_instance.telegram_username}?start=admin",
                )
            ])
        rows.append([
            InlineKeyboardButton(
                text="🚀 Запустить приём записей",
                callback_data=f"mgr:bot:activate:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="📋 Чек-лист готовности",
                callback_data=f"mgr:bot:checklist:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="♻️ Заменить токен",
                callback_data=f"mgr:bot:rotate:{master.id}",
            ),
            InlineKeyboardButton(
                text="⏸ Отключить",
                callback_data=f"mgr:bot:disable:{master.id}",
            ),
        ])
    elif bot_instance.status == BotInstanceStatus.ACTIVE:
        if bot_instance.telegram_username:
            rows.append([
                InlineKeyboardButton(
                    text="🔗 Открыть бота",
                    url=f"https://t.me/{bot_instance.telegram_username}?start=admin",
                )
            ])
        rows.append([
            InlineKeyboardButton(
                text="📋 Чек-лист настроек",
                callback_data=f"mgr:bot:checklist:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="♻️ Заменить токен",
                callback_data=f"mgr:bot:rotate:{master.id}",
            ),
            InlineKeyboardButton(
                text="⏸ Отключить бота",
                callback_data=f"mgr:bot:disable:{master.id}",
            ),
        ])
    elif bot_instance.status == BotInstanceStatus.ERROR:
        rows.append([
            InlineKeyboardButton(
                text="🔄 Повторить подключение",
                callback_data=f"mgr:bot:retry:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="♻️ Заменить токен",
                callback_data=f"mgr:bot:rotate:{master.id}",
            )
        ])
    elif bot_instance.status == BotInstanceStatus.DISABLED:
        rows.append([
            InlineKeyboardButton(
                text="▶️ Включить бота",
                callback_data=f"mgr:bot:enable:{master.id}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="♻️ Заменить токен",
                callback_data=f"mgr:bot:rotate:{master.id}",
            )
        ])

    if bot_instance and bot_instance.status in (BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED):
        rows.append([InlineKeyboardButton(
            text="🔄 Обновить webhook", callback_data=f"mgr:bot:resync:{bot_instance.id}",
        )])

    if can_configure_bot and bot_instance and bot_instance.status in (BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED):
        rows.append([InlineKeyboardButton(text="🤖 Настройки бота → Mini App", callback_data=f"mgr:bot:miniapp:{bot_instance.id}:view")])
        rows.append([InlineKeyboardButton(text="🌐 Запись через сайт", callback_data=f"mgr:bot:web:{bot_instance.id}:view")])

    # Business & CRM features
    rows.append([
        InlineKeyboardButton(text="📅 Расписание", callback_data=f"mgr:schedule:{master.id}"),
        InlineKeyboardButton(text="👥 Клиенты CRM", callback_data=f"mgr:crm:{master.id}"),
        InlineKeyboardButton(text="💰 Финансы", callback_data=f"mgr:finances:{master.id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="👥 Сотрудники", callback_data=f"mgr:staff:{master.id}"),
        InlineKeyboardButton(text="💅 Услуги", callback_data=f"mgr:services:{master.id}"),
        InlineKeyboardButton(text="🖼 Портфолио", callback_data=f"mgr:portfolio:{master.id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="⭐ Отзывы", callback_data=f"mgr:reviews:{master.id}"),
        InlineKeyboardButton(text="📊 Статистика", callback_data=f"mgr:stats:{master.id}"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"mgr:settings:{master.id}"),
    ])

    # Subscription management
    rows.append([
        InlineKeyboardButton(
            text="💳 Подписка и тариф",
            callback_data=f"mgr:sub:{master.id}",
        )
    ])

    if bot_instance is not None:
        rows.append([
            InlineKeyboardButton(
                text="🗑 Удалить бота",
                callback_data=f"mgr:bot:delete:{master.id}",
            )
        ])

    rows.append([InlineKeyboardButton(text="⬅️ Назад к проектам", callback_data="mgr:projects")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_projects_keyboard(masters: Sequence[Master]) -> InlineKeyboardMarkup:
    """List of user's projects for subscription selection."""
    buttons = []
    for master in masters:
        status_icon = "🟢" if master.subscription_status.value == "ACTIVE" else ("🟡" if master.subscription_status.value == "TRIAL" else "🔴")
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {master.display_name}",
                callback_data=f"mgr:sub:{master.id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="mgr:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def support_button(text: Optional[str] = None) -> InlineKeyboardButton:
    """Standardized support link button to @zapisflow."""
    btn_text = text or f"💬 Поддержка @{settings.support_telegram_username}"
    return InlineKeyboardButton(text=btn_text, url=settings.support_url)


def subscription_card_keyboard(
    master_id: int,
    plans: Sequence[SubscriptionPlan],
    status: Optional[EffectiveSubscriptionStatus] = None,
    *,
    can_pay: bool = True,
) -> InlineKeyboardMarkup:
    """Action buttons for subscription management screen."""
    rows = []
    # A callback performs the owner check. No amount or payment URL is accepted
    # from callback_data; the backend reads the plan and creates the redirect.
    show_pay_action = (
        settings.uses_yookassa
        or (settings.payment_provider.lower() == "manual" and not settings.is_production)
    ) and status != EffectiveSubscriptionStatus.SUSPENDED and can_pay
    for plan in (plans if show_pay_action else ()):
        price_fmt = f"{plan.price:,.2f}".replace(",", " ").removesuffix(".00") + " ₽"
        if status == EffectiveSubscriptionStatus.PAID_ACTIVE:
            btn_text = f"💳 Продлить: {plan.name} — {price_fmt}"
        elif status == EffectiveSubscriptionStatus.TRIAL_ACTIVE:
            btn_text = f"💳 Оформить: {plan.name} — {price_fmt}"
        elif status == EffectiveSubscriptionStatus.EXPIRED:
            btn_text = f"💳 Оплатить {price_fmt} ({plan.name})"
        else:
            btn_text = f"💳 {plan.name} — {price_fmt}"

        rows.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"mgr:sub:pay:{master_id}:{plan.code}",
            )
        ])

    rows.append([support_button()])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="mgr:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_checkout_keyboard(
    master_id: int,
    url: str,
    payment_id: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """Provider-confirmed redirect returned by YooKassa for this order with status check button."""
    rows = [
        [InlineKeyboardButton(text="💳 Оплатить", url=url)],
    ]
    if payment_id is not None:
        rows.append([
            InlineKeyboardButton(
                text="🔄 Проверить оплату",
                callback_data=f"mgr:sub:check:{master_id}:{payment_id}",
            )
        ])
    rows.append([support_button()])
    rows.append([InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:sub:{master_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_pending_keyboard(
    master_id: int,
    payment_id: int,
    payment_url: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """Pending payment status screen keyboard."""
    rows = []
    if payment_url:
        rows.append([InlineKeyboardButton(text="💳 Оплатить", url=payment_url)])
    rows.append([
        InlineKeyboardButton(
            text="🔄 Проверить оплату",
            callback_data=f"mgr:sub:check:{master_id}:{payment_id}",
        )
    ])
    rows.append([support_button()])
    rows.append([InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:sub:{master_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_canceled_keyboard(
    master_id: int,
    plan_code: str,
) -> InlineKeyboardMarkup:
    """Canceled payment screen keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить снова", callback_data=f"mgr:sub:pay:{master_id}:{plan_code}")],
        [support_button()],
        [InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:sub:{master_id}")],
    ])


def subscription_success_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Success subscription payment screen keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Моя подписка", callback_data=f"mgr:sub:{master_id}")],
        [support_button()],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="mgr:menu")],
    ])


def subscription_payment_keyboard(
    master_id: int,
    payment_id: int,
    payment_url: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """Confirmation buttons for subscription payment."""
    rows = []
    if settings.is_production:
        if payment_url:
            rows.append([InlineKeyboardButton(text="💳 Оплатить онлайн", url=payment_url)])
        rows.append([InlineKeyboardButton(text="🆘 Написать в поддержку", url=settings.support_url)])
    elif payment_url:
        rows.append([InlineKeyboardButton(text="💳 Оплатить онлайн", url=payment_url)])
    if not settings.is_production:
        rows.append([
            InlineKeyboardButton(
                text="✅ Подтвердить оплату (тест/ручная)",
                callback_data=f"mgr:sub:confirm:{master_id}:{payment_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:sub:{master_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_connect_keyboard() -> InlineKeyboardMarkup:
    """Confirmation buttons for onboarding candidate bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подключить", callback_data="mgr:bot:confirm_connect"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="mgr:cancel"),
            ]
        ]
    )


def bot_connect_method_choice_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Choose bot connection method: Telegram Managed Bot (1-click) or manual token."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Создать нового бота (1 клик)",
                    callback_data=f"mgr:bot:mg:prep:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔑 Подключить через токен (BotFather)",
                    callback_data=f"mgr:bot:token:start:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад к проекту",
                    callback_data=f"mgr:master:{master_id}",
                )
            ],
        ]
    )


def managed_bot_prepare_keyboard(
    master_id: int,
    creation_url: str,
    suggested_username: str,
) -> InlineKeyboardMarkup:
    """Options for creating a managed bot via deep link or reply button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Создать бота в Telegram",
                    url=creation_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="⌨️ Создать через кнопку в чате",
                    callback_data=f"mgr:bot:mg:reply:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"✏️ Изменить логин (@{suggested_username})",
                    callback_data=f"mgr:bot:mg:custom:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔑 Подключить токеном вручную",
                    callback_data=f"mgr:bot:token:start:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад к выбору",
                    callback_data=f"mgr:bot:connect:{master_id}",
                )
            ],
        ]
    )


def managed_bot_reply_keyboard(
    suggested_name: str,
    suggested_username: str,
    request_id: int = 1,
) -> ReplyKeyboardMarkup:
    """Native reply keyboard with request_managed_bot button."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=f"🤖 Создать @{suggested_username}",
                    request_managed_bot=KeyboardButtonRequestManagedBot(
                        request_id=request_id,
                        suggested_name=suggested_name,
                        suggested_username=suggested_username,
                    ),
                )
            ],
            [
                KeyboardButton(text="❌ Отмена"),
            ],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def managed_bot_success_keyboard(
    master_id: int,
    bot_username: str,
) -> InlineKeyboardMarkup:
    """Action buttons after managed bot is successfully created and provisioned."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Открыть бота",
                    url=f"https://t.me/{bot_username}?start=admin",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Чек-лист готовности",
                    callback_data=f"mgr:bot:checklist:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏢 Настроить проект",
                    callback_data=f"mgr:master:{master_id}",
                )
            ],
        ]
    )


def managed_bot_rotate_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Options for rotating token of a managed bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="♻️ Обновить токен автоматически",
                    callback_data=f"mgr:bot:mg:rot_confirm:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔑 Ввести токен вручную",
                    callback_data=f"mgr:bot:token:start:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Отмена",
                    callback_data=f"mgr:master:{master_id}",
                )
            ],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Generic cancel button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="mgr:cancel")]
        ]
    )


def confirm_disable_bot_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Confirmation buttons before disconnecting/disabling a bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔴 Да, отключить бота",
                    callback_data=f"mgr:bot:confirm_disable:{master_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Отмена",
                    callback_data=f"mgr:master:{master_id}",
                )
            ],
        ]
    )


def bot_delete_confirm_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Confirmation before unlinking and logically deleting a bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:master:{master_id}"),
                InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"mgr:bot:delete:confirm:{master_id}"),
            ]
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    """Platform Admin main navigation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Dashboard", callback_data="mgr:admin:dashboard")],
            [
                InlineKeyboardButton(text="👥 Пользователи", callback_data="mgr:admin:users"),
                InlineKeyboardButton(text="🏢 Проекты", callback_data="mgr:admin:projects"),
            ],
            [
                InlineKeyboardButton(text="🤖 Боты", callback_data="mgr:admin:bots"),
                InlineKeyboardButton(text="💳 Подписки", callback_data="mgr:admin:subscriptions"),
            ],
            [
                InlineKeyboardButton(text="💰 Платежи", callback_data="mgr:admin:payments"),
                InlineKeyboardButton(text="📦 Тарифы", callback_data="mgr:admin:plans"),
            ],
            [
                InlineKeyboardButton(text="📈 SaaS Аналитика", callback_data="mgr:admin:metrics"),
                InlineKeyboardButton(text="📝 Audit Log", callback_data="mgr:admin:audit"),
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="mgr:menu")],
        ]
    )


def admin_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Platform Admin dashboard navigation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="mgr:admin:dashboard")],
            [
                InlineKeyboardButton(text="👥 Пользователи", callback_data="mgr:admin:users"),
                InlineKeyboardButton(text="🏢 Проекты", callback_data="mgr:admin:projects"),
            ],
            [
                InlineKeyboardButton(text="🤖 Боты", callback_data="mgr:admin:bots"),
                InlineKeyboardButton(text="💳 Подписки", callback_data="mgr:admin:subscriptions"),
            ],
            [
                InlineKeyboardButton(text="💰 Платежи", callback_data="mgr:admin:payments"),
                InlineKeyboardButton(text="📦 Тарифы", callback_data="mgr:admin:plans"),
            ],
            [
                InlineKeyboardButton(text="📈 SaaS Аналитика", callback_data="mgr:admin:metrics"),
                InlineKeyboardButton(text="📝 Audit Log", callback_data="mgr:admin:audit"),
            ],
            [InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")],
        ]
    )


def admin_users_keyboard(
    users: Sequence[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Paginated user list for Platform Admin."""
    buttons = []
    for u in users:
        label = f"{u['first_name']} (@{u['username']})" if u.get("username") else f"{u['first_name']} (id: {u['id']})"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"mgr:admin:user:{u['id']}")
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"mgr:admin:users:p:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр {page}/{total_pages}", callback_data=f"mgr:admin:users:p:{page}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="След ➡️", callback_data=f"mgr:admin:users:p:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_user_detail_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """User detail view actions with sub-navigation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏢 Проекты", callback_data=f"mgr:admin:u_projects:{user_id}"),
                InlineKeyboardButton(text="🤖 Боты", callback_data=f"mgr:admin:u_bots:{user_id}"),
            ],
            [
                InlineKeyboardButton(text="💳 Подписки", callback_data=f"mgr:admin:u_subs:{user_id}"),
                InlineKeyboardButton(text="💰 Платежи", callback_data=f"mgr:admin:u_payments:{user_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ К списку пользователей", callback_data="mgr:admin:users")],
        ]
    )


def admin_user_projects_keyboard(projects: Sequence[dict], user_id: int) -> InlineKeyboardMarkup:
    """List of projects owned by a user."""
    buttons = []
    for p in projects:
        icon = "🟢" if p["status"] == "ACTIVE" else "🟡"
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {p['display_name']} ({p['subscription_status']})",
                callback_data=f"mgr:admin:project:{p['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"mgr:admin:user:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_user_bots_keyboard(bots: Sequence[dict], user_id: int) -> InlineKeyboardMarkup:
    """List of bots owned by a user."""
    buttons = []
    for b in bots:
        icon = "🟢" if b["status"] == "ACTIVE" else "🔴"
        name = f"@{b['telegram_username']}" if b.get("telegram_username") else f"Bot #{b['id']}"
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {name} ({b['master_name']})",
                callback_data=f"mgr:admin:bot:{b['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"mgr:admin:user:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_user_subscriptions_keyboard(subs: Sequence[dict], user_id: int) -> InlineKeyboardMarkup:
    """List of subscriptions for a user's projects."""
    buttons = []
    for s in subs:
        icon = "🟢" if s["subscription_status"] == "ACTIVE" else ("🟡" if s["subscription_status"] == "TRIAL" else "🔴")
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {s['project_name']} ({s['subscription_status']})",
                callback_data=f"mgr:admin:project:sub:{s['master_id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"mgr:admin:user:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_user_payments_keyboard(payments: Sequence[dict], user_id: int) -> InlineKeyboardMarkup:
    """List of payments made by a user."""
    buttons = []
    for p in payments:
        icon = "🟢" if p["status"] == "SUCCEEDED" else "🔴"
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {p['amount']} {p['currency']} - {p['project_name']}",
                callback_data=f"mgr:admin:payment:{p['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"mgr:admin:user:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_projects_keyboard(
    projects: Sequence[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Paginated project list for Platform Admin."""
    buttons = []
    for p in projects:
        icon = "🟢" if p["status"] == "ACTIVE" else "🟡"
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {p['display_name']}",
                callback_data=f"mgr:admin:project:{p['id']}",
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"mgr:admin:projects:p:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр {page}/{total_pages}", callback_data=f"mgr:admin:projects:p:{page}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="След ➡️", callback_data=f"mgr:admin:projects:p:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_project_detail_keyboard(master_id: int, is_suspended: bool) -> InlineKeyboardMarkup:
    """Project detail action buttons."""
    action_text = "▶️ Активировать проект" if is_suspended else "⏸ Приостановить проект"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Подписка проекта", callback_data=f"mgr:admin:project:sub:{master_id}")],
            [InlineKeyboardButton(text=action_text, callback_data=f"mgr:admin:project:suspend:{master_id}")],
            [InlineKeyboardButton(text="🗑 Полностью удалить проект", callback_data=f"mgr:admin:project:hard_delete:{master_id}")],
            [InlineKeyboardButton(text="⬅️ К списку проектов", callback_data="mgr:admin:projects")],
        ]
    )


def admin_project_hard_delete_confirm_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """First-step confirmation modal for project hard deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ Продолжить", callback_data=f"mgr:admin:project:hard_delete_prompt:{master_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:project:{master_id}")],
        ]
    )


def admin_subscription_detail_keyboard(master_id: int, is_suspended: bool) -> InlineKeyboardMarkup:
    """Subscription detail card actions."""
    toggle_text = "▶️ Активировать" if is_suspended else "⏸ Приостановить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить время", callback_data=f"mgr:admin:sub:extend:{master_id}"),
                InlineKeyboardButton(text="📅 Установить дату", callback_data=f"mgr:admin:sub:set_date:{master_id}"),
            ],
            [
                InlineKeyboardButton(text=toggle_text, callback_data=f"mgr:admin:project:suspend:{master_id}"),
                InlineKeyboardButton(text="📜 История изменений", callback_data=f"mgr:admin:sub:history:{master_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ К проекту", callback_data=f"mgr:admin:project:{master_id}")],
        ]
    )


def admin_subscription_extend_presets_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Preset options for extending subscription days and months."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ 1 день", callback_data=f"mgr:admin:sub:ext_preset:{master_id}:1"),
                InlineKeyboardButton(text="➕ 7 дней", callback_data=f"mgr:admin:sub:ext_preset:{master_id}:7"),
                InlineKeyboardButton(text="➕ 14 дней", callback_data=f"mgr:admin:sub:ext_preset:{master_id}:14"),
            ],
            [
                InlineKeyboardButton(text="➕ 30 дней", callback_data=f"mgr:admin:sub:ext_preset:{master_id}:30"),
                InlineKeyboardButton(text="➕ 1 месяц (30 дн.)", callback_data=f"mgr:admin:sub:ext_preset:{master_id}:30"),
            ],
            [
                InlineKeyboardButton(text="➕ 3 месяца (90 дн.)", callback_data=f"mgr:admin:sub:ext_preset:{master_id}:90"),
                InlineKeyboardButton(text="➕ 6 месяцев (180 дн.)", callback_data=f"mgr:admin:sub:ext_preset:{master_id}:180"),
            ],
            [
                InlineKeyboardButton(text="✏️ Своё кол-во дней", callback_data=f"mgr:admin:sub:ext_custom_d:{master_id}"),
                InlineKeyboardButton(text="✏️ Своё кол-во месяцев", callback_data=f"mgr:admin:sub:ext_custom_m:{master_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"mgr:admin:project:sub:{master_id}")],
        ]
    )


def admin_subscription_extend_reason_keyboard(
    master_id: int, days: int, base_ts: int
) -> InlineKeyboardMarkup:
    """Reason selection modal for manual subscription extension."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤝 Партнёрская",
                    callback_data=f"mgr:admin:sub:ext_do:{master_id}:{days}:{base_ts}:partner",
                ),
                InlineKeyboardButton(
                    text="🎁 Компенсация",
                    callback_data=f"mgr:admin:sub:ext_do:{master_id}:{days}:{base_ts}:compensation",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧪 Тестирование",
                    callback_data=f"mgr:admin:sub:ext_do:{master_id}:{days}:{base_ts}:testing",
                ),
                InlineKeyboardButton(
                    text="🛠 Поддержка",
                    callback_data=f"mgr:admin:sub:ext_do:{master_id}:{days}:{base_ts}:support",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 Другая причина",
                    callback_data=f"mgr:admin:sub:ext_do:{master_id}:{days}:{base_ts}:other",
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:project:sub:{master_id}")],
        ]
    )


def admin_subscription_set_expiry_confirm_keyboard(
    master_id: int, date_iso: str, is_past: bool
) -> InlineKeyboardMarkup:
    """Confirmation modal for setting exact subscription expiry date."""
    action_text = "✅ Завершить подписку" if is_past else "✅ Подтвердить дату"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=action_text, callback_data=f"mgr:admin:sub:set_date_do:{master_id}:{date_iso}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:project:sub:{master_id}")],
        ]
    )


def admin_subscription_history_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Back button from subscription change history."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:admin:project:sub:{master_id}")]
        ]
    )


def admin_bots_keyboard(
    bots: Sequence[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Paginated bot list for Platform Admin."""
    buttons = []
    for b in bots:
        icon = "🟢" if b["status"] == "ACTIVE" else "🔴"
        name = f"@{b['telegram_username']}" if b.get("telegram_username") else f"Bot #{b['id']}"
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {name} ({b['master_name']})",
                callback_data=f"mgr:admin:bot:{b['id']}",
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"mgr:admin:bots:p:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр {page}/{total_pages}", callback_data=f"mgr:admin:bots:p:{page}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="След ➡️", callback_data=f"mgr:admin:bots:p:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_bot_detail_keyboard(bot_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Bot instance detail actions."""
    toggle_text = "⏸ Отключить бота" if is_active else "▶️ Включить бота"
    action = "disable" if is_active else "enable"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить webhook", callback_data=f"mgr:admin:bot:webhook:{bot_id}")],
            [InlineKeyboardButton(text=toggle_text, callback_data=f"mgr:admin:bot:{action}:{bot_id}")],
            [InlineKeyboardButton(text="🗑 Отключить от проекта", callback_data=f"mgr:admin:bot:delete:{bot_id}")],
            [InlineKeyboardButton(text="💥 Полностью удалить бота", callback_data=f"mgr:admin:bot:hard_delete:{bot_id}")],
            [InlineKeyboardButton(text="⬅️ К списку ботов", callback_data="mgr:admin:bots")],
        ]
    )


def admin_bot_hard_delete_confirm_keyboard(bot_id: int) -> InlineKeyboardMarkup:
    """First-step confirmation modal for bot hard deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ Продолжить", callback_data=f"mgr:admin:bot:hard_delete_prompt:{bot_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:bot:{bot_id}")],
        ]
    )


def admin_subscriptions_keyboard(
    subscriptions: Sequence[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Subscriptions list for Platform Admin."""
    buttons = []
    for s in subscriptions:
        icon = "🟢" if s["subscription_status"] == "ACTIVE" else ("🟡" if s["subscription_status"] == "TRIAL" else "🔴")
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {s['project_name']} ({s['subscription_status']})",
                callback_data=f"mgr:admin:project:sub:{s['master_id']}",
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"mgr:admin:sub:p:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр {page}/{total_pages}", callback_data=f"mgr:admin:sub:p:{page}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="След ➡️", callback_data=f"mgr:admin:sub:p:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_payments_keyboard(
    payments: Sequence[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Paginated payments list for Platform Admin."""
    buttons = []
    for p in payments:
        icon = "🟢" if p["status"] == "SUCCEEDED" else ("🟡" if p["status"] == "PENDING" else "🔴")
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {p['amount']} {p['currency']} - {p['project_name']}",
                callback_data=f"mgr:admin:payment:{p['id']}",
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"mgr:admin:payments:p:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр {page}/{total_pages}", callback_data=f"mgr:admin:payments:p:{page}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="След ➡️", callback_data=f"mgr:admin:payments:p:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_payment_detail_keyboard(
    payment_id: int, provider: str, status: str
) -> InlineKeyboardMarkup:
    """Payment detail actions."""
    rows = []
    if status == "PENDING" and provider == "YOOKASSA":
        rows.append([
            InlineKeyboardButton(
                text="🔄 Проверить в YooKassa",
                callback_data=f"mgr:admin:payment:check:{payment_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ К платежам", callback_data="mgr:admin:payments")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_plans_keyboard(plans: Sequence[SubscriptionPlan]) -> InlineKeyboardMarkup:
    """Catalog of subscription plans."""
    buttons = []
    for p in plans:
        icon = "🟢" if p.is_active else "🔴"
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {p.name} ({p.price_rub} ₽ / {p.duration_days} дн.)",
                callback_data=f"mgr:admin:plan:{p.id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_plan_detail_keyboard(plan_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Plan detail action buttons."""
    toggle_text = "🔴 Деактивировать" if is_active else "🟢 Активировать"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Название", callback_data=f"mgr:admin:plan:name:{plan_id}"),
                InlineKeyboardButton(text="💰 Цена", callback_data=f"mgr:admin:plan:price:{plan_id}"),
            ],
            [
                InlineKeyboardButton(text="📅 Длительность", callback_data=f"mgr:admin:plan:dur:{plan_id}"),
                InlineKeyboardButton(text="🧩 Features", callback_data=f"mgr:admin:plan:feat:{plan_id}"),
            ],
            [
                InlineKeyboardButton(text="🔢 Порядок", callback_data=f"mgr:admin:plan:sort:{plan_id}"),
                InlineKeyboardButton(text=toggle_text, callback_data=f"mgr:admin:plan:toggle:{plan_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ К тарифам", callback_data="mgr:admin:plans")],
        ]
    )


def admin_plan_price_confirm_keyboard(plan_id: int, new_price_str: str) -> InlineKeyboardMarkup:
    """Confirmation modal for plan price change."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"mgr:admin:plan:price_confirm:{plan_id}:{new_price_str}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:admin:plan:{plan_id}")],
        ]
    )


def admin_plan_duration_presets_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    """Presets for plan duration in days."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="7 дней", callback_data=f"mgr:admin:plan:dur_set:{plan_id}:7"),
                InlineKeyboardButton(text="14 дней", callback_data=f"mgr:admin:plan:dur_set:{plan_id}:14"),
                InlineKeyboardButton(text="30 дней", callback_data=f"mgr:admin:plan:dur_set:{plan_id}:30"),
            ],
            [
                InlineKeyboardButton(text="60 дней", callback_data=f"mgr:admin:plan:dur_set:{plan_id}:60"),
                InlineKeyboardButton(text="90 дней", callback_data=f"mgr:admin:plan:dur_set:{plan_id}:90"),
                InlineKeyboardButton(text="180 дней", callback_data=f"mgr:admin:plan:dur_set:{plan_id}:180"),
            ],
            [
                InlineKeyboardButton(text="365 дней", callback_data=f"mgr:admin:plan:dur_set:{plan_id}:365"),
                InlineKeyboardButton(text="✏️ Свой срок", callback_data=f"mgr:admin:plan:dur_custom:{plan_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"mgr:admin:plan:{plan_id}")],
        ]
    )


def admin_plan_features_keyboard(plan_id: int, features: dict) -> InlineKeyboardMarkup:
    """Whitelist feature toggles for plan."""
    buttons = []
    allowed_keys = [
        ("max_bots", "Боты"),
        ("max_staff", "Сотрудники"),
        ("custom_branding", "Брендинг"),
        ("broadcasts", "Рассылки"),
        ("analytics", "Аналитика"),
        ("priority_support", "Поддержка"),
    ]
    for key, label in allowed_keys:
        val = features.get(key)
        icon = f"✅ {val}" if val is not None and val is not False else "❌ Выкл"
        buttons.append([
            InlineKeyboardButton(
                text=f"{label}: {icon}",
                callback_data=f"mgr:admin:plan:feat_toggle:{plan_id}:{key}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ К тарифу", callback_data=f"mgr:admin:plan:{plan_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_audit_logs_keyboard(
    logs: Sequence[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Paginated list of platform audit events."""
    buttons = []
    for l in logs:
        ts_str = l["created_at"].strftime("%d.%m %H:%M") if l.get("created_at") else "-"
        action_name = l["action"][:18]
        buttons.append([
            InlineKeyboardButton(
                text=f"{ts_str} | {action_name} (#{l['id']})",
                callback_data=f"mgr:admin:audit:{l['id']}",
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"mgr:admin:audit:p:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр {page}/{total_pages}", callback_data=f"mgr:admin:audit:p:{page}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="След ➡️", callback_data=f"mgr:admin:audit:p:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_audit_log_detail_keyboard() -> InlineKeyboardMarkup:
    """Back button from audit log detail."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К списку логов", callback_data="mgr:admin:audit")]
        ]
    )


def admin_metrics_keyboard() -> InlineKeyboardMarkup:
    """Metrics view actions."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить метрики", callback_data="mgr:admin:metrics")],
            [InlineKeyboardButton(text="⬅️ В админку", callback_data="mgr:admin:menu")],
        ]
    )


# ---------------------------------------------------------------------------
# Phase 3: CRM & Master Business Features Keyboards
# ---------------------------------------------------------------------------


def crm_menu_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Main CRM dashboard menu with client segmentation."""
    buttons = [
        [InlineKeyboardButton(text="🔎 Найти клиента", callback_data=f"mgr:crm:search:{master_id}")],
        [InlineKeyboardButton(text="👥 Все клиенты", callback_data=f"mgr:crm:seg:{master_id}:all:1")],
        [InlineKeyboardButton(text="⭐ Постоянные (3+ визита)", callback_data=f"mgr:crm:seg:{master_id}:regular:1")],
        [InlineKeyboardButton(text="🆕 Новые клиенты (до 30 дн.)", callback_data=f"mgr:crm:seg:{master_id}:new:1")],
        [InlineKeyboardButton(text="⏰ Давно не были (30+ дней)", callback_data=f"mgr:crm:seg:{master_id}:inactive30:1")],
        [InlineKeyboardButton(text="⏰ Давно не были (60+ дней)", callback_data=f"mgr:crm:seg:{master_id}:inactive60:1")],
        [InlineKeyboardButton(text="📤 Экспорт клиентов (CSV)", callback_data=f"mgr:crm:export:{master_id}")],
        [InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def crm_client_list_keyboard(
    master_id: int,
    segment: str,
    page: int,
    total_count: int,
    clients: Sequence[dict],
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    """Paginated list of clients for a segment or search query."""
    buttons = []
    for c in clients:
        name = c.get("full_name") or "Клиент"
        phone = f" ({c['phone']})" if c.get("phone") else ""
        visits = f" • {c.get('completed', 0)} виз."
        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {name}{phone}{visits}",
                callback_data=f"mgr:client:{master_id}:{c['user_id']}:{segment}:{page}",
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"mgr:crm:seg:{master_id}:{segment}:{page - 1}",
            )
        )
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    nav_row.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data="mgr:noop",
        )
    )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Вперёд ▶️",
                callback_data=f"mgr:crm:seg:{master_id}:{segment}:{page + 1}",
            )
        )
    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton(text="🔍 Другой поиск / сегмент", callback_data=f"mgr:crm:{master_id}"),
        InlineKeyboardButton(text="⬅️ К проекту", callback_data=f"mgr:master:{master_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def crm_client_card_keyboard(
    master_id: int, user_id: int, segment: str = "all", page: int = 1
) -> InlineKeyboardMarkup:
    """Action buttons for client profile in CRM."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 История записей",
                    callback_data=f"mgr:client:hist:{master_id}:{user_id}:1:{segment}:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить заметку",
                    callback_data=f"mgr:client:note:{master_id}:{user_id}:{segment}:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку клиентов",
                    callback_data=f"mgr:crm:seg:{master_id}:{segment}:{page}",
                ),
                InlineKeyboardButton(
                    text="🏠 В меню CRM",
                    callback_data=f"mgr:crm:{master_id}",
                ),
            ],
        ]
    )


def crm_client_history_keyboard(
    master_id: int, user_id: int, page: int, total_count: int, segment: str = "all", client_page: int = 1, page_size: int = 5
) -> InlineKeyboardMarkup:
    """Pagination for client's appointments history."""
    buttons = []
    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"mgr:client:hist:{master_id}:{user_id}:{page - 1}:{segment}:{client_page}",
            )
        )
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    nav_row.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data="mgr:noop",
        )
    )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Вперёд ▶️",
                callback_data=f"mgr:client:hist:{master_id}:{user_id}:{page + 1}:{segment}:{client_page}",
            )
        )
    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к карточке клиента",
            callback_data=f"mgr:client:{master_id}:{user_id}:{segment}:{client_page}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def stats_period_keyboard(master_id: int, current_period: str = "month") -> InlineKeyboardMarkup:
    """Period selector for master business statistics."""
    p = current_period.lower()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'🔘' if p == 'today' else '⚪️'} Сегодня",
                    callback_data=f"mgr:stats:{master_id}:today",
                ),
                InlineKeyboardButton(
                    text=f"{'🔘' if p == 'week' else '⚪️'} Неделя",
                    callback_data=f"mgr:stats:{master_id}:week",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{'🔘' if p == 'month' else '⚪️'} Месяц",
                    callback_data=f"mgr:stats:{master_id}:month",
                ),
                InlineKeyboardButton(
                    text=f"{'🔘' if p == 'all' else '⚪️'} За всё время",
                    callback_data=f"mgr:stats:{master_id}:all",
                ),
            ],
            [InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")],
        ]
    )


def finances_period_keyboard(master_id: int, current_period: str = "month") -> InlineKeyboardMarkup:
    """Period selector for master revenue and booking finances."""
    p = current_period.lower()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'🔘' if p == 'today' else '⚪️'} Сегодня",
                    callback_data=f"mgr:finances:{master_id}:today",
                ),
                InlineKeyboardButton(
                    text=f"{'🔘' if p == 'week' else '⚪️'} Неделя",
                    callback_data=f"mgr:finances:{master_id}:week",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{'🔘' if p == 'month' else '⚪️'} Месяц",
                    callback_data=f"mgr:finances:{master_id}:month",
                ),
                InlineKeyboardButton(
                    text=f"{'🔘' if p == 'all' else '⚪️'} За всё время",
                    callback_data=f"mgr:finances:{master_id}:all",
                ),
            ],
            [InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")],
        ]
    )


def reviews_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Actions on reviews screen."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить отзывы", callback_data=f"mgr:reviews:{master_id}")],
            [InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")],
        ]
    )


def manager_contacts_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Manager Bot contacts configuration keyboard."""
    from app.services.master_contacts import CONTACT_FIELD_LABELS

    buttons = []
    for field, label in CONTACT_FIELD_LABELS.items():
        buttons.append([
            InlineKeyboardButton(
                text=f"{label} — изменить",
                callback_data=f"mgr:contact:edit:{master_id}:{field}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def manager_contact_cancel_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Cancel contact editing."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mgr:contacts:{master_id}")]
        ]
    )


# ---------------------------------------------------------------------------
# Phase 4: Product Polish, Onboarding & Master Features Keyboards
# ---------------------------------------------------------------------------

ACTIVITY_TYPE_NAMES = {
    "nails": "💅 Маникюр",
    "lashes": "👁 Ресницы",
    "brows": "✨ Брови",
    "barber": "💇 Барбер",
    "hair": "💇‍♀️ Парикмахер",
    "makeup": "💄 Визаж",
    "cosmetology": "🧴 Косметология",
    "other": "📋 Другое",
}


def onboarding_activity_type_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Activity type picker during onboarding."""
    buttons = [
        [
            InlineKeyboardButton(text="💅 Маникюр", callback_data=f"mgr:ob:act:{master_id}:nails"),
            InlineKeyboardButton(text="👁 Ресницы", callback_data=f"mgr:ob:act:{master_id}:lashes"),
        ],
        [
            InlineKeyboardButton(text="✨ Брови", callback_data=f"mgr:ob:act:{master_id}:brows"),
            InlineKeyboardButton(text="💇 Барбер", callback_data=f"mgr:ob:act:{master_id}:barber"),
        ],
        [
            InlineKeyboardButton(text="💇‍♀️ Парикмахер", callback_data=f"mgr:ob:act:{master_id}:hair"),
            InlineKeyboardButton(text="💄 Визаж", callback_data=f"mgr:ob:act:{master_id}:makeup"),
        ],
        [
            InlineKeyboardButton(text="🧴 Косметология", callback_data=f"mgr:ob:act:{master_id}:cosmetology"),
            InlineKeyboardButton(text="📋 Другое", callback_data=f"mgr:ob:act:{master_id}:other"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="mgr:projects")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def onboarding_skip_keyboard(master_id: int, step: str) -> InlineKeyboardMarkup:
    """Skip button for optional onboarding steps."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏩ Пропустить этот шаг", callback_data=f"mgr:ob:skip:{master_id}:{step}")],
            [InlineKeyboardButton(text="❌ Прервать онбординг", callback_data=f"mgr:master:{master_id}")],
        ]
    )


def onboarding_suggested_service_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Accept or customize suggested service during onboarding."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Добавить предложенную услугу", callback_data=f"mgr:ob:srv:accept:{master_id}")],
            [InlineKeyboardButton(text="✏️ Ввести другое название", callback_data=f"mgr:ob:srv:custom:{master_id}")],
            [InlineKeyboardButton(text="⏩ Настроить услуги позже", callback_data=f"mgr:ob:skip:{master_id}:service")],
        ]
    )


def onboarding_schedule_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Accept standard schedule or skip."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Принять базовый график (Пн–Пт 10:00–19:00)", callback_data=f"mgr:ob:sch:accept:{master_id}")],
            [InlineKeyboardButton(text="⏩ Настроить график позже", callback_data=f"mgr:ob:skip:{master_id}:schedule")],
        ]
    )


def onboarding_completion_keyboard(master_id: int, bot_instance: Optional[BotInstance] = None) -> InlineKeyboardMarkup:
    """Final screen of onboarding with immediate access to bot."""
    buttons = []
    if bot_instance and bot_instance.telegram_username and bot_instance.status == BotInstanceStatus.ACTIVE:
        buttons.append([
            InlineKeyboardButton(
                text="🤖 Открыть клиентского бота",
                url=f"https://t.me/{bot_instance.telegram_username}",
            )
        ])
    elif bot_instance and bot_instance.status == BotInstanceStatus.SETUP_REQUIRED and bot_instance.telegram_username:
        buttons.append([
            InlineKeyboardButton(
                text="🔗 Открыть бота (Admin)",
                url=f"https://t.me/{bot_instance.telegram_username}?start=admin",
            )
        ])
    else:
        buttons.append([
            InlineKeyboardButton(
                text="🤖 Подключить Telegram-бота",
                callback_data=f"mgr:bot:connect:{master_id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⚙️ Перейти в управление проектом", callback_data=f"mgr:master:{master_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def manager_services_list_keyboard(master_id: int, services: Sequence[Service]) -> InlineKeyboardMarkup:
    """List of services in Manager Bot."""
    buttons = []
    for s in services:
        if s.is_archived:
            continue
        status_icon = "🟢" if s.is_active else "🔴"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {s.title} • {int(s.price)} ₽ ({s.duration_min} мин)",
                callback_data=f"mgr:srv:card:{master_id}:{s.id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить услугу", callback_data=f"mgr:srv:add:{master_id}")
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def manager_service_detail_keyboard(master_id: int, service: Service) -> InlineKeyboardMarkup:
    """Manage single service actions."""
    toggle_text = "🔴 Выключить услугу" if service.is_active else "🟢 Включить услугу"
    dep_text = f"💳 Предоплата: {int(service.deposit_value)}%" if service.deposit_type == DepositType.PERCENT else f"💳 Предоплата: {int(service.deposit_value)} ₽"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Название", callback_data=f"mgr:srv:edit:{master_id}:{service.id}:title"),
                InlineKeyboardButton(text="💰 Цена", callback_data=f"mgr:srv:edit:{master_id}:{service.id}:price"),
            ],
            [
                InlineKeyboardButton(text="⏱ Длительность", callback_data=f"mgr:srv:edit:{master_id}:{service.id}:duration"),
                InlineKeyboardButton(text="⏳ Буфер", callback_data=f"mgr:srv:edit:{master_id}:{service.id}:buffer"),
            ],
            [
                InlineKeyboardButton(text=dep_text, callback_data=f"mgr:srv:edit:{master_id}:{service.id}:deposit"),
            ],
            [
                InlineKeyboardButton(text=toggle_text, callback_data=f"mgr:srv:toggle:{master_id}:{service.id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"mgr:srv:delete:{master_id}:{service.id}"),
            ],
            [
                InlineKeyboardButton(text="⬅️ К списку услуг", callback_data=f"mgr:services:{master_id}")
            ],
        ]
    )


def manager_portfolio_categories_keyboard(
    master_id: int, categories: Sequence[PortfolioCategory]
) -> InlineKeyboardMarkup:
    """List of portfolio categories in Manager Bot."""
    buttons = []
    for cat in categories:
        count = len(cat.items) if hasattr(cat, "items") and cat.items is not None else 0
        buttons.append([
            InlineKeyboardButton(
                text=f"📁 {cat.title} ({count} фото)",
                callback_data=f"mgr:port:cat:{master_id}:{cat.id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить категорию", callback_data=f"mgr:port:cat:add:{master_id}")
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def manager_portfolio_items_keyboard(
    master_id: int, category_id: int, items: Sequence[PortfolioItem]
) -> InlineKeyboardMarkup:
    """Items inside category."""
    buttons = []
    for item in items:
        title = item.title or (item.caption[:25] + "..." if item.caption and len(item.caption) > 25 else item.caption) or f"Фото #{item.id}"
        buttons.append([
            InlineKeyboardButton(
                text=f"🖼 {title}",
                callback_data=f"mgr:port:item:{master_id}:{item.id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить фото", callback_data=f"mgr:port:item:add:{master_id}:{category_id}")
    ])
    buttons.append([
        InlineKeyboardButton(text="🗑 Удалить категорию", callback_data=f"mgr:port:cat:del:{master_id}:{category_id}"),
        InlineKeyboardButton(text="⬅️ К категориям", callback_data=f"mgr:portfolio:{master_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def manager_portfolio_item_detail_keyboard(
    master_id: int, item_id: int, category_id: int
) -> InlineKeyboardMarkup:
    """Portfolio item detail actions."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить это фото", callback_data=f"mgr:port:item:del:{master_id}:{item_id}")],
            [InlineKeyboardButton(text="⬅️ Назад в категорию", callback_data=f"mgr:port:cat:{master_id}:{category_id}")],
        ]
    )


def manager_schedule_menu_keyboard(
    master_id: int, settings_obj: Optional[MasterSettings] = None
) -> InlineKeyboardMarkup:
    """Schedule settings menu."""
    adv_h = settings_obj.min_advance_hours if settings_obj else 2
    horiz_d = settings_obj.booking_horizon_days if settings_obj else 30
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🕒 Рабочие дни и часы", callback_data=f"mgr:sch:hours:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="☕️ Перерывы", callback_data=f"mgr:sch:breaks:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="🏖 Добавить выходной / отпуск", callback_data=f"mgr:sch:dayoff:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="➕ Добавить отдельный рабочий день", callback_data=f"mgr:sch:workdate:{master_id}"),
            ],
            [
                InlineKeyboardButton(text=f"⏱ Минимум за {adv_h} ч.", callback_data=f"mgr:sch:advance:{master_id}"),
                InlineKeyboardButton(text=f"📆 Горизонт {horiz_d} дн.", callback_data=f"mgr:sch:horizon:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")
            ],
        ]
    )


def manager_settings_menu_keyboard(
    master_id: int, settings_obj: Optional[MasterSettings] = None
) -> InlineKeyboardMarkup:
    """Master project settings menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏷 Название проекта", callback_data=f"mgr:set:name:{master_id}"),
                InlineKeyboardButton(text="📝 Описание / О себе", callback_data=f"mgr:set:about:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="📞 Контакты студии", callback_data=f"mgr:contacts:{master_id}"),
                InlineKeyboardButton(text="📅 Расписание", callback_data=f"mgr:schedule:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="💅 Услуги и прайс", callback_data=f"mgr:services:{master_id}"),
                InlineKeyboardButton(text="🖼 Портфолио", callback_data=f"mgr:portfolio:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"mgr:set:notif:{master_id}"),
                InlineKeyboardButton(text="💳 Предоплата", callback_data=f"mgr:set:prepay:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="🤖 Настройки бота", callback_data=f"mgr:master:{master_id}"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад к проекту", callback_data=f"mgr:master:{master_id}")
            ],
        ]
    )


def manager_notification_settings_keyboard(
    master_id: int, reminder_24h: bool, reminder_3h: bool
) -> InlineKeyboardMarkup:
    """Toggle 24h / 3h reminders."""
    t24 = "🟢 Напоминание за 24 ч.: ВКЛ" if reminder_24h else "🔴 Напоминание за 24 ч.: ВЫКЛ"
    t3 = "🟢 Напоминание за 3 ч.: ВКЛ" if reminder_3h else "🔴 Напоминание за 3 ч.: ВЫКЛ"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t24, callback_data=f"mgr:notif:toggle:{master_id}:24h")],
            [InlineKeyboardButton(text=t3, callback_data=f"mgr:notif:toggle:{master_id}:3h")],
            [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data=f"mgr:settings:{master_id}")],
        ]
    )


def manager_prepayment_settings_keyboard(
    master_id: int, settings_obj: Optional[MasterSettings] = None
) -> InlineKeyboardMarkup:
    """Prepayment rules and tenant-owned payment requisites."""
    cancel_h = settings_obj.cancel_policy_hours if settings_obj else 24
    hold_m = settings_obj.hold_duration_minutes if settings_obj else 30
    bank = bool(settings_obj and settings_obj.bank_name and settings_obj.bank_name.strip())
    card = bool(settings_obj and settings_obj.bank_card_number and settings_obj.bank_card_number.strip())
    recipient = bool(settings_obj and settings_obj.bank_recipient_name and settings_obj.bank_recipient_name.strip())
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⏱ Время на оплату чека: {hold_m} мин", callback_data=f"mgr:prepay:hold:{master_id}")],
            [InlineKeyboardButton(text=f"🚫 Бесплатная отмена за: {cancel_h} ч", callback_data=f"mgr:prepay:cancel:{master_id}")],
            [InlineKeyboardButton(text=f"🏦 Банк: {'✅' if bank else 'не задан'}", callback_data=f"mgr:prepay:edit:bank_name:{master_id}")],
            [InlineKeyboardButton(text=f"💳 Карта: {'✅' if card else 'не задана'}", callback_data=f"mgr:prepay:edit:bank_card_number:{master_id}")],
            [InlineKeyboardButton(text=f"👤 Получатель: {'✅' if recipient else 'не задан'}", callback_data=f"mgr:prepay:edit:bank_recipient_name:{master_id}")],
            [InlineKeyboardButton(text="💅 Настроить размер предоплаты в услугах", callback_data=f"mgr:services:{master_id}")],
            [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data=f"mgr:settings:{master_id}")],
        ]
    )


def staff_list_keyboard(
    master_id: int, staff_members: Sequence[StaffMember]
) -> InlineKeyboardMarkup:
    """List of specialists in a master studio."""
    buttons = []
    for s in staff_members:
        status_icon = "🟢" if s.is_active else "⚪️"
        spec_text = f" ({s.specialization})" if s.specialization else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {s.display_name}{spec_text}",
                callback_data=f"mgr:staff:card:{master_id}:{s.id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="➕ Добавить сотрудника",
            callback_data=f"mgr:staff:add:{master_id}",
        )
    ])
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к проекту",
            callback_data=f"mgr:master:{master_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def staff_card_keyboard(
    master_id: int, staff: StaffMember
) -> InlineKeyboardMarkup:
    """Detailed management card for an individual staff member."""
    toggle_text = "⏸ Деактивировать" if staff.is_active else "▶️ Активировать"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Изменить имя",
                    callback_data=f"mgr:staff:edit:name:{master_id}:{staff.id}",
                ),
                InlineKeyboardButton(
                    text="✂️ Специализация",
                    callback_data=f"mgr:staff:edit:spec:{master_id}:{staff.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💅 Настроить услуги мастера",
                    callback_data=f"mgr:staff:services:{master_id}:{staff.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Ссылка-приглашение в Telegram",
                    callback_data=f"mgr:staff:invite:{master_id}:{staff.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=f"mgr:staff:toggle:{master_id}:{staff.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку сотрудников",
                    callback_data=f"mgr:staff:{master_id}",
                )
            ],
        ]
    )


def staff_services_keyboard(
    master_id: int,
    staff_id: int,
    all_services: Sequence[Service],
    assigned_service_ids: Sequence[int],
) -> InlineKeyboardMarkup:
    """Toggle which services are provided by this staff member."""
    assigned_set = set(assigned_service_ids)
    buttons = []
    for svc in all_services:
        is_assigned = svc.id in assigned_set
        icon = "✅" if is_assigned else "⬜️"
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {svc.title} ({svc.price} ₽)",
                callback_data=f"mgr:staff:svc_toggle:{master_id}:{staff_id}:{svc.id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="💾 Готово",
            callback_data=f"mgr:staff:card:{master_id}:{staff_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
