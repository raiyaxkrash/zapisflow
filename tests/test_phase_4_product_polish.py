"""Comprehensive tests for Phase 4: Product Polish, Master Onboarding, Services, Portfolio, Schedule, Dashboard and CRM Export."""

from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal
import io
import csv
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.client.about import cb_client_reviews
from app.bot.keyboards.client.menu import get_main_menu_keyboard
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterClient, MasterSettings, MasterStatus, SubscriptionStatus
from app.database.models.portfolio import PortfolioCategory, PortfolioItem
from app.database.models.review import Review
from app.database.models.schedule import ScheduleTemplate
from app.database.models.service import DepositType, Service
from app.database.models.user import User
from app.manager_bot.handlers import (
    DEFAULT_PORTFOLIO_CATEGORIES,
    DEFAULT_SERVICE_SUGGESTIONS,
    cb_crm_export,
    cb_manager_portfolio,
    cb_manager_portfolio_cat_add,
    cb_manager_portfolio_cat_del,
    cb_manager_schedule,
    cb_manager_service_card,
    cb_manager_service_delete,
    cb_manager_service_toggle,
    cb_manager_services_list,
    cb_manager_settings,
    cb_onboarding_accept_schedule,
    cb_onboarding_accept_service,
    cb_onboarding_activity_type,
    cb_project_card,
    msg_manager_service_add_duration,
)
from app.manager_bot.states import MasterOnboardingStates
from app.repositories.master_repository import MasterRepository
from app.repositories.portfolio_repository import PortfolioRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_repository import ServiceRepository
from app.services.crm_service import MasterCrmService


@pytest.fixture
async def master_with_owner(pg_session: AsyncSession):
    """Fixture providing a registered master and owner user."""
    owner = User(
        telegram_id=987654321,
        first_name="Ирина",
        last_name="Мастерова",
        username="irina_master",
    )
    pg_session.add(owner)
    await pg_session.flush()

    master = Master(
        owner_user_id=owner.id,
        display_name="Студия Ирины",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        timezone="Europe/Moscow",
    )
    pg_session.add(master)
    await pg_session.flush()

    settings = MasterSettings(
        master_id=master.id,
        studio_address="г. Москва, ул. Арбат 10",
        studio_phone="+79991112233",
        min_advance_hours=2,
        booking_horizon_days=30,
    )
    pg_session.add(settings)
    await pg_session.flush()
    return master, owner


@pytest.fixture
async def foreign_master(pg_session: AsyncSession):
    """Fixture providing another master owned by a different user for IDOR testing."""
    other_owner = User(
        telegram_id=111222333,
        first_name="Ольга",
        last_name="Чужая",
        username="olga_other",
    )
    pg_session.add(other_owner)
    await pg_session.flush()

    other_master = Master(
        owner_user_id=other_owner.id,
        display_name="Студия Ольги",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        timezone="Europe/Moscow",
    )
    pg_session.add(other_master)
    await pg_session.flush()

    other_settings = MasterSettings(master_id=other_master.id)
    pg_session.add(other_settings)
    await pg_session.flush()
    return other_master, other_owner


