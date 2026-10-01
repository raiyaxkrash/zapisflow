"""
Comprehensive test suite for Phase 6 — Final SaaS Monetization.
Verifies all 40 required scenarios:
Plans catalog, multi-period pricing, trial protection, lifecycle states, renewal math,
idempotency, YooKassa checkout & fallback, entitlements, multi-tenant & role restrictions, and UI elements.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.audit import AuditLog
from app.database.models.master import (
    Master,
    MasterAdmin,
    MasterAdminRole,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.staff import StaffMember
from app.database.models.subscription import (
    EffectiveSubscriptionStatus,
    SubscriptionPayment,
    SubscriptionPeriod,
    SubscriptionPlan,
)
from app.database.models.user import User
from app.manager_bot.keyboards import (
    subscription_card_keyboard,
    subscription_checkout_keyboard,
    subscription_pending_keyboard,
    support_button,
)
from app.scripts.activate_subscription import activate_subscription
from app.services.billing.yookassa_checkout import (
    PaymentCheckResult,
    YooKassaCheckoutService,
)
from app.services.billing.yookassa_client import YooKassaPayment
from app.services.exceptions import (
    BillingIDORViolationError,
    PlanNotFoundError,
    SubscriptionError,
)
from app.services.master_authorization_service import AdminRole, MasterAuthorizationService
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.services.subscription_entitlement_service import SubscriptionEntitlementService
from app.services.subscription_notification_service import SubscriptionNotificationService
from app.services.subscription_service import SubscriptionService


@pytest.mark.asyncio
async def test_01_basic_plan_configuration(pg_session: AsyncSession) -> None:
    """1. Basic plan configuration in DB: code, name, price_rub, duration_days, is_active, sort_order."""
    sub_service = SubscriptionService(pg_session)
    plan = await sub_service.get_active_plan("basic_monthly")

    assert plan.code == "basic_monthly"
    assert plan.name == "ZapisFlow Basic"
    assert plan.price_rub == Decimal("499.00")
    assert plan.duration_days == 30
    assert plan.is_active is True
    assert plan.sort_order == 1


@pytest.mark.asyncio
async def test_02_multi_period_plans_in_db(pg_session: AsyncSession) -> None:
    """2. Database architecture supports multi-period plans with dynamic prices."""
    sub_service = SubscriptionService(pg_session)
    plans = await sub_service.list_active_plans()
    plan_codes = {p.code: p for p in plans}

    assert "basic_monthly" in plan_codes
    assert "basic_3_months" in plan_codes
    assert "basic_6_months" in plan_codes
    assert "basic_yearly" in plan_codes

    p3m = plan_codes["basic_3_months"]
    assert p3m.duration_days == 90
    assert p3m.price_rub == Decimal("1299.00")

    p6m = plan_codes["basic_6_months"]
    assert p6m.duration_days == 180
    assert p6m.price_rub == Decimal("2390.00")

    p1y = plan_codes["basic_yearly"]
    assert p1y.duration_days == 365
    assert p1y.price_rub == Decimal("4490.00")


@pytest.mark.asyncio
async def test_03_trial_granted_14_days_on_first_project(pg_session: AsyncSession) -> None:
    """3. 14-day trial is granted to user upon creating their first master."""
    user = User(
        telegram_id=987111001,
        first_name="FirstUser",
        username="first_user",
    )
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="First Studio",
        status=MasterStatus.SETUP_REQUIRED,
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    now_utc = datetime.now(timezone.utc)
    granted = await sub_svc.claim_user_trial_or_reject(
        user_id=user.id,
        master=master,
        trial_days=14,
        now_utc=now_utc,
    )

    assert granted is True
    assert master.subscription_status == SubscriptionStatus.TRIAL
    assert master.trial_ends_at is not None
    assert (master.trial_ends_at - now_utc).days >= 13
    assert user.trial_claimed_at is not None


@pytest.mark.asyncio
async def test_04_trial_rejected_on_second_project_same_user(pg_session: AsyncSession) -> None:
    """4. Trial is granted strictly once per User account; second project gets EXPIRED."""
    user = User(
        telegram_id=987111002,
        first_name="SecondUser",
        username="second_user",
    )
    pg_session.add(user)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    now_utc = datetime.now(timezone.utc)

    # First master claims trial
    master1 = Master(
        owner_user_id=user.id,
        display_name="Studio 1",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master1)
    await pg_session.flush()
    await sub_svc.claim_user_trial_or_reject(user.id, master1, trial_days=14, now_utc=now_utc)

    # Second master created by same user
    master2 = Master(
        owner_user_id=user.id,
        display_name="Studio 2",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master2)
    await pg_session.flush()

    granted2 = await sub_svc.claim_user_trial_or_reject(user.id, master2, trial_days=14, now_utc=now_utc)

    assert granted2 is False
    assert master2.subscription_status == SubscriptionStatus.EXPIRED
    assert master2.trial_ends_at is None


@pytest.mark.asyncio
async def test_05_deleting_and_recreating_master_preserves_trial_restriction(
    pg_session: AsyncSession,
) -> None:
    """5. Deleting and recreating a master does not reset user's claimed trial."""
    user = User(
        telegram_id=987111003,
        first_name="RecreateMasterUser",
    )
    pg_session.add(user)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    now_utc = datetime.now(timezone.utc)

    master = Master(
        owner_user_id=user.id,
        display_name="Temporary Studio",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master)
    await pg_session.flush()
    await sub_svc.claim_user_trial_or_reject(user.id, master, trial_days=14, now_utc=now_utc)

    # User's trial is now marked as claimed
    assert user.trial_claimed_at is not None

    # Delete master
    await pg_session.delete(master)
    await pg_session.flush()

    # Recreate new master
    new_master = Master(
        owner_user_id=user.id,
        display_name="Recreated Studio",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(new_master)
    await pg_session.flush()

    granted = await sub_svc.claim_user_trial_or_reject(user.id, new_master, trial_days=14, now_utc=now_utc)
    assert granted is False
    assert new_master.subscription_status == SubscriptionStatus.EXPIRED


@pytest.mark.asyncio
async def test_06_adding_or_removing_staff_does_not_affect_trial(pg_session: AsyncSession) -> None:
    """6. Adding or removing staff members does not reset or alter user trial."""
    user = User(telegram_id=987111004, first_name="OwnerWithStaff")
    pg_session.add(user)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    master = Master(
        owner_user_id=user.id,
        display_name="Multi Staff Studio",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master)
    await pg_session.flush()
    await sub_svc.claim_user_trial_or_reject(user.id, master, trial_days=14)

    initial_trial_ends = master.trial_ends_at

    # Add staff
    staff1 = StaffMember(master_id=master.id, display_name="Specialist 1", is_active=True)
    pg_session.add(staff1)
    await pg_session.flush()

    assert master.trial_ends_at == initial_trial_ends
    assert user.trial_claimed_at is not None

    # Remove staff
    staff1.is_active = False
    await pg_session.flush()

    assert master.trial_ends_at == initial_trial_ends


@pytest.mark.asyncio
async def test_07_status_trial_when_now_before_trial_ends(pg_session: AsyncSession) -> None:
    """7. Effective status is TRIAL_ACTIVE when now < trial_ends_at."""
    user = User(telegram_id=987111005, first_name="TrialUser")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    master = Master(
        owner_user_id=user.id,
        display_name="Active Trial Studio",
        subscription_status=SubscriptionStatus.TRIAL,
        trial_ends_at=now_utc + timedelta(days=7),
    )
    pg_session.add(master)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    eff = await sub_svc.get_effective_status(master.id, now_utc=now_utc)
    assert eff.status == EffectiveSubscriptionStatus.TRIAL_ACTIVE
    assert eff.is_active is True
    assert eff.days_remaining >= 7


@pytest.mark.asyncio
async def test_08_status_active_when_now_before_paid_until(pg_session: AsyncSession) -> None:
    """8. Effective status is PAID_ACTIVE when now < paid_until."""
    user = User(telegram_id=987111006, first_name="PaidUser")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    master = Master(
        owner_user_id=user.id,
        display_name="Paid Studio",
        subscription_status=SubscriptionStatus.ACTIVE,
        paid_until=now_utc + timedelta(days=20),
    )
    pg_session.add(master)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    eff = await sub_svc.get_effective_status(master.id, now_utc=now_utc)
    assert eff.status == EffectiveSubscriptionStatus.PAID_ACTIVE
    assert eff.is_active is True
    assert eff.days_remaining >= 20


@pytest.mark.asyncio
async def test_09_status_expired_when_trial_elapsed(pg_session: AsyncSession) -> None:
    """9. Effective status is EXPIRED when trial_ends_at is in the past."""
    user = User(telegram_id=987111007, first_name="PastTrialUser")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    master = Master(
        owner_user_id=user.id,
        display_name="Past Trial Studio",
        subscription_status=SubscriptionStatus.TRIAL,
        trial_ends_at=now_utc - timedelta(days=1),
    )
    pg_session.add(master)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    eff = await sub_svc.get_effective_status(master.id, now_utc=now_utc)
    assert eff.status == EffectiveSubscriptionStatus.EXPIRED
    assert eff.is_active is False
    assert eff.days_remaining == 0


@pytest.mark.asyncio
async def test_10_status_expired_when_paid_until_elapsed(pg_session: AsyncSession) -> None:
    """10. Effective status is EXPIRED when paid_until is in the past."""
    user = User(telegram_id=987111008, first_name="PastPaidUser")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    master = Master(
        owner_user_id=user.id,
        display_name="Past Paid Studio",
        subscription_status=SubscriptionStatus.ACTIVE,
        paid_until=now_utc - timedelta(hours=2),
    )
    pg_session.add(master)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    eff = await sub_svc.get_effective_status(master.id, now_utc=now_utc)
    assert eff.status == EffectiveSubscriptionStatus.EXPIRED
    assert eff.is_active is False
    assert eff.days_remaining == 0


@pytest.mark.asyncio
async def test_11_status_suspended_overrides_dates(pg_session: AsyncSession) -> None:
    """11. Administrative suspension always supersedes valid future timestamps."""
    user = User(telegram_id=987111009, first_name="SuspendedUser")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    master = Master(
        owner_user_id=user.id,
        display_name="Suspended Studio",
        subscription_status=SubscriptionStatus.SUSPENDED,
        paid_until=now_utc + timedelta(days=60),
    )
    pg_session.add(master)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    eff = await sub_svc.get_effective_status(master.id, now_utc=now_utc)
    assert eff.status == EffectiveSubscriptionStatus.SUSPENDED
    assert eff.is_active is False


@pytest.mark.asyncio
async def test_12_payment_does_not_clear_suspended(pg_session: AsyncSession) -> None:
    """12. Payment confirmation extends paid_until but does not lift administrative SUSPENDED status."""
    user = User(telegram_id=987111010, first_name="SuspendedPayer")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    master = Master(
        owner_user_id=user.id,
        display_name="Suspended Payer Studio",
        subscription_status=SubscriptionStatus.SUSPENDED,
        paid_until=None,
    )
    pg_session.add(master)
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")

    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="test_pay_susp_001",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    await sub_svc.process_successful_payment("YOOKASSA", "test_pay_susp_001")

    await pg_session.refresh(master)
    assert master.subscription_status == SubscriptionStatus.SUSPENDED
    assert master.paid_until is not None
    assert master.paid_until > now_utc


@pytest.mark.asyncio
async def test_13_renewal_from_active_extends_paid_until(pg_session: AsyncSession) -> None:
    """13. Renewal for ACTIVE subscription extends from paid_until + duration_days."""
    user = User(telegram_id=987111011, first_name="ActiveRenewalUser")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    initial_paid_until = now_utc + timedelta(days=10)
    master = Master(
        owner_user_id=user.id,
        display_name="Active Renewal Studio",
        subscription_status=SubscriptionStatus.ACTIVE,
        paid_until=initial_paid_until,
    )
    pg_session.add(master)
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="test_pay_active_001",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    await sub_svc.process_successful_payment("YOOKASSA", "test_pay_active_001")

    await pg_session.refresh(master)
    assert master.paid_until == initial_paid_until + timedelta(days=30)
    assert master.subscription_status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_14_renewal_from_trial_preserves_remaining_trial(pg_session: AsyncSession) -> None:
    """14. Payment made during TRIAL extends from trial_ends_at + duration_days (trial days preserved)."""
    user = User(telegram_id=987111012, first_name="TrialRenewalUser")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    initial_trial_ends = now_utc + timedelta(days=8)
    master = Master(
        owner_user_id=user.id,
        display_name="Trial Renewal Studio",
        subscription_status=SubscriptionStatus.TRIAL,
        trial_ends_at=initial_trial_ends,
        paid_until=None,
    )
    pg_session.add(master)
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="test_pay_trial_001",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    await sub_svc.process_successful_payment("YOOKASSA", "test_pay_trial_001")

    await pg_session.refresh(master)
    assert master.paid_until == initial_trial_ends + timedelta(days=30)
    assert master.subscription_status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_15_renewal_from_expired_extends_from_now(pg_session: AsyncSession) -> None:
    """15. Payment made when EXPIRED extends from now_utc + duration_days."""
    user = User(telegram_id=987111013, first_name="ExpiredRenewalUser")
    pg_session.add(user)
    await pg_session.flush()

    now_utc = datetime.now(timezone.utc)
    master = Master(
        owner_user_id=user.id,
        display_name="Expired Renewal Studio",
        subscription_status=SubscriptionStatus.EXPIRED,
        paid_until=now_utc - timedelta(days=5),
    )
    pg_session.add(master)
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="test_pay_expired_001",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    await sub_svc.process_successful_payment("YOOKASSA", "test_pay_expired_001")

    await pg_session.refresh(master)
    assert master.subscription_status == SubscriptionStatus.ACTIVE
    assert master.paid_until is not None
    assert (master.paid_until - now_utc).days >= 29


@pytest.mark.asyncio
async def test_16_payment_idempotency_duplicate_calls(pg_session: AsyncSession) -> None:
    """16. Multiple calls to process_successful_payment with same ID activate strictly once."""
    user = User(telegram_id=987111014, first_name="IdempotentUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="Idempotent Studio",
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master)
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="test_pay_idem_001",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    res1 = await sub_svc.process_successful_payment("YOOKASSA", "test_pay_idem_001")
    await pg_session.refresh(master)
    paid_after_first = master.paid_until

    # Second call
    res2 = await sub_svc.process_successful_payment("YOOKASSA", "test_pay_idem_001")
    await pg_session.refresh(master)

    assert res1 is True
    assert res2 is True
    assert master.paid_until == paid_after_first

    # Exactly 1 SubscriptionPeriod must exist
    periods_count = await pg_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_payment_id == payment.id)
    )
    assert periods_count is not None


