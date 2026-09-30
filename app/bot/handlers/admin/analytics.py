"""
Admin analytics and financial metrics reporting handlers.
Presents income, average ticket, completion rates, no-shows and top services.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdminFilter
from app.bot.keyboards.admin import AdminMenuCallback
from app.services.analytics_service import AnalyticsService
from app.utils.formatters import format_rub

router = Router(name="admin_analytics")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


def get_analytics_keyboard(active_period: str) -> InlineKeyboardMarkup:
    """
    Period switcher keyboard with active state indicator.
    """
    periods = [
        ("today", "🌅 Сегодня"),
        ("month", "📅 Этот месяц"),
        ("prev_month", "🗓 Прошлый месяц"),
        ("all_time", "📈 За всё время"),
    ]

    buttons = []
    row = []
    for key, label in periods:
        text = f"• {label} •" if key == active_period else label
        row.append(
            InlineKeyboardButton(text=text, callback_data=f"adm_an:period:{key}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="◀️ В панель мастера",
            callback_data=AdminMenuCallback(action="dashboard").pack(),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(AdminMenuCallback.filter(F.action == "analytics"))
@router.callback_query(F.data.startswith("adm_an:period:"))
async def cb_admin_analytics_view(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """
    Render executive dashboard for the selected time period.
    """
    await state.clear()
    period = "month"
    if callback.data and callback.data.startswith("adm_an:period:"):
        period = callback.data.split(":")[2]

    analytics_svc = AnalyticsService(session)

    if period == "today":
        metrics = await analytics_svc.get_today_analytics(master_id=1)
    elif period == "prev_month":
        metrics = await analytics_svc.get_previous_month_analytics(master_id=1)
    elif period == "all_time":
        metrics = await analytics_svc.get_all_time_analytics(master_id=1)
    else:
        period = "month"
        metrics = await analytics_svc.get_current_month_analytics(master_id=1)

    text = (
        f"<b>📊 Статистика и аналитика мастера</b>\n"
        f"Период: <b>{metrics['period_name']}</b>\n\n"
        "💰 <b>Финансовые результаты:</b>\n"
        f"• Доход от услуг: <b>{format_rub(metrics['revenue'])}</b>\n"
        f"• Удержанные предоплаты: <b>{format_rub(metrics['retained_deposits'])}</b>\n"
        f"• <b>Общий валовый доход: {format_rub(metrics['total_income'])}</b>\n"
        f"• Средний чек: <b>{format_rub(metrics['avg_ticket'])}</b>\n\n"
        "📈 <b>Показатели визитов:</b>\n"
        f"• Всего записей: <b>{metrics['total']}</b>\n"
        f"• Успешно выполнено: <b>{metrics['completed']}</b> ({metrics['completion_rate']}%)\n"
        f"• Активные / подтверждённые: <b>{metrics['confirmed']}</b>\n"
        f"• Отменено: <b>{metrics['cancelled']}</b>\n"
        f"• Неявок (NO-SHOW): <b>{metrics['no_show']}</b> ({metrics['no_show_rate']}%)\n"
        f"• Уникальных клиентов: <b>{metrics['unique_clients']}</b>\n"
    )

    if metrics["top_services"]:
        text += "\n🏆 <b>Топ услуг по спросу:</b>\n"
        for i, s in enumerate(metrics["top_services"], start=1):
            text += f"{i}. {s['title']} — <b>{s['count']} шт.</b> ({format_rub(s['revenue'])})\n"

    keyboard = get_analytics_keyboard(active_period=period)

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()
