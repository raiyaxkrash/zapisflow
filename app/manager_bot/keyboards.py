"""Inline keyboards for platform Manager Bot."""

from collections.abc import Sequence
from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config.settings import settings
from app.database.models.master import BotInstance, BotInstanceStatus, Master
from app.database.models.subscription import EffectiveSubscriptionStatus, SubscriptionPlan


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu of Manager Bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Мои проекты", callback_data="mgr:projects")],
            [InlineKeyboardButton(text="➕ Создать проект", callback_data="mgr:master:new")],
            [
                InlineKeyboardButton(text="💳 Подписка", callback_data="mgr:sub:menu"),
                InlineKeyboardButton(text="🆘 Поддержка", url=settings.support_url),
            ],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="mgr:help")],
        ]
    )


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

    # Subscription management
    rows.append([
        InlineKeyboardButton(
            text="💳 Подписка и тариф",
            callback_data=f"mgr:sub:{master.id}",
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


def subscription_checkout_keyboard(master_id: int, url: str) -> InlineKeyboardMarkup:
    """Provider-confirmed redirect returned by YooKassa for this order."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить подписку", url=url)],
        [InlineKeyboardButton(text="🆘 Поддержка", url=settings.support_url)],
        [InlineKeyboardButton(text="⬅️ К подписке", callback_data=f"mgr:sub:{master_id}")],
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


