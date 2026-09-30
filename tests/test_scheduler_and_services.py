"""
Unit and integration tests for background jobs (hold cleaner, reminders) and analytics.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest
import pytz

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.notification import Notification, NotificationStatus, NotificationType
from app.database.models.payment import Payment, PaymentStatus
from app.database.models.user import User
from app.scheduler.jobs.hold_cleaner import clean_expired_holds
from app.scheduler.jobs.reminder_worker import send_visit_reminders
from app.services.analytics_service import AnalyticsService


@pytest.mark.asyncio
async def test_clean_expired_holds_job():
    """
    Test that clean_expired_holds marks expired holds as EXPIRED and cancels pending payment.
    """
    mock_bot = AsyncMock()
    mock_session = AsyncMock()

    # Create dummy user and appointment with expired hold
    user = User(id=1, telegram_id=12345678, first_name="Тест")
    now = datetime.now(pytz.UTC)
    app = Appointment(
        id=42,
        user_id=1,
        service_id=10,
        status=AppointmentStatus.WAITING_PAYMENT,
        start_time=now + timedelta(days=2),
        end_time=now + timedelta(days=2, hours=1),
        end_time_with_buffer=now + timedelta(days=2, hours=1, minutes=15),
        hold_until=now - timedelta(minutes=5),  # expired 5 min ago
        snapshot_service_title="Маникюр",
        snapshot_service_price=Decimal("2000.00"),
        snapshot_service_duration_min=60,
        snapshot_buffer_duration_min=15,
        snapshot_deposit_amount=Decimal("500.00"),
        user=user,
    )
    payment = Payment(
        id=101,
        appointment_id=42,
        user_id=1,
        amount=Decimal("500.00"),
        status=PaymentStatus.PENDING,
    )

    # Mock context manager for session maker
    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session
    mock_session_maker.return_value.__aexit__.return_value = None

    # Mock repositories
    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mock_app_repo = AsyncMock()
        mock_app_repo.get_expired_holds.return_value = [app]

        mock_pay_repo = AsyncMock()
        mock_pay_repo.get_by_appointment_id.return_value = payment

        mock_settings_repo = AsyncMock()
        mock_settings_repo.get_value.return_value = "Europe/Moscow"

        mp.setattr(
            "app.scheduler.jobs.hold_cleaner.AppointmentRepository",
            lambda s: mock_app_repo,
        )
        mp.setattr(
            "app.scheduler.jobs.hold_cleaner.PaymentRepository",
            lambda s: mock_pay_repo,
        )
        mp.setattr(
            "app.scheduler.jobs.hold_cleaner.SettingsRepository",
            lambda s: mock_settings_repo,
        )

        cleaned = await clean_expired_holds(mock_bot, session_maker=mock_session_maker)

    assert cleaned == 1
    assert app.status == AppointmentStatus.EXPIRED
    assert app.hold_until is None
    assert payment.status == PaymentStatus.REJECTED
    mock_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_visit_reminders_job():
    """
    Test that send_visit_reminders dispatches 24h reminder when within 24h window.
    """
    mock_bot = AsyncMock()
    mock_session = AsyncMock()

    user = User(id=2, telegram_id=987654321, first_name="Анна")
    now_utc = datetime.now(pytz.UTC)
    app = Appointment(
        id=88,
        user_id=2,
        service_id=5,
        status=AppointmentStatus.CONFIRMED,
        start_time=now_utc + timedelta(hours=18),  # in 18 hours (between 3h and 24h)
        end_time=now_utc + timedelta(hours=19, minutes=30),
        end_time_with_buffer=now_utc + timedelta(hours=19, minutes=45),
        snapshot_service_title="Педикюр",
        snapshot_service_price=Decimal("2500.00"),
        snapshot_service_duration_min=90,
        snapshot_buffer_duration_min=15,
        snapshot_deposit_amount=Decimal("500.00"),
        user=user,
        notifications=[],
    )

    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session
    mock_session_maker.return_value.__aexit__.return_value = None
    mock_session.add = MagicMock()

    # Mock execute result
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [app]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.MonkeyPatch.context() as mp:
        mock_settings_repo = AsyncMock()
        mock_settings_repo.get_value.side_effect = lambda k, default: "Europe/Moscow" if k == "timezone" else default
        mp.setattr(
            "app.scheduler.jobs.reminder_worker.SettingsRepository",
            lambda s: mock_settings_repo,
        )

        sent_count = await send_visit_reminders(mock_bot, session_maker=mock_session_maker)

    assert sent_count == 1
    mock_bot.send_message.assert_awaited_once()
    mock_session.add.assert_called_once()
    saved_notification = mock_session.add.call_args[0][0]
    assert saved_notification.type == NotificationType.REMINDER_24H
    assert saved_notification.status == NotificationStatus.SENT


@pytest.mark.asyncio
async def test_analytics_service_metrics():
    """
    Test AnalyticsService metrics calculations and aggregations.
    """
    mock_session = AsyncMock()
    analytics_svc = AnalyticsService(mock_session)

    # Mock aggregations row
    class MockRow:
        total = 10
        completed = 8
        confirmed = 1
        cancelled = 1
        no_show = 0
        revenue = Decimal("24000.00")
        unique_clients = 7

    mock_result1 = MagicMock()
    mock_result1.one.return_value = MockRow()

    # Mock top services
    class MockSvcRow:
        def __init__(self, title, count, rev):
            self.title = title
            self.count = count
            self.svc_revenue = rev

    mock_result2 = MagicMock()
    mock_result2.all.return_value = [
        MockSvcRow("Ламинирование", 5, Decimal("15000.00")),
        MockSvcRow("Маникюр", 3, Decimal("9000.00")),
    ]

    # Mock retained deposits
    mock_result3 = MagicMock()
    mock_result3.scalar.return_value = Decimal("500.00")

    mock_session.execute.side_effect = [mock_result1, mock_result2, mock_result3]

    metrics = await analytics_svc.get_metrics_for_range(master_id=1)

    assert metrics["total"] == 10
    assert metrics["completed"] == 8
    assert metrics["revenue"] == Decimal("24000.00")
    assert metrics["retained_deposits"] == Decimal("500.00")
    assert metrics["total_income"] == Decimal("24500.00")
    assert metrics["avg_ticket"] == Decimal("3000.00")  # 24000 / 8
    assert metrics["completion_rate"] == 80.0
    assert len(metrics["top_services"]) == 2
