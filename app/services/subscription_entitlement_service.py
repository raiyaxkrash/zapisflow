"""
Subscription entitlement and usage limits service for ZapisFlow SaaS masters.
Delegates effective subscription lifecycle resolution to SubscriptionService.
"""

from typing import Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.master import Master
from app.database.models.staff import StaffMember
from app.database.models.subscription import EffectiveSubscriptionStatus, SubscriptionPlan
from app.services.subscription_service import SubscriptionService


class SubscriptionEntitlementService:
    """
    Evaluates role and tier entitlements for masters and owners.
    Does not duplicate subscription calculation logic.
    """

    def __init__(
        self,
        session: AsyncSession,
        subscription_service: Optional[SubscriptionService] = None,
    ) -> None:
        self.session = session
        self.subscription_service = subscription_service or SubscriptionService(session)

    async def can_create_master(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """
        Evaluate if a user is allowed to create another master project.
        In ZapisFlow v1, users have no artificial limits on project creation.
        """
        return True, None

    async def can_create_staff(self, master_id: int) -> Tuple[bool, Optional[str]]:
        """
        Evaluate if a master is allowed to add a new staff member.
        Blocked if the project is suspended or subscription is expired.
        Basic plan supports unlimited staff unless explicitly capped in Plan.features.
        """
        eff_sub = await self.subscription_service.get_effective_status(master_id)

        if eff_sub.status == EffectiveSubscriptionStatus.SUSPENDED:
            return False, f"Аккаунт приостановлен администратором. Обратитесь в поддержку: {settings.support_tag}"

        if eff_sub.status == EffectiveSubscriptionStatus.EXPIRED:
            return False, "Подписка истекла. Продлите подписку для добавления сотрудников."

        # Check plan limits if configured
        plan: Optional[SubscriptionPlan] = None
        try:
            plan = await self.subscription_service.get_active_plan()
        except Exception:
            plan = None

        if plan and plan.features and isinstance(plan.features, dict):
            max_staff = plan.features.get("max_staff")
            if max_staff is not None and isinstance(max_staff, int):
                # Count current active staff members
                count_stmt = select(func.count(StaffMember.id)).where(
                    StaffMember.master_id == master_id,
                    StaffMember.is_active == True,  # noqa: E712
                )
                current_staff_count = await self.session.scalar(count_stmt) or 0
                if current_staff_count >= max_staff:
                    return False, f"Достигнут лимит сотрудников для тарифа {plan.name} ({max_staff})."

        return True, None

    async def can_create_appointment(self, master_id: int) -> Tuple[bool, Optional[str]]:
        """
        Evaluate if appointments can be created (both client self-booking and master manual booking).
        Blocked when subscription is expired or suspended.
        """
        eff_sub = await self.subscription_service.get_effective_status(master_id)

        if eff_sub.status == EffectiveSubscriptionStatus.SUSPENDED:
            return False, f"Аккаунт приостановлен администратором. Обратитесь в поддержку: {settings.support_tag}"

        if eff_sub.status == EffectiveSubscriptionStatus.EXPIRED:
            return False, "Подписка истекла. Продлите подписку для создания записей."

        return True, None

    async def can_use_broadcast(self, master_id: int) -> Tuple[bool, Optional[str]]:
        """
        Evaluate if broadcast messaging can be used for CRM marketing.
        Blocked when subscription is expired or suspended.
        """
        eff_sub = await self.subscription_service.get_effective_status(master_id)

        if eff_sub.status == EffectiveSubscriptionStatus.SUSPENDED:
            return False, f"Аккаунт приостановлен администратором. Обратитесь в поддержку: {settings.support_tag}"

        if eff_sub.status == EffectiveSubscriptionStatus.EXPIRED:
            return False, "Подписка истекла. Рассылки доступны только с активной подпиской."

        plan: Optional[SubscriptionPlan] = None
        try:
            plan = await self.subscription_service.get_active_plan()
        except Exception:
            plan = None

        if plan and plan.features and isinstance(plan.features, dict):
            if plan.features.get("can_broadcast") is False:
                return False, f"Рассылки не входят в ваш текущий тариф ({plan.name})."

        return True, None

    async def can_use_feature(self, master_id: int, feature_key: str) -> Tuple[bool, Optional[str]]:
        """
        Generic feature entitlement evaluator.
        Allows read-only features even when expired; blocks mutations.
        """
        eff_sub = await self.subscription_service.get_effective_status(master_id)

        if eff_sub.status == EffectiveSubscriptionStatus.SUSPENDED:
            return False, f"Аккаунт приостановлен администратором. Обратитесь в поддержку: {settings.support_tag}"

        # Allowed read-only actions when expired
        read_only_prefixes = ("read_", "view_", "list_", "export_")
        is_read_only = any(feature_key.startswith(p) for p in read_only_prefixes) or feature_key in {
            "crm_view",
            "schedule_view",
            "finances_view",
            "settings_view",
            "staff_view",
            "reviews_view",
            "portfolio_view",
        }

        if eff_sub.status == EffectiveSubscriptionStatus.EXPIRED and not is_read_only:
            return False, "Подписка истекла. Продлите подписку для использования этой функции."

        plan: Optional[SubscriptionPlan] = None
        try:
            plan = await self.subscription_service.get_active_plan()
        except Exception:
            plan = None

        if plan and plan.features and isinstance(plan.features, dict):
            if plan.features.get(feature_key) is False:
                return False, f"Функция недоступна в текущем тарифе ({plan.name})."

        return True, None
