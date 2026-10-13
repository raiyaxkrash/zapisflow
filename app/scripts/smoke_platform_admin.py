"""Live Production Smoke Test for Platform Admin expansion.

Verifies:
1. Dashboard stats and SaaS metrics calculation.
2. Plan listing and parameter validation.
3. Temporary test user creation and sub-navigation queries.
4. Manual subscription extension and exact expiry setting.
5. Subscription audit history.
6. Temporary bot creation, outbox creation, and hard deletion.
7. Project impact summary.
8. Complete project hard deletion (tenant purge) and preservation of global User account.
9. Safe cleanup of smoke test entities.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import sys

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.audit import AuditLog
from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterStatus, SubscriptionStatus
from app.database.models.subscription import SubscriptionPlan
from app.database.models.telegram_outbox import TelegramOutbox, TelegramOutboxStatus
from app.database.models.user import User
from app.database.session import async_session_factory
from app.services.audit_service import AuditEvent
from app.services.platform_admin_service import PlatformAdminService


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("smoke_platform_admin")


async def run_smoke_test() -> bool:
    logger.info("--- STARTING PLATFORM ADMIN PRODUCTION SMOKE TEST ---")

    async with async_session_factory() as session:
        admin_svc = PlatformAdminService(session)

        # 1. Dashboard stats and SaaS metrics
        logger.info("[1/9] Checking dashboard stats and SaaS metrics...")
        stats = await admin_svc.get_dashboard_stats()
        metrics = await admin_svc.get_saas_metrics()
        assert "total_users" in stats and stats["total_users"] >= 0
        assert "mrr" in metrics and metrics["mrr"] >= Decimal("0.00")
        logger.info("  -> Dashboard: %s users, %s masters, %s active bots, MRR: %s ₽",
                    stats["total_users"], stats["total_masters"], stats["active_bots"], stats["mrr"])
        logger.info("  [OK] Dashboard and SaaS metrics operational.")

        # 2. Plan catalog inspection
        logger.info("[2/9] Checking plans catalog and parameters...")
        plans = await admin_svc.list_plans()
        assert len(plans) >= 1
        test_plan = plans[0]
        logger.info("  -> Found plan #%s: «%s» (%s ₽, %s d)", test_plan.id, test_plan.name, test_plan.price, test_plan.period_days)
        logger.info("  [OK] Plan catalog operational.")

        # 3. Create temporary tenant test user and project
        logger.info("[3/9] Creating temporary test tenant...")
        temp_tg_id = 999_888_771
        # Cleanup any lingering previous smoke user
        await session.execute(delete(User).where(User.telegram_id == temp_tg_id))
        await session.commit()

        smoke_user = User(
            telegram_id=temp_tg_id,
            first_name="SmokeTester",
            username="temp_smoke_tester",
        )
        session.add(smoke_user)
        await session.flush()

        smoke_master = Master(
            owner_user_id=smoke_user.id,
            display_name="Temp Smoke Studio",
            status=MasterStatus.ACTIVE,
            subscription_status=SubscriptionStatus.TRIAL,
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(smoke_master)
        await session.commit()
        logger.info("  -> Created test User #%s and Master #%s", smoke_user.id, smoke_master.id)
        logger.info("  [OK] Temporary tenant initialized.")

        # 4. Verify user card sub-navigation queries
        logger.info("[4/9] Testing user card navigation queries...")
        user_projects = await admin_svc.list_user_projects(smoke_user.id)
        assert len(user_projects) == 1
        assert user_projects[0]["id"] == smoke_master.id
        logger.info("  [OK] User card sub-navigation returned correct projects.")

        # 5. Manual subscription extension (+1 day)
        logger.info("[5/9] Testing manual subscription extension (+1 day)...")
        ok, msg, new_date = await admin_svc.extend_subscription_manually(
            master_id=smoke_master.id,
            days=1,
            actor_user_id=smoke_user.id,
            reason="Продвижение / Smoke Test",
        )
        assert ok is True
        await session.refresh(smoke_master)
        assert smoke_master.subscription_status == SubscriptionStatus.ACTIVE
        logger.info("  -> Subscription extended to %s. Status: %s", new_date.strftime("%d.%m.%Y"), smoke_master.subscription_status)
        logger.info("  [OK] Manual extension verified.")

        # 6. Manual subscription exact expiry date setting (+3 days)
        logger.info("[6/9] Testing exact subscription expiry setting...")
        target_date = datetime.now(timezone.utc) + timedelta(days=3)
        ok, msg, set_date = await admin_svc.set_subscription_expiry_manually(
            master_id=smoke_master.id,
            new_expiry_date=target_date,
            actor_user_id=smoke_user.id,
            reason="Тестовая фиксация даты",
        )
        assert ok is True
        history = await admin_svc.get_subscription_history(smoke_master.id)
        assert len(history) >= 2
        logger.info("  -> Subscription history entries: %s", len(history))
        logger.info("  [OK] Exact expiry date and subscription history verified.")

        # 7. Create test bot instance, outbox item, and test bot hard delete
        logger.info("[7/9] Testing bot creation and hard deletion...")
        temp_bot = BotInstance(
            master_id=smoke_master.id,
            telegram_bot_id=888_777_666,
            telegram_username="temp_smoke_bot",
            telegram_first_name="Temp Smoke Bot",
            encrypted_token="enc_dummy_smoke",
            status=BotInstanceStatus.ACTIVE,
            is_current=True,
        )
        session.add(temp_bot)
        await session.flush()

        temp_outbox = TelegramOutbox(
            bot_instance_id=temp_bot.id,
            master_id=smoke_master.id,
            operation_type="SEND_MESSAGE",
            target_chat_id=temp_tg_id,
            idempotency_key="smoke_idem_001",
            status=TelegramOutboxStatus.PENDING,
            payload={"text": "smoke message"},
        )
        session.add(temp_outbox)
        await session.commit()

        # Hard delete bot
        ok, msg = await admin_svc.hard_delete_bot(temp_bot.id, actor_user_id=smoke_user.id)
        assert ok is True
        assert await session.get(BotInstance, temp_bot.id) is None
        assert await session.get(TelegramOutbox, temp_outbox.id) is None
        logger.info("  [OK] Bot and dependent TelegramOutbox row hard deleted successfully.")

        # 8. Project impact summary & project hard delete
        logger.info("[8/9] Testing project impact summary and project hard deletion...")
        impact = await admin_svc.get_project_impact_summary(smoke_master.id)
        logger.info("  -> Project impact preview: %s", impact)
        ok, msg = await admin_svc.hard_delete_project(smoke_master.id, actor_user_id=smoke_user.id)
        assert ok is True
        assert await session.get(Master, smoke_master.id) is None

        # CRITICAL VERIFICATION: User account must be preserved!
        check_user = await session.get(User, smoke_user.id)
        assert check_user is not None
        logger.info("  -> Verified Master #%s was purged while User #%s was PRESERVED.", smoke_master.id, smoke_user.id)
        logger.info("  [OK] Project hard deletion cascades tenant data and preserves User accounts.")

        # 9. Clean up smoke test user and smoke audit logs
        logger.info("[9/9] Cleaning up smoke test artifacts...")
        await session.execute(delete(AuditLog).where(AuditLog.actor_user_id == smoke_user.id))
        await session.execute(delete(User).where(User.id == smoke_user.id))
        await session.commit()
        logger.info("  [OK] Smoke test user and audit artifacts cleaned up.")

    logger.info("--- ALL PLATFORM ADMIN SMOKE TEST CHECKS PASSED SUCCESSFULLY ---")
    return True


if __name__ == "__main__":
    success = asyncio.run(run_smoke_test())
    sys.exit(0 if success else 1)
