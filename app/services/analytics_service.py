"""
Analytics and financial statistics service for master performance metrics.
Computes revenues, average check, completion rates, no-shows, and service popularities.
"""

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
import pytz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import Payment, PaymentStatus
from app.repositories.settings_repository import SettingsRepository


class AnalyticsService:
    """
    Service for calculating financial, operational and client retention analytics.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings_repo = SettingsRepository(session)

    async def _get_timezone(self) -> pytz.BaseTzInfo:
        tz_str = await self.settings_repo.get_value("timezone", settings.timezone)
        return pytz.timezone(tz_str)

    async def get_metrics_for_range(
        self,
        master_id: int = 1,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Calculate aggregated metrics for a specific datetime range.
        """
        # 1. Base appointment query
        conditions = [Appointment.master_id == master_id]
        if start_dt:
            conditions.append(Appointment.start_time >= start_dt)
        if end_dt:
            conditions.append(Appointment.start_time <= end_dt)

        query = (
            select(
                func.count(Appointment.id).label("total"),
                func.count().filter(Appointment.status == AppointmentStatus.COMPLETED).label("completed"),
                func.count().filter(Appointment.status == AppointmentStatus.CONFIRMED).label("confirmed"),
                func.count().filter(
                    Appointment.status.in_([
                        AppointmentStatus.CANCELLED_BY_CLIENT,
                        AppointmentStatus.CANCELLED_BY_ADMIN,
                    ])
                ).label("cancelled"),
                func.count().filter(Appointment.status == AppointmentStatus.NO_SHOW).label("no_show"),
                func.coalesce(
                    func.sum(Appointment.snapshot_service_price).filter(
                        Appointment.status == AppointmentStatus.COMPLETED
                    ),
                    Decimal("0.00"),
                ).label("revenue"),
                func.count(func.distinct(Appointment.user_id)).label("unique_clients"),
            )
            .where(*conditions)
        )

        res = await self.session.execute(query)
        row = res.one()

        total = row.total or 0
        completed = row.completed or 0
        confirmed = row.confirmed or 0
        cancelled = row.cancelled or 0
        no_show = row.no_show or 0
        revenue = row.revenue or Decimal("0.00")
        unique_clients = row.unique_clients or 0

        avg_ticket = (revenue / Decimal(completed)).quantize(Decimal("1.00")) if completed > 0 else Decimal("0.00")
        completion_rate = (completed / total * 100) if total > 0 else 0.0
        no_show_rate = (no_show / total * 100) if total > 0 else 0.0

        # 2. Top services in period
        svc_query = (
            select(
                Appointment.snapshot_service_title.label("title"),
                func.count(Appointment.id).label("count"),
                func.coalesce(
                    func.sum(Appointment.snapshot_service_price).filter(
                        Appointment.status == AppointmentStatus.COMPLETED
                    ),
                    Decimal("0.00"),
                ).label("svc_revenue"),
            )
            .where(*conditions)
            .group_by(Appointment.snapshot_service_title)
            .order_by(func.count(Appointment.id).desc())
            .limit(5)
        )
        svc_res = await self.session.execute(svc_query)
        top_services: List[Dict[str, Any]] = [
            {
                "title": r.title,
                "count": r.count,
                "revenue": r.svc_revenue or Decimal("0.00"),
            }
            for r in svc_res.all()
        ]

        # 3. Retained deposits from cancellations/no-shows
        dep_query = (
            select(
                func.coalesce(func.sum(Payment.amount), Decimal("0.00")).label("retained_deposits")
            )
            .join(Appointment, Payment.appointment_id == Appointment.id)
            .where(
                Appointment.master_id == master_id,
                Payment.status == PaymentStatus.RETAINED,
                Payment.confirmed_at.is_not(None),
            )
        )
        if start_dt:
            dep_query = dep_query.where(Appointment.start_time >= start_dt)
        if end_dt:
            dep_query = dep_query.where(Appointment.start_time <= end_dt)

        dep_res = await self.session.execute(dep_query)
        retained_deposits = dep_res.scalar() or Decimal("0.00")

        return {
            "total": total,
            "completed": completed,
            "confirmed": confirmed,
            "cancelled": cancelled,
            "no_show": no_show,
            "revenue": revenue,
            "retained_deposits": retained_deposits,
            "total_income": revenue + retained_deposits,
            "avg_ticket": avg_ticket,
            "completion_rate": round(completion_rate, 1),
            "no_show_rate": round(no_show_rate, 1),
            "unique_clients": unique_clients,
            "top_services": top_services,
        }

    async def get_today_analytics(self, master_id: int = 1) -> Dict[str, Any]:
        """
        Analytics for current local day.
        """
        tz = await self._get_timezone()
        now_local = datetime.now(tz)
        today = now_local.date()

        start_dt = tz.localize(datetime.combine(today, time.min))
        end_dt = tz.localize(datetime.combine(today, time.max))

        metrics = await self.get_metrics_for_range(master_id, start_dt, end_dt)
        metrics["period_name"] = f"Сегодня ({today.strftime('%d.%m.%Y')})"
        return metrics

    async def get_current_month_analytics(self, master_id: int = 1) -> Dict[str, Any]:
        """
        Analytics for current local month.
        """
        tz = await self._get_timezone()
        now_local = datetime.now(tz)
        today = now_local.date()

        first_day = today.replace(day=1)
        _, last_day_num = monthrange(today.year, today.month)
        last_day = today.replace(day=last_day_num)

        start_dt = tz.localize(datetime.combine(first_day, time.min))
        end_dt = tz.localize(datetime.combine(last_day, time.max))

        metrics = await self.get_metrics_for_range(master_id, start_dt, end_dt)
        metrics["period_name"] = f"Текущий месяц ({today.strftime('%m.%Y')})"
        return metrics

    async def get_previous_month_analytics(self, master_id: int = 1) -> Dict[str, Any]:
        """
        Analytics for previous local month.
        """
        tz = await self._get_timezone()
        now_local = datetime.now(tz)
        today = now_local.date()

        # Compute previous month
        first_of_this_month = today.replace(day=1)
        last_of_prev_month = first_of_this_month - timedelta(days=1)
        first_of_prev_month = last_of_prev_month.replace(day=1)

        start_dt = tz.localize(datetime.combine(first_of_prev_month, time.min))
        end_dt = tz.localize(datetime.combine(last_of_prev_month, time.max))

        metrics = await self.get_metrics_for_range(master_id, start_dt, end_dt)
        metrics["period_name"] = f"Прошлый месяц ({last_of_prev_month.strftime('%m.%Y')})"
        return metrics

    async def get_all_time_analytics(self, master_id: int = 1) -> Dict[str, Any]:
        """
        All-time total metrics.
        """
        metrics = await self.get_metrics_for_range(master_id, None, None)
        metrics["period_name"] = "За всё время работы"
        return metrics
