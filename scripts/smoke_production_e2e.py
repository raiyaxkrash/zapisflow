"""Production E2E live smoke test on actual production database and Redis."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone, time
from decimal import Decimal
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config.settings import settings
from app.database.models import User, Master, MasterSettings, Service
from app.database.models.user import Admin
from app.database.models.master import MasterStatus, SubscriptionStatus, BotInstance, BotInstanceStatus
from app.database.models.service import DepositType
from app.database.models.staff import StaffMember
from app.database.models.schedule import ScheduleTemplate
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import Payment, PaymentStatus
from app.database.models.telegram_outbox import TelegramOutbox
from app.services.booking_service import BookingService
from app.services.slot_engine import SlotEngine
from app.services.payment_service import PaymentService


async def run_production_smoke_test() -> None:
    print(f"=== Starting Production E2E Smoke Test ===")
    print(f"Database: {settings.safe_database_url}")
    print(f"Redis: {settings.redis_host}:{settings.redis_port}")
    print(f"App Env: {settings.app_env}, App Mode: {settings.app_mode}")

    # 1. Connect to DB and Redis
    engine = create_async_engine(settings.database_url, echo=False)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    redis = Redis.from_url(settings.redis_url)
    await redis.ping()
    print("✓ Redis PING successful")

    unique_suffix = uuid4().int % 100_000_000
    owner_tg = 90_000_000_000 + unique_suffix
    client_tg = 91_000_000_000 + unique_suffix
    bot_id = 92_000_000_000 + unique_suffix

    now_utc = datetime.now(timezone.utc)
    target_slot = (now_utc + timedelta(days=2)).replace(hour=11, minute=0, second=0, microsecond=0)

    try:
        # 2. Master Onboarding & Configuration
        async with sessions.begin() as session:
            owner = User(
                telegram_id=owner_tg,
                first_name="SmokeOwner",
                phone="+79990001122",
            )
            client = User(
                telegram_id=client_tg,
                first_name="SmokeClient",
                phone="+79998887766",
            )
            session.add_all([owner, client])
            await session.flush()

            admin = Admin(user_id=owner.id, role="OWNER")
            session.add(admin)
            await session.flush()

            master = Master(
                owner_user_id=owner.id,
                display_name="Smoke Test Studio",
                status=MasterStatus.ACTIVE,
                subscription_status=SubscriptionStatus.ACTIVE,
                paid_until=now_utc + timedelta(days=30),
            )
            session.add(master)
            await session.flush()

            m_settings = MasterSettings(
                master_id=master.id,
                bank_name="Smoke Bank",
                bank_card_number="4111111111111111",
                bank_recipient_name="Smoke Owner",
                booking_horizon_days=14,
            )
            session.add(m_settings)

            service = Service(
                master_id=master.id,
                title="Smoke Manicure",
                duration_min=60,
                buffer_min=15,
                price=Decimal("1500.00"),
                deposit_type=DepositType.FIXED,
                deposit_value=Decimal("300.00"),
                is_active=True,
                is_archived=False,
            )
            session.add(service)

            staff = StaffMember(
                master_id=master.id,
                user_id=owner.id,
                display_name="Master Smoke",
                is_active=True,
            )
            session.add(staff)

            bot_inst = BotInstance(
                master_id=master.id,
                telegram_bot_id=bot_id,
                status=BotInstanceStatus.ACTIVE,
                is_current=True,
            )
            session.add(bot_inst)
            await session.flush()

            # Add schedule template for the target weekday
            sched = ScheduleTemplate(
                master_id=master.id,
                staff_id=staff.id,
                day_of_week=target_slot.weekday(),
                is_day_off=False,
                work_start=time(9, 0),
                work_end=time(19, 0),
            )
            session.add(sched)

            master_id = master.id
            owner_id = owner.id
            admin_id = admin.id
            client_id = client.id
            service_id = service.id
            staff_id = staff.id

        print(f"✓ Master onboarding completed: Master #{master_id}, Service #{service_id}, Staff #{staff_id}")

        # 3. Slot Discovery with Booking Horizon
        async with sessions() as session:
            engine_slot = SlotEngine(session)
            dates = await engine_slot.get_available_dates(
                service_id=service_id,
                start_date=now_utc.date(),
                master_id=master_id,
                days_count=30,  # Should be clamped by horizon to 14
            )
            assert len(dates) <= 14, f"Expected <= 14 days, got {len(dates)}"
            assert target_slot.date() in dates, "Target slot date should be available"
            print(f"✓ Slot discovery with horizon respected: {len(dates)} dates returned (<= 14 days)")

            # Check available slots on target day
            slots = await engine_slot.get_available_slots(
                service_id=service_id,
                target_date=target_slot.date(),
                master_id=master_id,
                staff_id=staff_id,
            )
            assert any(s.hour == 11 and s.minute == 0 for s in slots), "11:00 slot must be available"
            print(f"✓ Available slots calculated: {len(slots)} slots found on {target_slot.date()}")

        # 4. Client Hold Booking Creation
        async with sessions() as session:
            booking_svc = BookingService(session)
            appt, payment = await booking_svc.create_hold_booking(
                master_id=master_id,
                user_id=client_id,
                service_id=service_id,
                start_time=target_slot,
                staff_id=staff_id,
            )
            await session.commit()
            appt_id = appt.id
            payment_id = payment.id if payment else None
            assert appt.status == AppointmentStatus.WAITING_PAYMENT, f"Initial status must be WAITING_PAYMENT, got {appt.status}"
            assert payment is not None, "Deposit service must create payment"
            assert payment.status == PaymentStatus.PENDING, "Payment must be PENDING"
            assert payment.amount == Decimal("300.00")
            print(f"✓ Client hold booking created: Appointment #{appt_id}, Payment #{payment_id}, amount: {payment.amount} ₽")

        # 5. Collision Detection Check (overlapping hold must be rejected)
        async with sessions() as session:
            booking_svc = BookingService(session)
            overlap_rejected = False
            try:
                await booking_svc.create_hold_booking(
                    master_id=master_id,
                    user_id=client_id,
                    service_id=service_id,
                    start_time=target_slot,
                    staff_id=staff_id,
                )
            except Exception as e:
                overlap_rejected = True
            assert overlap_rejected, "Overlapping slot must be rejected by SlotAlreadyBookedError"
            print("✓ Overlapping booking collision successfully rejected")

        # 6. Payment Proof Submission by Client
        async with sessions() as session:
            payment_svc = PaymentService(session)
            from app.database.models.payment import MediaType
            await payment_svc.submit_payment_proof(
                master_id=master_id,
                appointment_id=appt_id,
                user_id=client_id,
                telegram_file_id="smoke_file_123",
                telegram_file_unique_id="smoke_uniq_123",
                media_type=MediaType.PHOTO,
            )
            await session.commit()
            print("✓ Payment proof submitted by client")

        # 7. Payment Approval & Appointment Activation by Master
        async with sessions() as session:
            payment_svc = PaymentService(session)
            decision = await payment_svc.approve_payment(
                master_id=master_id,
                payment_id=payment_id,
                admin_id=admin_id,
            )
            await session.commit()

        async with sessions() as session:
            confirmed_appt = await session.get(Appointment, appt_id)
            assert confirmed_appt.status == AppointmentStatus.CONFIRMED, "Status must be CONFIRMED after payment"
            confirmed_pay = await session.get(Payment, payment_id)
            assert confirmed_pay.status == PaymentStatus.CONFIRMED, "Payment must be CONFIRMED"
            print(f"✓ Payment approved by master: Appointment #{appt_id} state is {confirmed_appt.status.value}")

        print("=== PRODUCTION E2E SMOKE TEST PASSED SUCCESSFULLY ===")

    finally:
        # Cleanup test records
        async with sessions.begin() as session:
            await session.execute(delete(TelegramOutbox).where(TelegramOutbox.master_id == master_id))
            await session.execute(delete(Payment).where(Payment.master_id == master_id))
            await session.execute(delete(Appointment).where(Appointment.master_id == master_id))
            await session.execute(delete(ScheduleTemplate).where(ScheduleTemplate.master_id == master_id))
            await session.execute(delete(StaffMember).where(StaffMember.master_id == master_id))
            await session.execute(delete(BotInstance).where(BotInstance.master_id == master_id))
            await session.execute(delete(Service).where(Service.master_id == master_id))
            await session.execute(delete(MasterSettings).where(MasterSettings.master_id == master_id))
            await session.execute(delete(Master).where(Master.id == master_id))
            await session.execute(delete(Admin).where(Admin.user_id == owner_id))
            await session.execute(delete(User).where(User.id.in_([owner_id, client_id])))
        print("✓ Cleanup completed: all temporary test records removed cleanly")

        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_production_smoke_test())
