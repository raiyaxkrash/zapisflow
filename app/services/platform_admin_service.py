"""Platform Admin Service for system-wide monitoring, metrics, and administration."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, func, select
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
