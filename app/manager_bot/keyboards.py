"""Inline keyboards for platform Manager Bot."""

from collections.abc import Sequence
from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config.settings import settings
from app.database.models.master import BotInstance, BotInstanceStatus, Master
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
) -> InlineKeyboardMarkup:
    """Action buttons for a single Master project."""
    rows = []

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

    # Business & CRM features
    rows.append([
        InlineKeyboardButton(text="👥 Клиенты CRM", callback_data=f"mgr:crm:{master.id}"),
        InlineKeyboardButton(text="📊 Статистика", callback_data=f"mgr:stats:{master.id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="💰 Финансы", callback_data=f"mgr:finances:{master.id}"),
        InlineKeyboardButton(text="⭐ Отзывы", callback_data=f"mgr:reviews:{master.id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="📞 Контакты", callback_data=f"mgr:contacts:{master.id}"),
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
        settings.payment_provider.lower() == "yookassa_web"
        or (settings.payment_provider.lower() == "manual" and not settings.is_production)
    ) and status != EffectiveSubscriptionStatus.SUSPENDED and can_pay
    for plan in (plans if show_pay_action else ()):
        price_fmt = f"{plan.price:,.2f}".replace(",", " ").removesuffix(".00") + " ₽"
        if settings.payment_provider.lower() == "yookassa_web":
            btn_text = f"💳 Оплатить {price_fmt}"
        elif status == EffectiveSubscriptionStatus.EXPIRED:
            btn_text = f"💳 Оплатить {price_fmt} ({plan.name})"
        elif status == EffectiveSubscriptionStatus.PAID_ACTIVE:
            btn_text = f"💳 Продлить: {plan.name} — {price_fmt}"
        elif status == EffectiveSubscriptionStatus.TRIAL_ACTIVE:
            btn_text = f"💳 Оформить: {plan.name} — {price_fmt}"
        else:
            btn_text = f"💳 {plan.name} — {price_fmt}"

        rows.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"mgr:sub:pay:{master_id}:{plan.code}",
            )
        ])

    if status == EffectiveSubscriptionStatus.SUSPENDED or not show_pay_action:
        rows.append([
            InlineKeyboardButton(
                text="🆘 Написать в поддержку",
                url=settings.support_url,
            )
        ])

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
        [InlineKeyboardButton(text="💳 Перейти к оплате", url=url)],
    ]
    if payment_id is not None:
        rows.append([
            InlineKeyboardButton(
                text="🔄 Проверить оплату",
                callback_data=f"mgr:sub:check:{master_id}:{payment_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="🆘 Поддержка", url=settings.support_url)])
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
        rows.append([InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)])
    rows.append([
        InlineKeyboardButton(
            text="🔄 Проверить оплату",
            callback_data=f"mgr:sub:check:{master_id}:{payment_id}",
        )
    ])
    rows.append([InlineKeyboardButton(text="🆘 Поддержка", url=settings.support_url)])
    rows.append([InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:sub:{master_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_canceled_keyboard(
    master_id: int,
    plan_code: str,
) -> InlineKeyboardMarkup:
    """Canceled payment screen keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить снова", callback_data=f"mgr:sub:pay:{master_id}:{plan_code}")],
        [InlineKeyboardButton(text="🆘 Поддержка", url=settings.support_url)],
        [InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:sub:{master_id}")],
    ])


def subscription_success_keyboard(master_id: int) -> InlineKeyboardMarkup:
    """Success subscription payment screen keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Моя подписка", callback_data=f"mgr:sub:{master_id}")],
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
            [InlineKeyboardButton(text="📈 SaaS Аналитика", callback_data="mgr:admin:metrics")],
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
            [InlineKeyboardButton(text="📈 SaaS Аналитика", callback_data="mgr:admin:metrics")],
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
    """User detail view actions."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К списку пользователей", callback_data="mgr:admin:users")]
        ]
    )


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
            [InlineKeyboardButton(text=action_text, callback_data=f"mgr:admin:project:suspend:{master_id}")],
            [InlineKeyboardButton(text="⬅️ К списку проектов", callback_data="mgr:admin:projects")],
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить webhook", callback_data=f"mgr:admin:bot:webhook:{bot_id}")],
            [InlineKeyboardButton(text=toggle_text, callback_data=f"mgr:admin:bot:toggle:{bot_id}")],
            [InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"mgr:admin:bot:delete:{bot_id}")],
            [InlineKeyboardButton(text="⬅️ К списку ботов", callback_data="mgr:admin:bots")],
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
                callback_data=f"mgr:admin:project:{s['master_id']}",
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
    toggle_text = "🔴 Деактивировать тариф" if is_active else "🟢 Активировать тариф"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f"mgr:admin:plan:toggle:{plan_id}")],
            [InlineKeyboardButton(text="⬅️ К тарифам", callback_data="mgr:admin:plans")],
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