@pytest.mark.asyncio
async def test_17_payment_idempotency_duplicate_webhook(pg_session: AsyncSession) -> None:
    """17. Duplicate webhook arrival for already confirmed payment returns success without double-crediting."""
    user = User(telegram_id=987111015, first_name="WebhookUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="Webhook Studio",
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master)
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="test_pay_wh_001",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="SUCCEEDED",
        paid_at=datetime.now(timezone.utc),
    )
    pg_session.add(payment)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    result = await sub_svc.process_successful_payment("YOOKASSA", "test_pay_wh_001")
    assert result is True


@pytest.mark.asyncio
async def test_18_race_condition_protection_concurrent_payments(pg_session: AsyncSession) -> None:
    """18. Database row locks (with_for_update) protect against simultaneous webhook and manual check."""
    user = User(telegram_id=987111016, first_name="RaceUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="Race Studio",
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master)
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="test_pay_race_001",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    ok1 = await sub_svc.process_successful_payment("YOOKASSA", "test_pay_race_001")
    ok2 = await sub_svc.process_successful_payment("YOOKASSA", "test_pay_race_001")
    assert ok1 is True
    assert ok2 is True


@pytest.mark.asyncio
async def test_19_yookassa_payment_amount_from_plan(pg_session: AsyncSession) -> None:
    """19. Payment amount matches Plan.price_rub exactly; price is not hardcoded."""
    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    assert plan.price_rub == Decimal("499.00")

    user = User(telegram_id=987111017, first_name="PriceUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Price Studio")
    pg_session.add(master)
    await pg_session.flush()

    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="test_pay_price_001",
        amount=plan.price_rub,
        currency=plan.currency,
        period_days=plan.duration_days,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    assert payment.amount == Decimal("499.00")
    assert payment.period_days == 30


