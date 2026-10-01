"""Tenant-scoped Master CRM service for client management, segmentation, reviews and finances."""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
import pytz
from sqlalchemy import case, distinct, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import Master, MasterClient
from app.database.models.review import Review
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_client_repository import MasterClientRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.services.analytics_service import AnalyticsService
from app.services.master_authorization_service import MasterAuthorizationService


class MasterCrmService:
    """Service providing CRM, segmentation, reviews, stats and finances strictly per master_id."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.client_repo = MasterClientRepository(session)
        self.appointment_repo = AppointmentRepository(session)
        self.settings_repo = MasterSettingsRepository(session)
        self.analytics_service = AnalyticsService(session)

    async def _get_timezone(self, master_id: int) -> pytz.BaseTzInfo:
        tz_str = await self.settings_repo.get_value(master_id, "timezone", settings.timezone)
        return pytz.timezone(tz_str)

    # -------------------------------------------------------------------------
    # 1. CLIENT CARD & PROFILE
    # -------------------------------------------------------------------------
    async def get_client_card(self, master_id: int, user_id: int) -> Dict[str, Any]:
        """Fetch tenant-isolated client card with LTV, stats and favorite service."""
        client = await self.client_repo.get_client(master_id, user_id)
        if client is None:
            # Check if user has any appointment with this master
            has_app = await self.session.scalar(
                select(func.count(Appointment.id)).where(
                    Appointment.master_id == master_id, Appointment.user_id == user_id
                )
            )
            if not has_app:
                raise LookupError("Клиент не найден в базе данного мастера")
            client = await self.client_repo.get_or_create(master_id, user_id)

        user = client.user or await self.session.get(User, user_id)
        if not user:
            raise LookupError("Пользователь не найден")

        # Stats strictly for this master
        stats = await self.client_repo.get_client_crm_stats(master_id, user_id)

        # Favorite service (most completed)
        fav_query = (
            select(Appointment.snapshot_service_title, func.count(Appointment.id).label("cnt"))
            .where(
                Appointment.master_id == master_id,
                Appointment.user_id == user_id,
                Appointment.status == AppointmentStatus.COMPLETED,
            )
            .group_by(Appointment.snapshot_service_title)
            .order_by(func.count(Appointment.id).desc())
            .limit(1)
        )
        fav_res = (await self.session.execute(fav_query)).first()
        favorite_service = f"{fav_res[0]} ({fav_res[1]} раз)" if fav_res else "Нет завершённых визитов"

        return {
            "master_id": master_id,
            "user_id": user_id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": f"{user.first_name or ''} {user.last_name or ''}".strip() or "Клиент",
            "username": user.username,
            "phone": user.phone,
            "notes": client.notes,
            "is_marketing_allowed": client.is_marketing_allowed,
            "is_bot_blocked": client.is_bot_blocked,
            "total_bookings": stats["total_bookings"],
            "completed": stats["completed"],
            "cancelled": stats["cancelled"],
            "no_show": stats["no_show"],
            "total_spent": stats["total_spent"],
            "first_booking_at": stats["first_booking_at"],
            "last_booking_at": stats["last_booking_at"],
            "favorite_service": favorite_service,
        }

    async def update_client_notes(
        self, master_id: int, user_id: int, actor_user_id: int, notes: Optional[str]
    ) -> MasterClient:
        """Update client notes with strict master admin authorization."""
        await MasterAuthorizationService(self.session).require_admin(master_id, actor_user_id)
        client = await self.client_repo.get_or_create(master_id, user_id)
        clean_notes = (notes.strip() if notes else None) or None
        if clean_notes and len(clean_notes) > 2000:
            raise ValueError("Заметка слишком длинная (максимум 2000 символов)")
        client.notes = clean_notes
        await self.session.flush()
        return client

    # -------------------------------------------------------------------------
    # 2. CLIENT SEGMENTATION & SEARCH
    # -------------------------------------------------------------------------
    async def list_clients_by_segment(
        self, master_id: int, segment: str = "all", page: int = 1, page_size: int = 5
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Fetch paginated clients by segment strictly within tenant."""
        now_utc = datetime.now(timezone.utc)
        thirty_days_ago = now_utc - timedelta(days=30)
        sixty_days_ago = now_utc - timedelta(days=60)

        # Base subquery aggregating client visits for this master
        app_subq = (
            select(
                Appointment.user_id.label("uid"),
                func.count(Appointment.id).label("total_cnt"),
                func.count().filter(Appointment.status == AppointmentStatus.COMPLETED).label("completed_cnt"),
                func.min(Appointment.start_time).label("first_visit"),
                func.max(Appointment.start_time).label("last_visit"),
            )
            .where(Appointment.master_id == master_id)
            .group_by(Appointment.user_id)
            .subquery()
        )

        query = (
            select(
                MasterClient,
                User,
                func.coalesce(app_subq.c.total_cnt, 0).label("total_cnt"),
                func.coalesce(app_subq.c.completed_cnt, 0).label("completed_cnt"),
                app_subq.c.first_visit,
                app_subq.c.last_visit,
            )
            .join(User, MasterClient.user_id == User.id)
            .outerjoin(app_subq, app_subq.c.uid == MasterClient.user_id)
            .where(MasterClient.master_id == master_id)
        )

        count_query = (
            select(func.count(MasterClient.id))
            .join(User, MasterClient.user_id == User.id)
            .outerjoin(app_subq, app_subq.c.uid == MasterClient.user_id)
            .where(MasterClient.master_id == master_id)
        )

        seg_lower = segment.lower().strip()
        if seg_lower in ("regular", "⭐ постоянные", "постоянные"):
            # Completed >= 3 visits
            query = query.where(func.coalesce(app_subq.c.completed_cnt, 0) >= 3)
            count_query = count_query.where(func.coalesce(app_subq.c.completed_cnt, 0) >= 3)
        elif seg_lower in ("new", "🆕 новые", "новые"):
            # First visit within last 30 days OR exactly 1 appointment
            query = query.where(
                or_(
                    app_subq.c.first_visit >= thirty_days_ago,
                    func.coalesce(app_subq.c.total_cnt, 0) == 1,
                )
            )
            count_query = count_query.where(
                or_(
                    app_subq.c.first_visit >= thirty_days_ago,
                    func.coalesce(app_subq.c.total_cnt, 0) == 1,
                )
            )
        elif seg_lower in ("inactive30", "inactive", "⏰ давно не были", "давно не были"):
            # At least 1 visit and last visit was > 30 days ago
            query = query.where(
                app_subq.c.last_visit.is_not(None),
                app_subq.c.last_visit < thirty_days_ago,
            )
            count_query = count_query.where(
                app_subq.c.last_visit.is_not(None),
                app_subq.c.last_visit < thirty_days_ago,
            )
        elif seg_lower in ("inactive60", "давно не были 60"):
            # At least 1 visit and last visit was > 60 days ago
            query = query.where(
                app_subq.c.last_visit.is_not(None),
                app_subq.c.last_visit < sixty_days_ago,
            )
            count_query = count_query.where(
                app_subq.c.last_visit.is_not(None),
                app_subq.c.last_visit < sixty_days_ago,
            )

        total_count = (await self.session.scalar(count_query)) or 0
        offset = max(0, (page - 1) * page_size)

        query = query.order_by(
            app_subq.c.last_visit.desc().nullslast(),
            MasterClient.id.desc(),
        ).offset(offset).limit(page_size)

        results = (await self.session.execute(query)).all()
        items = []
        for mc, u, total_cnt, completed_cnt, first_v, last_v in results:
            items.append({
                "user_id": u.id,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "full_name": f"{u.first_name or ''} {u.last_name or ''}".strip() or "Клиент",
                "phone": u.phone,
                "username": u.username,
                "total_bookings": total_cnt,
                "completed": completed_cnt,
                "last_visit_at": last_v,
            })

        return items, total_count

    async def search_clients(
        self, master_id: int, query_text: str, page: int = 1, page_size: int = 5
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Search clients by name, phone or username strictly for this master."""
        pattern = f"%{query_text.strip()}%"
        base_filter = [
            MasterClient.master_id == master_id,
            or_(
                User.first_name.ilike(pattern),
                User.last_name.ilike(pattern),
                User.username.ilike(pattern),
                User.phone.ilike(pattern),
            ),
        ]

        count_query = (
            select(func.count(MasterClient.id))
            .join(User, MasterClient.user_id == User.id)
            .where(*base_filter)
        )
        total_count = (await self.session.scalar(count_query)) or 0

        offset = max(0, (page - 1) * page_size)
        query = (
            select(MasterClient, User)
            .join(User, MasterClient.user_id == User.id)
            .where(*base_filter)
            .order_by(User.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        res = (await self.session.execute(query)).all()
        items = []
        for mc, u in res:
            stats = await self.client_repo.get_client_crm_stats(master_id, u.id)
            items.append({
                "user_id": u.id,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "full_name": f"{u.first_name or ''} {u.last_name or ''}".strip() or "Клиент",
                "phone": u.phone,
                "username": u.username,
                "total_bookings": stats["total_bookings"],
                "completed": stats["completed"],
                "last_visit_at": stats["last_booking_at"],
            })
        return items, total_count

    # -------------------------------------------------------------------------
    # 3. CLIENT APPOINTMENT HISTORY
    # -------------------------------------------------------------------------
    async def get_client_history(
        self, master_id: int, user_id: int, page: int = 1, page_size: int = 5
    ) -> Tuple[List[Appointment], int]:
        """Get paginated appointments history for client strictly scoped to master_id."""
        count_query = select(func.count(Appointment.id)).where(
            Appointment.master_id == master_id, Appointment.user_id == user_id
        )
        total_count = (await self.session.scalar(count_query)) or 0

        offset = max(0, (page - 1) * page_size)
        query = (
            select(Appointment)
            .where(Appointment.master_id == master_id, Appointment.user_id == user_id)
            .order_by(Appointment.start_time.desc())
            .offset(offset)
            .limit(page_size)
        )
        res = await self.session.execute(query)
        return list(res.scalars().all()), total_count

    # -------------------------------------------------------------------------
    # 4. REVIEWS & RATINGS
    # -------------------------------------------------------------------------
    async def create_review(
        self,
        master_id: int,
        user_id: int,
        appointment_id: int,
        rating: int,
        comment: Optional[str] = None,
    ) -> Review:
        """Create a client review for a completed appointment with strict tenant check."""
        if not (1 <= rating <= 5):
            raise ValueError("Оценка должна быть от 1 до 5 звезд")

        # Verify appointment exists, is completed, and belongs to user & master
        app = await self.appointment_repo.get_by_id_with_relations(appointment_id, master_id=master_id)
        if not app or app.user_id != user_id:
            raise LookupError("Запись не найдена или принадлежит другому пользователю")
        if app.status != AppointmentStatus.COMPLETED:
            raise ValueError("Оставить отзыв можно только после завершения визита")

        # Check unique constraint on appointment_id
        existing = await self.session.scalar(
            select(Review).where(Review.appointment_id == appointment_id)
        )
        if existing:
            raise ValueError("Отзыв к этой записи уже оставлен")

        clean_comment = (comment.strip() if comment else None) or None
        if clean_comment and len(clean_comment) > 2000:
            raise ValueError("Комментарий слишком длинный (максимум 2000 символов)")

        review = Review(
            master_id=master_id,
            user_id=user_id,
            appointment_id=appointment_id,
            rating=rating,
            comment=clean_comment,
        )
        self.session.add(review)
        await self.session.flush()
        return review

    async def get_reviews_summary(
        self, master_id: int, limit: int = 5
    ) -> Dict[str, Any]:
        """Aggregate review ratings and fetch recent reviews for master."""
        agg_query = (
            select(
                func.count(Review.id).label("total_count"),
                func.coalesce(func.avg(Review.rating), 0.0).label("avg_rating"),
                func.count().filter(Review.rating == 5).label("stars_5"),
                func.count().filter(Review.rating == 4).label("stars_4"),
                func.count().filter(Review.rating == 3).label("stars_3"),
                func.count().filter(Review.rating == 2).label("stars_2"),
                func.count().filter(Review.rating == 1).label("stars_1"),
            )
            .where(Review.master_id == master_id)
        )
        agg_res = (await self.session.execute(agg_query)).one()

        recent_query = (
            select(Review, User)
            .join(User, Review.user_id == User.id)
            .where(Review.master_id == master_id)
            .order_by(Review.created_at.desc())
            .limit(limit)
        )
        recent_rows = (await self.session.execute(recent_query)).all()

        recent_reviews = [
            {
                "id": r.id,
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at,
                "client_name": f"{u.first_name or ''} {u.last_name or ''}".strip() or "Клиент",
            }
            for r, u in recent_rows
        ]

        return {
            "total_count": agg_res.total_count or 0,
            "avg_rating": round(float(agg_res.avg_rating or 0.0), 2),
            "stars_5": agg_res.stars_5 or 0,
            "stars_4": agg_res.stars_4 or 0,
            "stars_3": agg_res.stars_3 or 0,
            "stars_2": agg_res.stars_2 or 0,
            "stars_1": agg_res.stars_1 or 0,
            "recent_reviews": recent_reviews,
        }

    # -------------------------------------------------------------------------
    # 5. MASTER STATISTICS (OPERATIONAL)
    # -------------------------------------------------------------------------
    async def get_master_statistics(self, master_id: int, period: str = "month") -> Dict[str, Any]:
        """Aggregate operational metrics (bookings, statuses, occupancy, new vs returning)."""
        tz = await self._get_timezone(master_id)
        now_local = datetime.now(tz)
        today = now_local.date()

        start_dt: Optional[datetime] = None
        end_dt: Optional[datetime] = None
        period_title = "За всё время"

        p = period.lower().strip()
        if p == "today":
            start_dt = tz.localize(datetime.combine(today, time.min))
            end_dt = tz.localize(datetime.combine(today, time.max))
            period_title = f"Сегодня ({today.strftime('%d.%m.%Y')})"
        elif p == "week":
            start_week = today - timedelta(days=today.weekday())
            end_week = start_week + timedelta(days=6)
            start_dt = tz.localize(datetime.combine(start_week, time.min))
            end_dt = tz.localize(datetime.combine(end_week, time.max))
            period_title = f"Текущая неделя ({start_week.strftime('%d.%m')} - {end_week.strftime('%d.%m.%Y')})"
        elif p == "month":
            first_day = today.replace(day=1)
            next_month = (first_day + timedelta(days=32)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            start_dt = tz.localize(datetime.combine(first_day, time.min))
            end_dt = tz.localize(datetime.combine(last_day, time.max))
            period_title = f"Текущий месяц ({today.strftime('%m.%Y')})"

        # Base metrics from AnalyticsService
        metrics = await self.analytics_service.get_metrics_for_range(master_id, start_dt, end_dt)

        # Calculate new vs returning clients in this period
        # A client is "new" if their very first appointment with this master occurred in this period
        first_app_subq = (
            select(
                Appointment.user_id,
                func.min(Appointment.start_time).label("first_time"),
            )
            .where(Appointment.master_id == master_id)
            .group_by(Appointment.user_id)
            .subquery()
        )

        new_clients_query = (
            select(func.count(distinct(Appointment.user_id)))
            .join(first_app_subq, Appointment.user_id == first_app_subq.c.user_id)
            .where(Appointment.master_id == master_id)
        )
        if start_dt:
            new_clients_query = new_clients_query.where(
                Appointment.start_time >= start_dt,
                first_app_subq.c.first_time >= start_dt,
            )
        if end_dt:
            new_clients_query = new_clients_query.where(
                Appointment.start_time <= end_dt,
                first_app_subq.c.first_time <= end_dt,
            )

        new_clients_cnt = (await self.session.scalar(new_clients_query)) or 0
        total_unique = metrics["unique_clients"]
        returning_clients_cnt = max(0, total_unique - new_clients_cnt)

        # Schedule occupancy rate (completed + confirmed / total bookings if total > 0)
        active_bookings = metrics["completed"] + metrics["confirmed"]
        total_bookings = metrics["total"]
        occupancy_rate = round((active_bookings / total_bookings * 100), 1) if total_bookings > 0 else 0.0

        return {
            "period": p,
            "period_title": period_title,
            "total_bookings": total_bookings,
            "completed": metrics["completed"],
            "confirmed": metrics["confirmed"],
            "cancelled": metrics["cancelled"],
            "no_show": metrics["no_show"],
            "unique_clients": total_unique,
            "new_clients": new_clients_cnt,
            "returning_clients": returning_clients_cnt,
            "occupancy_rate": occupancy_rate,
            "completion_rate": metrics["completion_rate"],
            "no_show_rate": metrics["no_show_rate"],
        }

    # -------------------------------------------------------------------------
    # 6. MASTER FINANCES (BUSINESS REVENUE)
    # -------------------------------------------------------------------------
    async def get_master_finances(self, master_id: int, period: str = "month") -> Dict[str, Any]:
        """Aggregate master service revenue from client bookings (strictly isolated from SaaS billing)."""
        tz = await self._get_timezone(master_id)
        now_local = datetime.now(tz)
        today = now_local.date()

        start_dt: Optional[datetime] = None
        end_dt: Optional[datetime] = None
        period_title = "За всё время"

        p = period.lower().strip()
        if p == "today":
            start_dt = tz.localize(datetime.combine(today, time.min))
            end_dt = tz.localize(datetime.combine(today, time.max))
            period_title = f"Сегодня ({today.strftime('%d.%m.%Y')})"
        elif p == "week":
            start_week = today - timedelta(days=today.weekday())
            end_week = start_week + timedelta(days=6)
            start_dt = tz.localize(datetime.combine(start_week, time.min))
            end_dt = tz.localize(datetime.combine(end_week, time.max))
            period_title = f"Текущая неделя ({start_week.strftime('%d.%m')} - {end_week.strftime('%d.%m.%Y')})"
        elif p == "month":
            first_day = today.replace(day=1)
            next_month = (first_day + timedelta(days=32)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            start_dt = tz.localize(datetime.combine(first_day, time.min))
            end_dt = tz.localize(datetime.combine(last_day, time.max))
            period_title = f"Текущий месяц ({today.strftime('%m.%Y')})"

        metrics = await self.analytics_service.get_metrics_for_range(master_id, start_dt, end_dt)

        return {
            "period": p,
            "period_title": period_title,
            "revenue": metrics["revenue"],
            "retained_deposits": metrics["retained_deposits"],
            "total_income": metrics["total_income"],
            "completed_count": metrics["completed"],
            "avg_ticket": metrics["avg_ticket"],
            "top_services": metrics["top_services"],
        }
