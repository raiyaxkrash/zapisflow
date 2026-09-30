"""
Centralized SaaS access control and feature gating policy based on real-time effective subscription.
"""

from datetime import datetime, timezone
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.subscription import EffectiveSubscriptionStatus
from app.services.subscription_service import EffectiveSubscription, SubscriptionService

logger = logging.getLogger("app.services.subscription_access_policy")

NEUTRAL_CLIENT_EXPIRED_MESSAGE = (
    "🌸 Онлайн-запись сейчас временно недоступна. Пожалуйста, свяжитесь с мастером напрямую."
)


class SubscriptionAccessPolicy:
    """
    Centralized access policy answering feature gating questions for a master.
    Does not allow distributed 'if subscription_status ...' checks across handlers.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.subscription_service = SubscriptionService(session)

    async def get_effective_status(
        self,
        master_id: int,
        now_utc: Optional[datetime] = None,
    ) -> EffectiveSubscription:
        """Calculate effective status in real-time."""
        return await self.subscription_service.get_effective_status(master_id, now_utc=now_utc)

    async def can_accept_new_booking(self, master_id: int) -> bool:
        """
        Check if client can start booking flow or view booking slots.
        Allowed: TRIAL_ACTIVE, PAID_ACTIVE.
        Blocked: EXPIRED, SUSPENDED.
        """
        eff = await self.get_effective_status(master_id)
        return eff.status in (
            EffectiveSubscriptionStatus.TRIAL_ACTIVE,
            EffectiveSubscriptionStatus.PAID_ACTIVE,
        )

    async def can_create_hold(self, master_id: int) -> bool:
        """
        Check if client can reserve a new slot and create an appointment hold.
        Allowed: TRIAL_ACTIVE, PAID_ACTIVE.
        Blocked: EXPIRED, SUSPENDED.
        """
        return await self.can_accept_new_booking(master_id)

    async def can_submit_payment_proof(
        self,
        master_id: int,
        appointment_id: Optional[int] = None,
        now_utc: Optional[datetime] = None,
    ) -> bool:
        """
        Policy for submitting client payment proof:
        1. If master is TRIAL_ACTIVE or PAID_ACTIVE -> Allowed.
        2. If master is EXPIRED:
           - In-flight grace policy: If the appointment was held (WAITING_PAYMENT)
             and the hold has not expired, the client is permitted to submit proof
             so that existing payment attempts are not lost.
           - Otherwise -> Blocked.
        3. If SUSPENDED -> Blocked.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        eff = await self.get_effective_status(master_id, now_utc=now_utc)
        if eff.status in (
            EffectiveSubscriptionStatus.TRIAL_ACTIVE,
            EffectiveSubscriptionStatus.PAID_ACTIVE,
        ):
            return True

        if eff.status == EffectiveSubscriptionStatus.SUSPENDED:
            return False

        # Master is EXPIRED: check in-flight appointment hold
        if appointment_id is not None:
            app = await self.session.get(Appointment, appointment_id)
            if app and app.master_id == master_id:
                hold_dt = app.hold_until
                if hold_dt and hold_dt.tzinfo is None:
                    hold_dt = hold_dt.replace(tzinfo=timezone.utc)

                if app.status == AppointmentStatus.WAITING_PAYMENT and hold_dt and hold_dt > now_utc:
                    logger.info(
                        "Allowing in-flight payment proof for appointment #%s on EXPIRED master #%s",
                        appointment_id,
                        master_id,
                    )
                    return True

        return False

    async def can_send_marketing_broadcast(self, master_id: int) -> bool:
        """
        Check if master can create or dispatch marketing campaigns.
        Allowed: TRIAL_ACTIVE, PAID_ACTIVE.
        Blocked: EXPIRED, SUSPENDED.
        Transactional reminders for existing appointments are handled separately and never blocked.
        """
        eff = await self.get_effective_status(master_id)
        return eff.status in (
            EffectiveSubscriptionStatus.TRIAL_ACTIVE,
            EffectiveSubscriptionStatus.PAID_ACTIVE,
        )

    async def can_manage_admin(self, master_id: int) -> bool:
        """
        Check if owner / master admin can access admin panel, appointments, clients, and settings.
        Allowed: TRIAL_ACTIVE, PAID_ACTIVE, EXPIRED (data and management are never lost!).
        Blocked / Restricted: SUSPENDED.
        """
        eff = await self.get_effective_status(master_id)
        return eff.status in (
            EffectiveSubscriptionStatus.TRIAL_ACTIVE,
            EffectiveSubscriptionStatus.PAID_ACTIVE,
            EffectiveSubscriptionStatus.EXPIRED,
        )