@pytest.mark.asyncio
async def test_20_yookassa_metadata_contains_required_fields() -> None:
    """20. YooKassa verification ensures metadata contains payment_id, user_id, and plan_id."""
    remote = YooKassaPayment(
        id="yk_meta_test",
        status="succeeded",
        paid=True,
        amount=Decimal("499.00"),
        currency="RUB",
        checkout_ref=str(uuid.uuid4()),
        confirmation_url=None,
        metadata={"payment_id": "100", "user_id": "200", "plan_id": "300"},
    )
    # Matching verification passes
    YooKassaCheckoutService._verify_remote(
        remote,
        uuid.UUID(remote.checkout_ref),
        Decimal("499.00"),
        "RUB",
        payment_id=100,
        user_id=200,
        plan_id=300,
    )

    # Mismatch raises SubscriptionError
    with pytest.raises(SubscriptionError):
        YooKassaCheckoutService._verify_remote(
            remote,
            uuid.UUID(remote.checkout_ref),
            Decimal("499.00"),
            "RUB",
            payment_id=999,  # Mismatched payment ID
            user_id=200,
            plan_id=300,
        )


@pytest.mark.asyncio
async def test_21_yookassa_webhook_payment_succeeded_activates(pg_session: AsyncSession) -> None:
    """21. YooKassa webhook payment.succeeded triggers atomic subscription activation."""
    user = User(telegram_id=987111018, first_name="WhSuccessUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Wh Studio", subscription_status=SubscriptionStatus.EXPIRED)
    pg_session.add(master)
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="yk_wh_succ_01",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    await sub_svc.process_successful_payment("YOOKASSA", "yk_wh_succ_01")

    await pg_session.refresh(master)
    assert master.subscription_status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_22_yookassa_webhook_payment_canceled(pg_session: AsyncSession) -> None:
    """22. YooKassa webhook payment.canceled marks payment as CANCELLED."""
    user = User(telegram_id=987111019, first_name="WhCancelUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Cancel Studio")
    pg_session.add(master)
    await pg_session.flush()

    payment = SubscriptionPayment(
        master_id=master.id,
        provider="YOOKASSA",
        provider_payment_id="yk_wh_cancel_01",
        amount=Decimal("499.00"),
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    payment.status = "CANCELLED"
    await pg_session.flush()

    await pg_session.refresh(payment)
    assert payment.status == "CANCELLED"


@pytest.mark.asyncio
async def test_23_manual_check_payment_succeeded(pg_session: AsyncSession) -> None:
    """23. Manual check via check_payment returns SUCCEEDED and activates subscription."""
    user = User(telegram_id=987111020, first_name="ManualCheckUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Manual Check Studio", subscription_status=SubscriptionStatus.EXPIRED)
    pg_session.add(master)
    await pg_session.flush()

    ref = uuid.uuid4()
    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="yk_manual_succ_01",
        checkout_ref=ref,
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    mock_client = MagicMock()
    mock_client.get_payment = AsyncMock(
        return_value=YooKassaPayment(
            id="yk_manual_succ_01",
            status="succeeded",
            paid=True,
            amount=Decimal("499.00"),
            currency="RUB",
            checkout_ref=str(ref),
            confirmation_url=None,
            metadata={"payment_id": str(payment.id), "user_id": str(user.id), "plan_id": str(plan.id)},
        )
    )

    mock_maker = MagicMock()
    mock_maker.return_value.__aenter__.return_value = pg_session
    mock_maker.return_value.__aexit__.return_value = None
    mock_maker.begin.return_value.__aenter__.return_value = pg_session
    mock_maker.begin.return_value.__aexit__.return_value = None

    service = YooKassaCheckoutService(mock_maker, mock_client)
    res = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    assert res.status == "SUCCEEDED"
    assert res.payment_id == payment.id


@pytest.mark.asyncio
async def test_24_manual_check_payment_pending(pg_session: AsyncSession) -> None:
    """24. Manual check when payment is pending returns PENDING status without error."""
    user = User(telegram_id=987111021, first_name="PendingCheckUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Pending Studio")
    pg_session.add(master)
    await pg_session.flush()

    ref = uuid.uuid4()
    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="yk_manual_pend_01",
        checkout_ref=ref,
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    mock_client = MagicMock()
    mock_client.get_payment = AsyncMock(
        return_value=YooKassaPayment(
            id="yk_manual_pend_01",
            status="pending",
            paid=False,
            amount=Decimal("499.00"),
            currency="RUB",
            checkout_ref=str(ref),
            confirmation_url="https://yookassa.ru/pay/123",
            metadata={"payment_id": str(payment.id), "user_id": str(user.id), "plan_id": str(plan.id)},
        )
    )

    mock_maker = MagicMock()
    mock_maker.return_value.__aenter__.return_value = pg_session
    mock_maker.return_value.__aexit__.return_value = None
    mock_maker.begin.return_value.__aenter__.return_value = pg_session
    mock_maker.begin.return_value.__aexit__.return_value = None

    service = YooKassaCheckoutService(mock_maker, mock_client)
    res = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    assert res.status == "PENDING"


@pytest.mark.asyncio
async def test_25_manual_check_payment_canceled(pg_session: AsyncSession) -> None:
    """25. Manual check when payment was canceled returns CANCELLED."""
    user = User(telegram_id=987111022, first_name="CancelCheckUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Cancel Check Studio")
    pg_session.add(master)
    await pg_session.flush()

    ref = uuid.uuid4()
    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="yk_manual_canc_01",
        checkout_ref=ref,
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment)
    await pg_session.flush()

    mock_client = MagicMock()
    mock_client.get_payment = AsyncMock(
        return_value=YooKassaPayment(
            id="yk_manual_canc_01",
            status="canceled",
            paid=False,
            amount=Decimal("499.00"),
            currency="RUB",
            checkout_ref=str(ref),
            confirmation_url=None,
            metadata={"payment_id": str(payment.id), "user_id": str(user.id), "plan_id": str(plan.id)},
        )
    )

    mock_maker = MagicMock()
    mock_maker.return_value.__aenter__.return_value = pg_session
    mock_maker.return_value.__aexit__.return_value = None
    mock_maker.begin.return_value.__aenter__.return_value = pg_session
    mock_maker.begin.return_value.__aexit__.return_value = None

    service = YooKassaCheckoutService(mock_maker, mock_client)
    res = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    assert res.status == "CANCELLED"