# ---------------------------------------------------------------------------
# 1. Onboarding Wizard & Activity Types
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onboarding_activity_type_populates_portfolio_categories(
    pg_session: AsyncSession, master_with_owner
):
    master, owner = master_with_owner
    state = AsyncMock()

    callback = SimpleNamespace(
        data=f"mgr:ob:act:{master.id}:nails",
        from_user=SimpleNamespace(id=owner.telegram_id, first_name=owner.first_name, last_name=owner.last_name, username=owner.username),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await cb_onboarding_activity_type(callback, state, pg_session)

    # Master activity type is updated
    await pg_session.refresh(master)
    assert master.activity_type == "nails"

    # Default portfolio categories created
    port_repo = PortfolioRepository(pg_session)
    cats = await port_repo.list_categories(master.id, active_only=False)
    titles = [c.title for c in cats]
    assert "Маникюр" in titles
    assert "Педикюр" in titles

    state.update_data.assert_awaited_with(onboarding_master_id=master.id, activity_type="nails")
    state.set_state.assert_awaited_with(MasterOnboardingStates.waiting_for_address)


@pytest.mark.asyncio
async def test_onboarding_accept_service_and_schedule(
    pg_session: AsyncSession, master_with_owner
):
    master, owner = master_with_owner
    state = AsyncMock()
    state.get_data.return_value = {
        "suggested_title": "Классическое наращивание",
        "suggested_price": 2500,
        "suggested_duration": 120,
    }

    cb_srv = SimpleNamespace(
        data=f"mgr:ob:srv:accept:{master.id}",
        from_user=SimpleNamespace(id=owner.telegram_id, first_name=owner.first_name, last_name=owner.last_name, username=owner.username),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await cb_onboarding_accept_service(cb_srv, state, pg_session)

    srv_repo = ServiceRepository(pg_session)
    services = await srv_repo.list_active(master.id)
    assert len(services) == 1
    assert services[0].title == "Классическое наращивание"
    assert services[0].price == Decimal("2500")

    # Now accept schedule
    cb_sch = SimpleNamespace(
        data=f"mgr:ob:sch:accept:{master.id}",
        from_user=SimpleNamespace(id=owner.telegram_id, first_name=owner.first_name, last_name=owner.last_name, username=owner.username),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    await cb_onboarding_accept_schedule(cb_sch, state, pg_session)

    sch_repo = ScheduleRepository(pg_session)
    templates = await sch_repo.get_weekly_templates(master.id)
    assert len(templates) == 7
    mon = next(t for t in templates if t.day_of_week == 0)
    sun = next(t for t in templates if t.day_of_week == 6)
    assert mon.is_day_off is False
    assert sun.is_day_off is True
    assert len(mon.breaks) == 1
    assert mon.breaks[0].break_start == dt_time(14, 0)


# ---------------------------------------------------------------------------
# 2. Services Management & Soft Deletion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_soft_delete_preserves_appointment_history(
    pg_session: AsyncSession, master_with_owner
):
    master, owner = master_with_owner
    srv_repo = ServiceRepository(pg_session)
    service = await srv_repo.create_service(
        master_id=master.id,
        title="Дизайн ногтей",
        price=1500,
        duration_min=60,
    )

    client_user = User(telegram_id=555444333, first_name="Анна")
    pg_session.add(client_user)
    await pg_session.flush()

    app = Appointment(
        master_id=master.id,
        user_id=client_user.id,
        service_id=service.id,
        snapshot_service_title=service.title,
        snapshot_service_price=service.price,
        snapshot_service_duration_min=service.duration_min,
        snapshot_buffer_duration_min=service.buffer_min,
        snapshot_deposit_amount=Decimal("0.00"),
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(minutes=60),
        end_time_with_buffer=datetime.now(timezone.utc) + timedelta(minutes=90),
        status=AppointmentStatus.COMPLETED,
    )
    pg_session.add(app)
    await pg_session.flush()

    # Soft delete via archive
    callback = SimpleNamespace(
        data=f"mgr:srv:delete:{master.id}:{service.id}",
        from_user=SimpleNamespace(id=owner.telegram_id, first_name=owner.first_name, last_name=owner.last_name, username=owner.username),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    await cb_manager_service_delete(callback, pg_session)

    # Service is archived and not active
    await pg_session.refresh(service)
    assert service.is_archived is True
    assert service.is_active is False

    # Appointment still exists and references the service!
    await pg_session.refresh(app)
    assert app.service_id == service.id
    assert app.status == AppointmentStatus.COMPLETED


# ---------------------------------------------------------------------------
# 3. Portfolio Management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_portfolio_items_and_tenant_isolation(
    pg_session: AsyncSession, master_with_owner, foreign_master
):
    master, owner = master_with_owner
    other_master, other_owner = foreign_master

    port_repo = PortfolioRepository(pg_session)
    cat1 = await port_repo.create_category(master.id, "Мои работы")
    cat2 = await port_repo.create_category(other_master.id, "Чужие работы")

    item1 = await port_repo.add_item(
        master_id=master.id,
        category_id=cat1.id,
        telegram_file_id="file_123",
        telegram_file_unique_id="uniq_123",
        caption="Французский маникюр",
        title="Френч",
    )
    assert item1 is not None
    assert item1.title == "Френч"

    # Cannot add item to other master's category
    bad_item = await port_repo.add_item(
        master_id=master.id,
        category_id=cat2.id,
        telegram_file_id="file_fake",
        telegram_file_unique_id="uniq_fake",
    )
    assert bad_item is None

    # Cannot delete other master's item
    del_res = await port_repo.delete_item(item1.id, master_id=other_master.id)
    assert del_res is False


# ---------------------------------------------------------------------------
# 4. Master Dashboard «Сегодня»
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_today_dashboard_aggregation(
    pg_session: AsyncSession, master_with_owner
):
    master, owner = master_with_owner
    now_utc = datetime.now(timezone.utc)

    srv = await ServiceRepository(pg_session).create_service(
        master_id=master.id, title="Маникюр", price=2000, duration_min=60
    )

    u1 = User(telegram_id=9001, first_name="Катя")
    u2 = User(telegram_id=9002, first_name="Лена")
    pg_session.add_all([u1, u2])
    await pg_session.flush()

    # Appointment 1: completed today
    app1 = Appointment(
        master_id=master.id,
        user_id=u1.id,
        service_id=srv.id,
        snapshot_service_title=srv.title,
        snapshot_service_price=Decimal("2000.00"),
        snapshot_service_duration_min=srv.duration_min,
        snapshot_buffer_duration_min=srv.buffer_min,
        snapshot_deposit_amount=Decimal("0.00"),
        start_time=now_utc - timedelta(hours=2),
        end_time=now_utc - timedelta(hours=1),
        end_time_with_buffer=now_utc - timedelta(hours=1) + timedelta(minutes=15),
        status=AppointmentStatus.COMPLETED,
    )
    # Appointment 2: confirmed today (upcoming)
    app2 = Appointment(
        master_id=master.id,
        user_id=u2.id,
        service_id=srv.id,
        snapshot_service_title=srv.title,
        snapshot_service_price=Decimal("2000.00"),
        snapshot_service_duration_min=srv.duration_min,
        snapshot_buffer_duration_min=srv.buffer_min,
        snapshot_deposit_amount=Decimal("0.00"),
        start_time=now_utc + timedelta(hours=1),
        end_time=now_utc + timedelta(hours=2),
        end_time_with_buffer=now_utc + timedelta(hours=2) + timedelta(minutes=15),
        status=AppointmentStatus.CONFIRMED,
    )
    pg_session.add_all([app1, app2])
    await pg_session.flush()

    crm_svc = MasterCrmService(pg_session)
    dashboard = await crm_svc.get_today_dashboard(master.id)

    assert dashboard["total_today"] == 2
    assert dashboard["revenue_today"] == Decimal("2000")
    assert dashboard["unique_clients_today"] == 2
    assert dashboard["next_appointment"] is not None
    assert dashboard["next_appointment"]["client_name"] == "Лена"
    assert dashboard["next_appointment"]["service_title"] == "Маникюр"


# ---------------------------------------------------------------------------
# 5. CRM CSV Export
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_clients_csv(
    pg_session: AsyncSession, master_with_owner, foreign_master
):
    master, owner = master_with_owner
    other_master, _ = foreign_master

    u1 = User(telegram_id=7001, first_name="Татьяна", phone="+79161112233", username="tatyana_client")
    u_other = User(telegram_id=7002, first_name="Светлана", phone="+79169998877")
    pg_session.add_all([u1, u_other])
    await pg_session.flush()

    mc1 = MasterClient(master_id=master.id, user_id=u1.id, notes="Любит кофе с молоком")
    mc_other = MasterClient(master_id=other_master.id, user_id=u_other.id, notes="Чужая клиентка")
    pg_session.add_all([mc1, mc_other])
    await pg_session.flush()

    crm_svc = MasterCrmService(pg_session)
    csv_str = await crm_svc.export_clients_csv(master.id)

    # Must contain BOM
    assert csv_str.startswith("\ufeff")
    # Must contain columns
    assert "Имя" in csv_str
    assert "Телефон" in csv_str
    assert "LTV (₽)" in csv_str
    # Must contain master's client
    assert "Татьяна" in csv_str
    assert "+79161112233" in csv_str
    assert "Любит кофе с молоком" in csv_str
    # Must NOT leak other master's client!
    assert "Светлана" not in csv_str
    assert "Чужая клиентка" not in csv_str


# ---------------------------------------------------------------------------
# 6. IDOR Protection in Manager Bot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idor_protection_rejects_foreign_user_actions(
    pg_session: AsyncSession, master_with_owner, foreign_master
):
    master, owner = master_with_owner
    other_master, other_owner = foreign_master

    # Attempt to export other master's CRM
    callback = SimpleNamespace(
        data=f"mgr:crm:export:{other_master.id}",
        from_user=SimpleNamespace(id=owner.telegram_id, first_name=owner.first_name, last_name=owner.last_name, username=owner.username),
        message=SimpleNamespace(answer_document=AsyncMock()),
        answer=AsyncMock(),
    )
    await cb_crm_export(callback, pg_session)
    callback.answer.assert_awaited_with("Ошибка: доступ запрещён.", show_alert=True)
    callback.message.answer_document.assert_not_awaited()

    # Attempt to view other master's services
    cb_srv = SimpleNamespace(
        data=f"mgr:services:{other_master.id}",
        from_user=SimpleNamespace(id=owner.telegram_id, first_name=owner.first_name, last_name=owner.last_name, username=owner.username),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    await cb_manager_services_list(cb_srv, AsyncMock(), pg_session)
    cb_srv.answer.assert_awaited_with("Ошибка: доступ запрещён.", show_alert=True)
    cb_srv.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# 7. Client Bot Reviews & Main Menu Keyboard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_bot_menu_and_reviews_display(
    pg_session: AsyncSession, master_with_owner
):
    master, owner = master_with_owner

    kb = get_main_menu_keyboard(is_admin=False, has_reviews=True)
    button_texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "📅 Записаться" in button_texts
    assert "⭐ Отзывы" in button_texts
    assert "📍 Контакты" in button_texts

    # View reviews when empty
    callback = SimpleNamespace(
        data="menu:reviews",
        message=SimpleNamespace(
            photo=None,
            edit_text=AsyncMock(),
            answer=AsyncMock(),
            delete=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    await cb_client_reviews(callback, pg_session, master_id=master.id)
    callback.message.edit_text.assert_awaited_once()
    assert "Пока отзывов нет" in callback.message.edit_text.await_args.kwargs["text"]
