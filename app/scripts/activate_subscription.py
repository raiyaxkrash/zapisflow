"""
Platform Admin CLI: Manual subscription activation and extension script.
Usage:
    python -m app.scripts.activate_subscription --master-id 1 --days 30 [--plan-code basic_monthly] [--reason "Support manual renewal"]
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import sys
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.audit import AuditLog
from app.database.models.master import Master, SubscriptionStatus
from app.database.models.subscription import SubscriptionPeriod, SubscriptionPlan
from app.database.session import async_session_factory

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("activate_subscription")


async def activate_subscription(
    master_id: int,
    days: int = 30,
    plan_code: str = "basic_monthly",
    reason: str = "Manual CLI activation",
    session: Optional[AsyncSession] = None,
) -> None:
    """Manually activate or extend subscription for a master."""
    if session is not None:
        await _execute_activation(session, master_id, days, plan_code, reason, commit=False)
        return

    async with async_session_factory() as sess:
        await _execute_activation(sess, master_id, days, plan_code, reason, commit=True)


async def _execute_activation(
    session: AsyncSession,
    master_id: int,
    days: int,
    plan_code: str,
    reason: str,
    commit: bool = True,
) -> None:
    # 1. Fetch master with lock
    stmt = select(Master).where(Master.id == master_id).with_for_update()
    res = await session.execute(stmt)
    master = res.scalar_one_or_none()
    if not master:
        logger.error("Master #%d not found", master_id)
        if commit:
            sys.exit(1)
        raise ValueError(f"Master #{master_id} not found")

    # 2. Fetch plan
    plan_stmt = select(SubscriptionPlan).where(
        SubscriptionPlan.code == plan_code,
        SubscriptionPlan.is_active == True,  # noqa: E712
    )
    plan_res = await session.execute(plan_stmt)
    plan = plan_res.scalar_one_or_none()
    if not plan:
        # Fallback to first active
        first_plan = await session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.is_active == True).limit(1)
        )
        plan = first_plan.scalar_one_or_none()

    now_utc = datetime.now(timezone.utc)
    prev_status = master.subscription_status
    prev_paid_until = master.paid_until

    # 3. Calculate new paid_until
    # Policy:
    # If currently ACTIVE and paid_until in future -> extend from paid_until
    # If currently TRIAL and trial_ends_at in future -> extend from trial_ends_at (keep remaining trial)
    # Else -> extend from now_utc
    if master.paid_until and master.paid_until > now_utc:
        base_dt = master.paid_until
    elif master.subscription_status == SubscriptionStatus.TRIAL and master.trial_ends_at and master.trial_ends_at > now_utc:
        base_dt = master.trial_ends_at
    else:
        base_dt = now_utc

    new_paid_until = base_dt + timedelta(days=days)

    # 4. Create subscription period record
    period = SubscriptionPeriod(
        master_id=master.id,
        plan_id=plan.id if plan else None,
        status="ACTIVE",
        source="MANUAL",
        starts_at=base_dt,
        ends_at=new_paid_until,
        amount=plan.price if plan else None,
        currency=plan.currency if plan else "RUB",
        external_payment_id=f"cli_manual_{int(now_utc.timestamp())}",
    )
    session.add(period)

    # 5. Update master
    master.paid_until = new_paid_until
    master.subscription_status = SubscriptionStatus.ACTIVE

    # 6. Audit log
    audit = AuditLog(
        master_id=master.id,
        actor_user_id=None,
        action="SUBSCRIPTION_MANUAL_ACTIVATED",
        entity_type="Master",
        entity_id=master.id,
        payload_before={
            "status": prev_status.value if prev_status else None,
            "paid_until": prev_paid_until.isoformat() if prev_paid_until else None,
        },
        payload_after={
            "status": master.subscription_status.value,
            "paid_until": new_paid_until.isoformat(),
            "days_added": days,
            "reason": reason,
            "plan_code": plan.code if plan else None,
        },
    )
    session.add(audit)
    if commit:
        await session.commit()
    else:
        await session.flush()

    print("=" * 60)
    print("✅ ПОДПИСКА УСПЕШНО АКТИВИРОВАНА / ПРОДЛЕНА")
    print(f"Проект (Master #{master.id}): {master.display_name}")
    print(f"Статус: {master.subscription_status.value}")
    print(f"Добавлено дней: {days}")
    print(f"Действует до: {new_paid_until.strftime('%d.%m.%Y %H:%M UTC')}")
    print(f"Тариф: {plan.name if plan else 'Без тарифа'}")
    print(f"Причина: {reason}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ручная активация подписки мастера ZapisFlow")
    parser.add_argument("--master-id", type=int, required=True, help="ID мастера (проекта)")
    parser.add_argument("--days", type=int, default=30, help="Количество дней продления (по умолчанию 30)")
    parser.add_argument("--plan-code", type=str, default="basic_monthly", help="Код тарифа (по умолчанию basic_monthly)")
    parser.add_argument("--reason", type=str, default="Manual CLI activation", help="Причина ручной активации")

    args = parser.parse_args()
    asyncio.run(
        activate_subscription(
            master_id=args.master_id,
            days=args.days,
            plan_code=args.plan_code,
            reason=args.reason,
        )
    )


if __name__ == "__main__":
    main()