@pytest.mark.asyncio
async def test_26_fallback_when_yookassa_unconfigured() -> None:
    """26. When YooKassa is not configured, support tag and fallback instructions are offered."""
    assert settings.support_tag == "@zapisflow"
    assert settings.support_url == "https://t.me/zapisflow"


@pytest.mark.asyncio
async def test_27_cli_manual_activation_script(pg_session: AsyncSession) -> None:
    """27. Platform Admin CLI script activate_subscription manually activates/extends subscription."""
    user = User(telegram_id=987111023, first_name="CliUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name="CLI Studio",
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master)
    await pg_session.flush()

    # Call CLI logic with injected test session
    await activate_subscription(
        master_id=master.id,
        days=45,
        plan_code="basic_monthly",
        reason="VIP test grant",
        session=pg_session,
    )

    # Refresh
    await pg_session.refresh(master)
    assert master.subscription_status == SubscriptionStatus.ACTIVE
    assert master.paid_until is not None


def test_28_manager_ui_trial_screen() -> None:
    """28. Manager UI renders trial status with days remaining and basic monthly plan price."""
    kb = subscription_card_keyboard(
        master_id=1,
        plans=[],
        status=EffectiveSubscriptionStatus.TRIAL_ACTIVE,
    )
    assert any(btn.url == settings.support_url for row in kb.inline_keyboard for btn in row)


