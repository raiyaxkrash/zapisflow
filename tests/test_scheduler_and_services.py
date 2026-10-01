"""
Unit and integration tests for background jobs (hold cleaner, reminders) and analytics.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest
import pytz

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.user import User
from app.scheduler.jobs.hold_cleaner import clean_expired_holds
from app.services.analytics_service import AnalyticsService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commit_fails, enqueue_fails, expected_count",
    [
        pytest.param(False, False, 1, id="expiry-and-outbox-commit"),
        pytest.param(True, False, 0, id="commit-failure-rolls-back"),
        pytest.param(False, True, 0, id="outbox-failure-rolls-back-expiry"),
    ],
)
async def test_clean_expired_holds_job(
    commit_fails, enqueue_fails, expected_count, caplog
):
    """
    Expiry and durable notification intent commit or roll back together.
    """
    mock_bot = AsyncMock()
    mock_session = AsyncMock()
    if commit_fails:
        mock_session.commit.side_effect = RuntimeError("database commit failed")

    enqueue = AsyncMock()
    if enqueue_fails:
        enqueue.side_effect = RuntimeError("outbox unavailable")

    # Create dummy user and appointment with expired hold
    user = User(id=1, telegram_id=12345678, first_name="Тест")
    now = datetime.now(pytz.UTC)
    app = Appointment(
        id=42,
        master_id=1,
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

        mock_booking_service = AsyncMock()
        mock_booking_service.expire_booking.return_value = app

        mock_settings_repo = AsyncMock()
        mock_settings_repo.get_value.return_value = "Europe/Moscow"

        mock_bot_repo = AsyncMock()
        mock_bot_repo.get_active_by_master_id.return_value = MagicMock(id=7)

        mp.setattr(
            "app.scheduler.jobs.hold_cleaner.AppointmentRepository",
            lambda s: mock_app_repo,
        )
        mp.setattr(
            "app.scheduler.jobs.hold_cleaner.BookingService",
            lambda s: mock_booking_service,
        )
        mp.setattr(
            "app.scheduler.jobs.hold_cleaner.MasterSettingsRepository",
            lambda s: mock_settings_repo,
        )
        mp.setattr("app.scheduler.jobs.hold_cleaner.BotInstanceRepository", lambda s: mock_bot_repo)
        mp.setattr("app.scheduler.jobs.hold_cleaner.enqueue_telegram_message", enqueue)

        cleaned = await clean_expired_holds(mock_bot, session_maker=mock_session_maker)

    assert cleaned == expected_count
    mock_booking_service.expire_booking.assert_awaited_once_with(app.id, master_id=1)
    if enqueue_fails:
        mock_session.commit.assert_not_awaited()
    else:
        mock_session.commit.assert_awaited_once()
    if commit_fails or enqueue_fails:
        mock_session.rollback.assert_awaited_once()
    else:
        mock_session.rollback.assert_not_awaited()
    enqueue.assert_awaited_once()
    mock_bot.send_message.assert_not_awaited()


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
