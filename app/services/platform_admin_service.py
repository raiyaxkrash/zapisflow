"""Platform Admin Service for system-wide monitoring, metrics, and administration."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.settings import settings
from app.database.models.appointment import Appointment
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterClient,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.subscription import (
    SubscriptionPayment,
    SubscriptionPeriod,
    SubscriptionPlan,
)
from app.database.models.user import Admin, User
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.services.audit_service import AuditEvent, AuditService
from app.services.bot_registry import BotRegistry
from app.services.telegram_provisioning_gateway import TelegramProvisioningGateway

logger = logging.getLogger(__name__)


class PlatformAdminService:
    """Provides centralized operational statistics, multi-tenant listings, and admin actions."""

    def __init__(
        self,
        session: AsyncSession,
        registry: Optional[BotRegistry] = None,
        gateway: Optional[TelegramProvisioningGateway] = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.gateway = gateway or TelegramProvisioningGateway()
        self.audit_service = AuditService(session)
        self.master_repo = MasterRepository(session)
        self.bot_repo = BotInstanceRepository(session)

    async def is_platform_admin(self, telegram_id: int, user_id: Optional[int] = None) -> bool:
        """Verify if a user has platform-level administrative privileges."""
        if telegram_id in settings.admin_ids:
            return True

        query = select(Admin).join(User, Admin.user_id == User.id).where(
            User.telegram_id == telegram_id,
            Admin.is_active.is_(True),
        )
        res = await self.session.execute(query)
        if res.scalars().first() is not None:
            return True

        if user_id is not None:
            admin_query = select(Admin).where(
                Admin.user_id == user_id,
                Admin.is_active.is_(True),
            )
            admin_res = await self.session.execute(admin_query)
            if admin_res.scalars().first() is not None:
                return True

        return False

    async def get_dashboard_stats(self) -> Dict[str, Any]:
        """Aggregate high-level platform health and subscription KPIs."""
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        # Basic counts
        total_users = await self.session.scalar(select(func.count(User.id))) or 0
        total_masters = await self.session.scalar(select(func.count(Master.id))) or 0

        active_bots = await self.session.scalar(
            select(func.count(BotInstance.id)).where(
                BotInstance.status == BotInstanceStatus.ACTIVE,
                BotInstance.is_current.is_(True),
            )
        ) or 0

        trial_masters = await self.session.scalar(
            select(func.count(Master.id)).where(Master.subscription_status == SubscriptionStatus.TRIAL)
        ) or 0

        paid_masters = await self.session.scalar(
            select(func.count(Master.id)).where(Master.subscription_status == SubscriptionStatus.ACTIVE)
        ) or 0

        expired_masters = await self.session.scalar(
            select(func.count(Master.id)).where(Master.subscription_status == SubscriptionStatus.EXPIRED)
        ) or 0

        new_users_7d = await self.session.scalar(
            select(func.count(User.id)).where(User.first_seen_at >= week_ago)
        ) or 0

        # Calculate MRR from active paid masters
        default_plan_price = await self.session.scalar(
            select(SubscriptionPlan.price)
            .where(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.sort_order.asc())
            .limit(1)
        ) or Decimal("499.00")

        # Sum latest active subscription period prices or multiply paid_masters * base price
        mrr = Decimal(paid_masters) * default_plan_price

        # Conversion: paid out of (paid + expired)
        total_evaluated = paid_masters + expired_masters
        conversion_rate = (
            round((paid_masters / total_evaluated) * 100, 1) if total_evaluated > 0 else 0.0
        )

        return {
            "total_users": total_users,
            "total_masters": total_masters,
            "active_bots": active_bots,
            "trial_masters": trial_masters,
            "paid_masters": paid_masters,
            "expired_masters": expired_masters,
            "mrr": mrr,
            "new_users_7d": new_users_7d,
            "conversion_rate": conversion_rate,
        }

    async def get_saas_metrics(self) -> Dict[str, Any]:
        """Compute advanced business and SaaS growth metrics."""
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        stats = await self.get_dashboard_stats()
        mrr = stats["mrr"]
        arr = mrr * 12
        paid_masters = stats["paid_masters"]

        arpu = round(mrr / paid_masters, 2) if paid_masters > 0 else Decimal("0.00")

        new_users_30d = await self.session.scalar(
            select(func.count(User.id)).where(User.first_seen_at >= month_ago)
        ) or 0

        new_projects_7d = await self.session.scalar(
            select(func.count(Master.id)).where(Master.created_at >= week_ago)
        ) or 0

        new_projects_30d = await self.session.scalar(
            select(func.count(Master.id)).where(Master.created_at >= month_ago)
        ) or 0

        total_revenue = await self.session.scalar(
            select(func.coalesce(func.sum(SubscriptionPayment.amount), Decimal("0.00"))).where(
                SubscriptionPayment.status == "SUCCEEDED"
            )
        ) or Decimal("0.00")

        total_successful_payments = await self.session.scalar(
            select(func.count(SubscriptionPayment.id)).where(
                SubscriptionPayment.status == "SUCCEEDED"
            )
        ) or 0

        return {
            **stats,
            "arr": arr,
            "arpu": arpu,
            "new_users_30d": new_users_30d,
            "new_projects_7d": new_projects_7d,
            "new_projects_30d": new_projects_30d,
            "total_revenue": total_revenue,
            "total_successful_payments": total_successful_payments,
        }

    async def list_users(
        self, page: int = 1, per_page: int = 5
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Paginated list of platform users."""
        total = await self.session.scalar(select(func.count(User.id))) or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)

        query = (
            select(User)
            .order_by(desc(User.first_seen_at))
            .offset(offset)
            .limit(per_page)
        )
        res = await self.session.execute(query)
        users = res.scalars().all()

        items = []
        for u in users:
            proj_count = await self.session.scalar(
                select(func.count(Master.id)).where(Master.owner_user_id == u.id)
            ) or 0
            items.append({
                "id": u.id,
                "telegram_id": u.telegram_id,
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "phone": u.phone,
                "first_seen_at": u.first_seen_at,
                "projects_count": proj_count,
            })

        return items, total, total_pages

    async def get_user_details(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Fetch comprehensive user overview with owned projects."""
        user = await self.session.get(User, user_id)
        if not user:
            return None

        masters_query = select(Master).where(Master.owner_user_id == user.id).order_by(Master.id.asc())
        masters_res = await self.session.execute(masters_query)
        masters = masters_res.scalars().all()

        is_admin = await self.is_platform_admin(user.telegram_id, user.id)

        projects_data = []
        for m in masters:
            bot = await self.bot_repo.get_current_for_master(m.id)
            projects_data.append({
                "id": m.id,
                "display_name": m.display_name,
                "status": m.status.value,
                "subscription_status": m.subscription_status.value,
                "bot_username": bot.telegram_username if bot else None,
            })

        return {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "first_seen_at": user.first_seen_at,
            "last_activity_at": user.last_activity_at,
            "is_platform_admin": is_admin,
            "projects": projects_data,
        }

    async def list_projects(
        self, page: int = 1, per_page: int = 5
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Paginated list of master projects across all tenants."""
        total = await self.session.scalar(select(func.count(Master.id))) or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)

        query = (
            select(Master)
            .options(selectinload(Master.owner))
            .order_by(desc(Master.created_at))
            .offset(offset)
            .limit(per_page)
        )
        res = await self.session.execute(query)
        masters = res.scalars().all()

        items = []
        for m in masters:
            bot = await self.bot_repo.get_current_for_master(m.id)
            clients_cnt = await self.session.scalar(
                select(func.count(MasterClient.id)).where(MasterClient.master_id == m.id)
            ) or 0
            appts_cnt = await self.session.scalar(
                select(func.count(Appointment.id)).where(Appointment.master_id == m.id)
            ) or 0

            items.append({
                "id": m.id,
                "display_name": m.display_name,
                "owner_id": m.owner_user_id,
                "owner_name": f"{m.owner.first_name} (@{m.owner.username})" if m.owner and m.owner.username else (m.owner.first_name if m.owner else "-"),
                "status": m.status.value,
                "subscription_status": m.subscription_status.value,
                "paid_until": m.paid_until,
                "trial_ends_at": m.trial_ends_at,
                "bot_username": bot.telegram_username if bot else None,
                "bot_status": bot.status.value if bot else None,
                "clients_count": clients_cnt,
                "appointments_count": appts_cnt,
                "created_at": m.created_at,
            })

        return items, total, total_pages

    async def get_project_details(self, master_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve complete project status and tenant summary."""
        master = await self.master_repo.get_by_id(master_id)
        if not master:
            return None

        owner = await self.session.get(User, master.owner_user_id)
        bot = await self.bot_repo.get_current_for_master(master.id)

        clients_cnt = await self.session.scalar(
            select(func.count(MasterClient.id)).where(MasterClient.master_id == master.id)
        ) or 0
        appts_cnt = await self.session.scalar(
            select(func.count(Appointment.id)).where(Appointment.master_id == master.id)
        ) or 0

        # Last activity timestamp
        last_appt_created = await self.session.scalar(
            select(func.max(Appointment.created_at)).where(Appointment.master_id == master.id)
        )

        return {
            "id": master.id,
            "display_name": master.display_name,
            "status": master.status.value,
            "subscription_status": master.subscription_status.value,
            "timezone": master.timezone,
            "trial_ends_at": master.trial_ends_at,
            "paid_until": master.paid_until,
            "created_at": master.created_at,
            "updated_at": master.updated_at,
            "owner": {
                "id": owner.id if owner else None,
                "telegram_id": owner.telegram_id if owner else None,
                "username": owner.username if owner else None,
                "first_name": owner.first_name if owner else None,
            },
            "bot": {
                "id": bot.id if bot else None,
                "telegram_bot_id": bot.telegram_bot_id if bot else None,
                "username": bot.telegram_username if bot else None,
                "status": bot.status.value if bot else None,
                "last_error": bot.last_error if bot else None,
            },
            "clients_count": clients_cnt,
            "appointments_count": appts_cnt,
            "last_activity": last_appt_created or master.updated_at,
        }

    async def toggle_project_suspension(
        self, master_id: int, actor_user_id: int
    ) -> Tuple[bool, str]:
        """Suspend or unsuspend a master project."""
        master = await self.master_repo.get_by_id(master_id)
        if not master:
            return False, "Проект не найден."

        if master.status == MasterStatus.SUSPENDED:
            master.status = MasterStatus.ACTIVE
            action = AuditEvent.PROJECT_ACTIVATED
            msg = "Проект активирован."
        else:
            master.status = MasterStatus.SUSPENDED
            action = AuditEvent.PROJECT_SUSPENDED
            msg = "Проект приостановлен."

            # If suspending, invalidate active bot
            bot = await self.bot_repo.get_current_for_master(master_id)
            if bot and self.registry:
                await self.registry.invalidate_bot(bot.id, reason="project_suspended")

        await self.session.flush()
        await self.audit_service.log_event(
            action=action,
            actor_user_id=actor_user_id,
            master_id=master_id,
            payload_after={"status": master.status.value},
        )
        await self.session.flush()
        return True, msg

    async def list_bots(
        self, page: int = 1, per_page: int = 5
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Paginated list of all registered bot instances."""
        total = await self.session.scalar(select(func.count(BotInstance.id))) or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)

        query = (
            select(
                BotInstance.id, BotInstance.telegram_username, BotInstance.telegram_bot_id,
                BotInstance.status, BotInstance.is_current, BotInstance.master_id,
                BotInstance.last_error, BotInstance.created_at, Master.display_name,
                User.username,
            )
            .join(Master, Master.id == BotInstance.master_id)
            .join(User, User.id == Master.owner_user_id)
            .order_by(desc(BotInstance.created_at))
            .offset(offset)
            .limit(per_page)
        )
        res = await self.session.execute(query)
        bots = res.all()

        items = []
        for b in bots:
            items.append({
                "id": b.id,
                "telegram_username": b.telegram_username,
                "telegram_bot_id": b.telegram_bot_id,
                "status": b.status.value,
                "is_current": b.is_current,
                "master_name": b.display_name,
                "master_id": b.master_id,
                "owner_username": b.username,
                "last_error": b.last_error,
                "created_at": b.created_at,
            })

        return items, total, total_pages

    async def get_bot_details(self, bot_id: int) -> Optional[Dict[str, Any]]:
        """Build a DTO from explicit scalar SQL; never lazy-load expired ORM fields."""
        row = (await self.session.execute(
            select(
                BotInstance.id, BotInstance.public_id, BotInstance.telegram_bot_id,
                BotInstance.telegram_username, BotInstance.telegram_first_name,
                BotInstance.master_id, BotInstance.status, BotInstance.is_current,
                BotInstance.token_version, BotInstance.last_error,
                BotInstance.created_at, BotInstance.updated_at,
                Master.display_name, User.username,
            )
            .join(Master, Master.id == BotInstance.master_id)
            .join(User, User.id == Master.owner_user_id)
            .where(BotInstance.id == bot_id)
        )).one_or_none()
        if row is None:
            return None

        base_url = settings.webhook_base_url.rstrip("/")
        webhook_url = f"{base_url}/telegram/webhook/{row.public_id}"

        return {
            "id": row.id,
            "public_id": str(row.public_id),
            "telegram_bot_id": row.telegram_bot_id,
            "telegram_username": row.telegram_username,
            "telegram_first_name": row.telegram_first_name,
            "master_id": row.master_id,
            "master_name": row.display_name,
            "owner_username": row.username,
            "status": row.status.value,
            "is_current": row.is_current,
            "token_version": row.token_version,
            "last_error": row.last_error,
            "webhook_url": webhook_url,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def list_subscriptions(
        self, page: int = 1, per_page: int = 5
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Paginated list of master subscriptions."""
        total = await self.session.scalar(select(func.count(Master.id))) or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)

        query = (
            select(Master)
            .options(selectinload(Master.owner))
            .order_by(desc(Master.id))
            .offset(offset)
            .limit(per_page)
        )
        res = await self.session.execute(query)
        masters = res.scalars().all()

        items = []
        for m in masters:
            items.append({
                "master_id": m.id,
                "project_name": m.display_name,
                "owner_name": m.owner.first_name if m.owner else "-",
                "subscription_status": m.subscription_status.value,
                "trial_ends_at": m.trial_ends_at,
                "paid_until": m.paid_until,
            })

        return items, total, total_pages

    async def list_payments(
        self, page: int = 1, per_page: int = 5
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Paginated list of billing payments."""
        total = await self.session.scalar(select(func.count(SubscriptionPayment.id))) or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)

        query = (
            select(SubscriptionPayment)
            .options(selectinload(SubscriptionPayment.plan))
            .order_by(desc(SubscriptionPayment.created_at))
            .offset(offset)
            .limit(per_page)
        )
        res = await self.session.execute(query)
        payments = res.scalars().all()

        items = []
        for p in payments:
            master = await self.master_repo.get_by_id(p.master_id)
            items.append({
                "id": p.id,
                "master_id": p.master_id,
                "project_name": master.display_name if master else "Удалён",
                "plan_name": p.plan.name if p.plan else "Тариф",
                "provider": p.provider,
                "provider_payment_id": p.provider_payment_id[:16] + "..." if len(p.provider_payment_id) > 16 else p.provider_payment_id,
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status,
                "created_at": p.created_at,
                "paid_at": p.paid_at,
            })

        return items, total, total_pages

    async def get_payment_details(self, payment_id: int) -> Optional[Dict[str, Any]]:
        """Fetch details of a single subscription payment."""
        payment = await self.session.get(SubscriptionPayment, payment_id)
        if not payment:
            return None

        master = await self.master_repo.get_by_id(payment.master_id)
        owner = await self.session.get(User, master.owner_user_id) if master else None
        plan = await self.session.get(SubscriptionPlan, payment.plan_id) if payment.plan_id else None

        return {
            "id": payment.id,
            "master_id": payment.master_id,
            "project_name": master.display_name if master else "Удалён",
            "owner_name": f"{owner.first_name} (@{owner.username})" if owner and owner.username else (owner.first_name if owner else "-"),
            "plan_code": plan.code if plan else "custom",
            "plan_name": plan.name if plan else "Тариф",
            "provider": payment.provider,
            "provider_payment_id": payment.provider_payment_id,
            "amount": payment.amount,
            "currency": payment.currency,
            "status": payment.status,
            "period_days": payment.period_days,
            "created_at": payment.created_at,
            "paid_at": payment.paid_at,
            "last_reconciled_at": payment.last_reconciled_at,
        }

    async def list_plans(self) -> List[SubscriptionPlan]:
        """List all subscription catalog plans."""
        query = select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order.asc())
        res = await self.session.execute(query)
        return list(res.scalars().all())

    async def get_plan_details(self, plan_id: int) -> Optional[SubscriptionPlan]:
        """Fetch a specific plan by ID."""
        return await self.session.get(SubscriptionPlan, plan_id)

    async def toggle_plan_active(
        self, plan_id: int, actor_user_id: int
    ) -> Tuple[bool, str]:
        """Toggle active availability status for a plan."""
        plan = await self.session.get(SubscriptionPlan, plan_id)
        if not plan:
            return False, "Тариф не найден."

        plan.is_active = not plan.is_active
        await self.session.flush()

        status_str = "активирован" if plan.is_active else "деактивирован"
        await self.audit_service.log_event(
            action=AuditEvent.PLAN_UPDATED,
            actor_user_id=actor_user_id,
            entity_id=plan.id,
            payload_after={"is_active": plan.is_active},
        )
        await self.session.flush()
        return True, f"Тариф «{plan.name}» {status_str}."

    ALLOWED_FEATURE_KEYS = {
        "max_bots",
        "max_staff",
        "custom_branding",
        "broadcasts",
        "analytics",
        "priority_support",
    }

    async def update_plan(
        self,
        plan_id: int,
        actor_user_id: int,
        name: Optional[str] = None,
        price: Optional[Decimal] = None,
        period_days: Optional[int] = None,
        sort_order: Optional[int] = None,
        features: Optional[dict] = None,
    ) -> Tuple[bool, str, Optional[SubscriptionPlan]]:
        """Edit subscription plan parameters with validation."""
        plan = await self.session.get(SubscriptionPlan, plan_id)
        if not plan:
            return False, "Тариф не найден.", None

        payload_before = {
            "name": plan.name,
            "price": str(plan.price),
            "period_days": plan.period_days,
            "sort_order": plan.sort_order,
            "features": plan.features,
        }

        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                return False, "Название тарифа не может быть пустым.", None
            if len(clean_name) > 64:
                return False, "Название тарифа не должно превышать 64 символа.", None
            plan.name = clean_name

        if price is not None:
            try:
                dec_price = Decimal(str(price))
            except Exception:
                return False, "Некорректный формат цены.", None
            if not dec_price.is_finite() or dec_price <= 0:
                return False, "Цена тарифа должна быть больше 0.", None
            if dec_price > Decimal("1000000.00"):
                return False, "Цена тарифа не может превышать 1 000 000 ₽.", None
            plan.price = dec_price.quantize(Decimal("0.01"))

        if period_days is not None:
            if period_days <= 0 or period_days > 3650:
                return False, "Срок действия тарифа должен быть от 1 до 3650 дней.", None
            plan.period_days = period_days

        if sort_order is not None:
            plan.sort_order = sort_order

        if features is not None:
            sanitized = {}
            for k, v in features.items():
                if k in self.ALLOWED_FEATURE_KEYS:
                    sanitized[k] = v
            plan.features = sanitized

        await self.session.flush()

        payload_after = {
            "name": plan.name,
            "price": str(plan.price),
            "period_days": plan.period_days,
            "sort_order": plan.sort_order,
            "features": plan.features,
        }

        await self.audit_service.log_event(
            action=AuditEvent.PLAN_UPDATED,
            actor_user_id=actor_user_id,
            entity_type="SubscriptionPlan",
            entity_id=plan.id,
            payload_before=payload_before,
            payload_after=payload_after,
        )
        await self.session.flush()
        return True, f"Тариф «{plan.name}» успешно обновлён.", plan

    async def can_hard_delete_plan(self, plan_id: int) -> Tuple[bool, str]:
        """Check whether plan can be hard deleted or only archived."""
        payments_count = await self.session.scalar(
            select(func.count(SubscriptionPayment.id)).where(SubscriptionPayment.plan_id == plan_id)
        ) or 0
        periods_count = await self.session.scalar(
            select(func.count(SubscriptionPeriod.id)).where(SubscriptionPeriod.plan_id == plan_id)
        ) or 0
        if payments_count > 0 or periods_count > 0:
            return False, "Тариф имеет историю покупок или периодов. Удаление запрещено, используйте деактивацию."
        return True, "Тариф может быть удалён."

    async def extend_subscription_manually(
        self,
        master_id: int,
        days: int,
        actor_user_id: int,
        reason: str = "Ручное продление",
    ) -> Tuple[bool, str, Optional[datetime]]:
        """Delegate manual extension to SubscriptionService."""
        from app.services.subscription_service import SubscriptionService
        sub_svc = SubscriptionService(self.session)
        try:
            new_date, _ = await sub_svc.extend_subscription_manually(
                master_id=master_id,
                days=days,
                actor_user_id=actor_user_id,
                reason=reason,
            )
            return True, f"Подписка успешно продлена на {days} дн. до {new_date.strftime('%d.%m.%Y')}.", new_date
        except Exception as exc:
            logger.exception("Error extending subscription manually: %s", exc)
            return False, f"Ошибка продления: {exc}", None

    async def set_subscription_expiry_manually(
        self,
        master_id: int,
        new_expiry_date: datetime,
        actor_user_id: int,
        reason: str = "Установка даты окончания",
    ) -> Tuple[bool, str, Optional[datetime]]:
        """Delegate manual expiry date setting to SubscriptionService."""
        from app.services.subscription_service import SubscriptionService
        sub_svc = SubscriptionService(self.session)
        try:
            new_date, _ = await sub_svc.set_subscription_expiry_manually(
                master_id=master_id,
                new_expiry_date=new_expiry_date,
                actor_user_id=actor_user_id,
                reason=reason,
            )
            return True, f"Дата окончания установлена: {new_date.strftime('%d.%m.%Y')}.", new_date
        except Exception as exc:
            logger.exception("Error setting subscription expiry manually: %s", exc)
            return False, f"Ошибка установки даты: {exc}", None

    async def get_subscription_history(
        self, master_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Retrieve audit history of subscription changes for a master."""
        from app.database.models.audit import AuditLog
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.master_id == master_id,
                AuditLog.action.in_([
                    AuditEvent.SUBSCRIPTION_EXTENDED,
                    AuditEvent.SUBSCRIPTION_EXPIRY_SET,
                    AuditEvent.SUBSCRIPTION_SUSPENDED,
                    AuditEvent.SUBSCRIPTION_ACTIVATED,
                    "SUBSCRIPTION_RENEWED",
                    "SUBSCRIPTION_ACTIVATED",
                    "SUBSCRIPTION_RESTORED",
                    "SUBSCRIPTION_SUSPENDED",
                ]),
            )
            .order_by(desc(AuditLog.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        logs = res.scalars().all()

        items = []
        for l in logs:
            items.append({
                "id": l.id,
                "action": l.action,
                "actor_user_id": l.actor_user_id,
                "created_at": l.created_at,
                "payload_before": l.payload_before or {},
                "payload_after": l.payload_after or {},
            })
        return items

    async def get_project_impact_summary(self, master_id: int) -> Dict[str, int]:
        """Exact count of all tenant-owned entities for hard delete preview."""
        from app.database.models.appointment import Appointment
        from app.database.models.broadcast import Broadcast
        from app.database.models.master import BotInstance, MasterClient
        from app.database.models.payment import Payment
        from app.database.models.portfolio import PortfolioItem
        from app.database.models.review import Review
        from app.database.models.schedule import BlockedInterval, ScheduleException, ScheduleTemplate
        from app.database.models.service import Service
        from app.database.models.staff import StaffMember

        bots_count = await self.session.scalar(
            select(func.count(BotInstance.id)).where(BotInstance.master_id == master_id)
        ) or 0
        staff_count = await self.session.scalar(
            select(func.count(StaffMember.id)).where(StaffMember.master_id == master_id)
        ) or 0
        services_count = await self.session.scalar(
            select(func.count(Service.id)).where(Service.master_id == master_id)
        ) or 0
        appts_count = await self.session.scalar(
            select(func.count(Appointment.id)).where(Appointment.master_id == master_id)
        ) or 0
        clients_count = await self.session.scalar(
            select(func.count(MasterClient.id)).where(MasterClient.master_id == master_id)
        ) or 0
        payments_count = await self.session.scalar(
            select(func.count(Payment.id)).where(Payment.master_id == master_id)
        ) or 0
        reviews_count = await self.session.scalar(
            select(func.count(Review.id)).where(Review.master_id == master_id)
        ) or 0
        portfolio_count = await self.session.scalar(
            select(func.count(PortfolioItem.id)).where(PortfolioItem.master_id == master_id)
        ) or 0
        broadcasts_count = await self.session.scalar(
            select(func.count(Broadcast.id)).where(Broadcast.master_id == master_id)
        ) or 0
        tpl_count = await self.session.scalar(
            select(func.count(ScheduleTemplate.id)).where(ScheduleTemplate.master_id == master_id)
        ) or 0
        exc_count = await self.session.scalar(
            select(func.count(ScheduleException.id)).where(ScheduleException.master_id == master_id)
        ) or 0
        blk_count = await self.session.scalar(
            select(func.count(BlockedInterval.id)).where(BlockedInterval.master_id == master_id)
        ) or 0

        return {
            "bots": bots_count,
            "staff": staff_count,
            "services": services_count,
            "appointments": appts_count,
            "clients": clients_count,
            "payments": payments_count,
            "reviews": reviews_count,
            "portfolio": portfolio_count,
            "broadcasts": broadcasts_count,
            "schedule": tpl_count + exc_count + blk_count,
        }

    async def hard_delete_bot(
        self, bot_instance_id: int, actor_user_id: int
    ) -> Tuple[bool, str]:
        """Physically delete a BotInstance and its tokens with best-effort webhook cleanup."""
        bot = await self.session.scalar(
            select(BotInstance).where(BotInstance.id == bot_instance_id).with_for_update()
        )
        if not bot:
            return False, "Бот не найден или уже удалён."

        # Best-effort deleteWebhook
        webhook_ok = True
        if bot.encrypted_token and bot.telegram_bot_id:
            try:
                from app.core.token_crypto import TokenCrypto
                raw_token = TokenCrypto().decrypt(bot.encrypted_token, associated_data=bot.telegram_bot_id)
                await self.gateway.delete_webhook(raw_token)
            except Exception as exc:
                logger.warning("Telegram deleteWebhook failed for bot #%s: %s", bot.id, exc)
                webhook_ok = False

        if self.registry:
            try:
                await self.registry.invalidate_bot_instance(bot.id, reason="bot_hard_deleted")
            except Exception as exc:
                logger.warning("BotRegistry invalidation failed: %s", exc)

        # Audit snapshot before purging
        master_id = bot.master_id
        username = bot.telegram_username
        payload_before = {
            "bot_instance_id": bot.id,
            "telegram_bot_id": bot.telegram_bot_id,
            "telegram_username": bot.telegram_username,
            "master_id": bot.master_id,
            "status": bot.status.value,
        }

        # Delete dependent outbox items first to respect RESTRICT FK
        from app.database.models.telegram_outbox import TelegramOutbox
        await self.session.execute(
            delete(TelegramOutbox).where(TelegramOutbox.bot_instance_id == bot.id)
        )

        # Delete bot instance
        await self.session.delete(bot)
        await self.session.flush()

        # If master has no other active bots, update status to SETUP_REQUIRED
        remaining_bots = await self.session.scalar(
            select(func.count(BotInstance.id)).where(
                BotInstance.master_id == master_id,
                BotInstance.status.in_([BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED]),
            )
        ) or 0
        if remaining_bots == 0:
            master = await self.master_repo.get_by_id(master_id)
            if master and master.status == MasterStatus.ACTIVE:
                master.status = MasterStatus.SETUP_REQUIRED
                await self.session.flush()

        await self.audit_service.log_event(
            action=AuditEvent.BOT_HARD_DELETED,
            actor_user_id=actor_user_id,
            master_id=master_id,
            entity_type="BotInstance",
            entity_id=bot_instance_id,
            payload_before=payload_before,
            payload_after={"status": "DELETED", "webhook_cleaned": webhook_ok},
        )
        await self.session.flush()

        msg = f"Бот @{username or bot_instance_id} полностью удалён из платформы."
        if not webhook_ok:
            msg += "\n⚠️ Telegram API не подтвердил удаление webhook (токен мог быть отозван). Локальные данные удалены."
        return True, msg

    async def hard_delete_project(
        self, master_id: int, actor_user_id: int
    ) -> Tuple[bool, str]:
        """
        Hard delete an entire tenant/master project and all its tenant-owned entities.
        Preserves global User accounts and creates an audit record.
        """
        master = await self.session.scalar(
            select(Master).where(Master.id == master_id).with_for_update()
        )
        if not master:
            return False, "Проект не найден или уже удалён."

        # 1. Best-effort webhook cleanup and cache invalidation for all bots of this master
        bots_query = select(BotInstance).where(BotInstance.master_id == master_id)
        bots_res = await self.session.execute(bots_query)
        bots = bots_res.scalars().all()

        from app.core.token_crypto import TokenCrypto
        crypto = TokenCrypto()
        for b in bots:
            if b.encrypted_token and b.telegram_bot_id:
                try:
                    raw_token = crypto.decrypt(b.encrypted_token, associated_data=b.telegram_bot_id)
                    await self.gateway.delete_webhook(raw_token)
                except Exception as exc:
                    logger.warning("deleteWebhook failed for bot #%s of master #%s: %s", b.id, master_id, exc)
            if self.registry:
                try:
                    await self.registry.invalidate_bot_instance(b.id, reason="project_hard_deleted")
                except Exception as exc:
                    logger.warning("BotRegistry invalidation failed: %s", exc)

        # 2. Get impact summary for audit log
        impact = await self.get_project_impact_summary(master_id)
        display_name = master.display_name
        owner_id = master.owner_user_id

        # 3. Purge all tenant data in strict dependency order (leaf to root)
        from app.database.models.broadcast import Broadcast, BroadcastRecipient
        from app.database.models.checkout_session import CheckoutSession
        from app.database.models.payment import Payment, PaymentProof
        from app.database.models.portfolio import PortfolioCategory, PortfolioItem
        from app.database.models.review import Review
        from app.database.models.schedule import BlockedInterval, ScheduleException, ScheduleTemplate
        from app.database.models.service import Service
        from app.database.models.staff import StaffMember, StaffService
        from app.database.models.telegram_outbox import TelegramOutbox
        from app.database.models.master import MasterAdmin, MasterSettings

        # Outbox & notifications
        await self.session.execute(delete(TelegramOutbox).where(TelegramOutbox.master_id == master_id))

        # Broadcasts & recipients
        broadcast_ids_stmt = select(Broadcast.id).where(Broadcast.master_id == master_id)
        await self.session.execute(
            delete(BroadcastRecipient).where(BroadcastRecipient.broadcast_id.in_(broadcast_ids_stmt))
        )
        await self.session.execute(delete(Broadcast).where(Broadcast.master_id == master_id))

        # Checkout sessions
        await self.session.execute(delete(CheckoutSession).where(CheckoutSession.master_id == master_id))

        # Reviews
        await self.session.execute(delete(Review).where(Review.master_id == master_id))

        # Payments & payment proofs
        payment_ids_stmt = select(Payment.id).where(Payment.master_id == master_id)
        await self.session.execute(
            delete(PaymentProof).where(PaymentProof.payment_id.in_(payment_ids_stmt))
        )
        await self.session.execute(delete(Payment).where(Payment.master_id == master_id))

        # Appointments
        await self.session.execute(delete(Appointment).where(Appointment.master_id == master_id))

        # Schedule
        await self.session.execute(delete(BlockedInterval).where(BlockedInterval.master_id == master_id))
        await self.session.execute(delete(ScheduleException).where(ScheduleException.master_id == master_id))
        await self.session.execute(delete(ScheduleTemplate).where(ScheduleTemplate.master_id == master_id))

        # Portfolio
        await self.session.execute(delete(PortfolioItem).where(PortfolioItem.master_id == master_id))
        await self.session.execute(delete(PortfolioCategory).where(PortfolioCategory.master_id == master_id))

        # Staff services & staff
        await self.session.execute(delete(StaffService).where(StaffService.master_id == master_id))
        await self.session.execute(delete(StaffMember).where(StaffMember.master_id == master_id))

        # Services
        await self.session.execute(delete(Service).where(Service.master_id == master_id))

        # Clients, admins, settings
        await self.session.execute(delete(MasterClient).where(MasterClient.master_id == master_id))
        await self.session.execute(delete(MasterAdmin).where(MasterAdmin.master_id == master_id))
        await self.session.execute(delete(MasterSettings).where(MasterSettings.master_id == master_id))

        # Bot instances
        await self.session.execute(delete(BotInstance).where(BotInstance.master_id == master_id))

        # Subscriptions
        await self.session.execute(delete(SubscriptionPeriod).where(SubscriptionPeriod.master_id == master_id))
        await self.session.execute(delete(SubscriptionPayment).where(SubscriptionPayment.master_id == master_id))

        # Delete Master
        await self.session.execute(delete(Master).where(Master.id == master_id))
        await self.session.flush()

        # Audit log (master_id set to None since master is purged)
        await self.audit_service.log_event(
            action=AuditEvent.PROJECT_HARD_DELETED,
            actor_user_id=actor_user_id,
            master_id=None,
            entity_type="Master",
            entity_id=master_id,
            payload_before={
                "id": master_id,
                "display_name": display_name,
                "owner_user_id": owner_id,
                "impact": impact,
            },
            payload_after={"status": "DELETED"},
        )
        await self.session.flush()
        return True, f"Проект «{display_name}» (#{master_id}) и все его данные успешно удалены."

    async def list_user_projects(self, user_id: int) -> List[Dict[str, Any]]:
        """List all projects owned by a specific user."""
        query = select(Master).where(Master.owner_user_id == user_id).order_by(Master.id.asc())
        res = await self.session.execute(query)
        masters = res.scalars().all()
        items = []
        for m in masters:
            bot = await self.bot_repo.get_current_for_master(m.id)
            items.append({
                "id": m.id,
                "display_name": m.display_name,
                "status": m.status.value,
                "subscription_status": m.subscription_status.value,
                "paid_until": m.paid_until,
                "trial_ends_at": m.trial_ends_at,
                "bot_username": bot.telegram_username if bot else None,
            })
        return items

    async def list_user_bots(self, user_id: int) -> List[Dict[str, Any]]:
        """List all bot instances belonging to a user's projects."""
        query = (
            select(BotInstance)
            .join(Master, Master.id == BotInstance.master_id)
            .where(Master.owner_user_id == user_id)
            .order_by(desc(BotInstance.created_at))
        )
        res = await self.session.execute(query)
        bots = res.scalars().all()
        items = []
        for b in bots:
            master = await self.master_repo.get_by_id(b.master_id)
            items.append({
                "id": b.id,
                "telegram_username": b.telegram_username,
                "telegram_bot_id": b.telegram_bot_id,
                "status": b.status.value,
                "is_current": b.is_current,
                "master_id": b.master_id,
                "master_name": master.display_name if master else "-",
                "last_error": b.last_error,
            })
        return items

    async def list_user_subscriptions(self, user_id: int) -> List[Dict[str, Any]]:
        """List subscriptions for projects owned by a user."""
        query = select(Master).where(Master.owner_user_id == user_id).order_by(Master.id.asc())
        res = await self.session.execute(query)
        masters = res.scalars().all()
        items = []
        for m in masters:
            items.append({
                "master_id": m.id,
                "project_name": m.display_name,
                "subscription_status": m.subscription_status.value,
                "paid_until": m.paid_until,
                "trial_ends_at": m.trial_ends_at,
            })
        return items

    async def list_user_payments(self, user_id: int) -> List[Dict[str, Any]]:
        """List subscription payments associated with a user's projects."""
        query = (
            select(SubscriptionPayment)
            .join(Master, Master.id == SubscriptionPayment.master_id)
            .where(Master.owner_user_id == user_id)
            .order_by(desc(SubscriptionPayment.created_at))
        )
        res = await self.session.execute(query)
        payments = res.scalars().all()
        items = []
        for p in payments:
            master = await self.master_repo.get_by_id(p.master_id)
            items.append({
                "id": p.id,
                "master_id": p.master_id,
                "project_name": master.display_name if master else "Удалён",
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status,
                "provider": p.provider,
                "created_at": p.created_at,
                "paid_at": p.paid_at,
            })
        return items

    async def list_audit_logs(
        self, page: int = 1, per_page: int = 10
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Paginated platform audit events."""
        from app.database.models.audit import AuditLog
        total = await self.session.scalar(select(func.count(AuditLog.id))) or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)

        query = select(AuditLog).order_by(desc(AuditLog.created_at)).offset(offset).limit(per_page)
        res = await self.session.execute(query)
        logs = res.scalars().all()

        items = []
        for l in logs:
            items.append({
                "id": l.id,
                "action": l.action,
                "actor_user_id": l.actor_user_id,
                "master_id": l.master_id,
                "entity_type": l.entity_type,
                "entity_id": l.entity_id,
                "created_at": l.created_at,
            })
        return items, total, total_pages

    async def get_audit_log_details(self, log_id: int) -> Optional[Dict[str, Any]]:
        """Fetch details of a single audit log event."""
        from app.database.models.audit import AuditLog
        log = await self.session.get(AuditLog, log_id)
        if not log:
            return None
        actor = await self.session.get(User, log.actor_user_id) if log.actor_user_id else None
        return {
            "id": log.id,
            "action": log.action,
            "actor_user_id": log.actor_user_id,
            "actor_username": actor.username if actor else None,
            "master_id": log.master_id,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "payload_before": log.payload_before,
            "payload_after": log.payload_after,
            "created_at": log.created_at,
        }