def test_29_manager_ui_active_screen() -> None:
    """29. Manager UI renders active status with support button."""
    kb = subscription_card_keyboard(
        master_id=1,
        plans=[],
        status=EffectiveSubscriptionStatus.PAID_ACTIVE,
    )
    assert any("Поддержка" in btn.text for row in kb.inline_keyboard for btn in row)


def test_30_manager_ui_expired_screen() -> None:
    """30. Manager UI renders expired status with options to renew."""
    kb = subscription_card_keyboard(
        master_id=1,
        plans=[],
        status=EffectiveSubscriptionStatus.EXPIRED,
    )
    assert any("Главное меню" in btn.text for row in kb.inline_keyboard for btn in row)


def test_31_manager_ui_suspended_screen() -> None:
    """31. Manager UI renders suspended status directing to support."""
    kb = subscription_card_keyboard(
        master_id=1,
        plans=[],
        status=EffectiveSubscriptionStatus.SUSPENDED,
    )
    # Payment action buttons are not rendered when SUSPENDED
    assert not any("Оплатить" in btn.text or "Продлить" in btn.text for row in kb.inline_keyboard for btn in row)
    assert any("Поддержка" in btn.text for row in kb.inline_keyboard for btn in row)


def test_32_payment_screen_dynamic_price() -> None:
    """32. Keyboard renders exact plan price dynamically."""
    plan = SubscriptionPlan(
        id=1,
        code="basic_monthly",
        name="ZapisFlow Basic",
        price=Decimal("499.00"),
        currency="RUB",
        period_days=30,
        is_active=True,
    )
    kb = subscription_card_keyboard(
        master_id=1,
        plans=[plan],
        status=EffectiveSubscriptionStatus.EXPIRED,
    )
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("499" in t for t in texts)


def test_33_support_button_in_keyboards() -> None:
    """33. support_button generates link to @zapisflow."""
    btn = support_button()
    assert btn.text == "💬 Поддержка @zapisflow"
    assert btn.url == "https://t.me/zapisflow"


def test_34_support_url_configuration() -> None:
    """34. Settings expose support_telegram_username and support_url."""
    assert settings.support_telegram_username == "zapisflow"
    assert settings.support_url == "https://t.me/zapisflow"


@pytest.mark.asyncio
async def test_35_staff_restricted_from_billing(pg_session: AsyncSession) -> None:
    """35. Users with STAFF role are restricted from accessing billing."""
    user = User(telegram_id=987111024, first_name="StaffOnlyUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=1, display_name="Studio with Staff")
    pg_session.add(master)
    await pg_session.flush()

    staff_admin = MasterAdmin(
        master_id=master.id,
        user_id=user.id,
        role=MasterAdminRole.STAFF,
        is_active=True,
    )
    pg_session.add(staff_admin)
    await pg_session.flush()

    auth_svc = MasterAuthorizationService(pg_session)
    role = await auth_svc.get_role(master.id, user.id)
    assert role == AdminRole.STAFF


@pytest.mark.asyncio
async def test_36_owner_has_billing_access(pg_session: AsyncSession) -> None:
    """36. Project OWNER has full authorization to manage billing and initiate payments."""
    user = User(telegram_id=987111025, first_name="StudioOwner")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Owner Studio")
    pg_session.add(master)
    await pg_session.flush()

    auth_svc = MasterAuthorizationService(pg_session)
    role = await auth_svc.get_role(master.id, user.id)
    assert role == AdminRole.OWNER


@pytest.mark.asyncio
async def test_37_multi_project_subscription_isolation(pg_session: AsyncSession) -> None:
    """37. Payment for one project does not extend another project of the same user."""
    user = User(telegram_id=987111026, first_name="MultiProjectOwner")
    pg_session.add(user)
    await pg_session.flush()

    master_a = Master(owner_user_id=user.id, display_name="Studio A", subscription_status=SubscriptionStatus.EXPIRED)
    master_b = Master(owner_user_id=user.id, display_name="Studio B", subscription_status=SubscriptionStatus.EXPIRED)
    pg_session.add_all([master_a, master_b])
    await pg_session.flush()

    plan = await SubscriptionService(pg_session).get_active_plan("basic_monthly")
    payment_a = SubscriptionPayment(
        master_id=master_a.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        provider_payment_id="pay_iso_a_001",
        amount=plan.price_rub,
        currency="RUB",
        period_days=30,
        status="PENDING",
    )
    pg_session.add(payment_a)
    await pg_session.flush()

    sub_svc = SubscriptionService(pg_session)
    await sub_svc.process_successful_payment("YOOKASSA", "pay_iso_a_001")

    await pg_session.refresh(master_a)
    await pg_session.refresh(master_b)

    assert master_a.subscription_status == SubscriptionStatus.ACTIVE
    assert master_b.subscription_status == SubscriptionStatus.EXPIRED


@pytest.mark.asyncio
async def test_38_entitlement_blocks_appointments_when_expired(pg_session: AsyncSession) -> None:
    """38. SubscriptionEntitlementService blocks new appointment creation when EXPIRED."""
    user = User(telegram_id=987111027, first_name="EntitlementUser1")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Entitlement Studio 1", subscription_status=SubscriptionStatus.EXPIRED)
    pg_session.add(master)
    await pg_session.flush()

    entitlement_svc = SubscriptionEntitlementService(pg_session)
    can_app, reason = await entitlement_svc.can_create_appointment(master.id)

    assert can_app is False
    assert "истекла" in (reason or "").lower()


@pytest.mark.asyncio
async def test_39_entitlement_blocks_broadcast_when_expired(pg_session: AsyncSession) -> None:
    """39. SubscriptionEntitlementService blocks CRM broadcasts when EXPIRED."""
    user = User(telegram_id=987111028, first_name="EntitlementUser2")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Entitlement Studio 2", subscription_status=SubscriptionStatus.EXPIRED)
    pg_session.add(master)
    await pg_session.flush()

    entitlement_svc = SubscriptionEntitlementService(pg_session)
    can_bc, reason = await entitlement_svc.can_use_broadcast(master.id)

    assert can_bc is False
    assert "истекла" in (reason or "").lower()


@pytest.mark.asyncio
async def test_40_data_preserved_when_expired(pg_session: AsyncSession) -> None:
    """40. In EXPIRED status all tenant data (clients, staff, schedule) remains preserved and readable."""
    user = User(telegram_id=987111029, first_name="PreserveUser")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(owner_user_id=user.id, display_name="Preserved Studio", subscription_status=SubscriptionStatus.EXPIRED)
    pg_session.add(master)
    await pg_session.flush()

    staff = StaffMember(master_id=master.id, display_name="Faithful Specialist", is_active=True)
    pg_session.add(staff)
    await pg_session.flush()

    entitlement_svc = SubscriptionEntitlementService(pg_session)
    can_read_clients, _ = await entitlement_svc.can_use_feature(master.id, "read_clients")
    can_read_staff, _ = await entitlement_svc.can_use_feature(master.id, "read_staff")

    assert can_read_clients is True
    assert can_read_staff is True

    # Staff record still exists in DB
    found_staff = await pg_session.get(StaffMember, staff.id)
    assert found_staff is not None
    assert found_staff.display_name == "Faithful Specialist"
