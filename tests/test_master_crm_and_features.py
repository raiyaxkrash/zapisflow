"""Tests for Master CRM, segmentation, reviews, statistics, finances and repeat booking."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.broadcast import Broadcast
from app.database.models.master import Master, MasterAdmin, MasterAdminRole, MasterClient, MasterSettings, MasterStatus
from app.database.models.review import Review
from app.database.models.service import DepositType, Service
from app.database.models.user import User
from app.services.broadcast_service import BroadcastService
from app.services.crm_service import MasterCrmService
from app.services.exceptions import AccessDeniedError
from tests.conftest import requires_postgres


async def _create_user(session: AsyncSession, tg_id: int, name: str, phone: str = "+79990000000") -> User:
    u = User(
        telegram_id=tg_id,
        first_name=name,
        last_name="Test",
        username=f"user_{tg_id}",
        phone=phone,
    )
    session.add(u)
    await session.flush()
    return u


async def _create_master(session: AsyncSession, owner: User, name: str) -> Master:
    m = Master(
        owner_user_id=owner.id,
        display_name=name,
        status=MasterStatus.ACTIVE,
        timezone="Europe/Moscow",
    )
    session.add(m)
    await session.flush()
    ms = MasterSettings(master_id=m.id, vk_profile="master_vk")
    session.add(ms)
    await session.flush()
    return m


async def _create_service(session: AsyncSession, master_id: int, title: str, price: Decimal = Decimal("2000.00")) -> Service:
    s = Service(
        master_id=master_id,
        title=title,
        price=price,
        duration_min=60,
        buffer_min=15,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("500.00"),
        is_active=True,
    )
    session.add(s)
    await session.flush()
    return s


async def _create_appointment(
    session: AsyncSession,
    master_id: int,
    user_id: int,
    service: Service,
    start_time: datetime,
    status: AppointmentStatus = AppointmentStatus.COMPLETED,
) -> Appointment:
    app = Appointment(
        master_id=master_id,
        user_id=user_id,
        service_id=service.id,
        status=status,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=60),
        end_time_with_buffer=start_time + timedelta(minutes=75),
        snapshot_service_title=service.title,
        snapshot_service_price=service.price,
        snapshot_service_duration_min=60,
        snapshot_buffer_duration_min=15,
        snapshot_deposit_amount=service.deposit_value,
    )
    session.add(app)
    await session.flush()
    return app


@requires_postgres
@pytest.mark.asyncio
async def test_crm_client_card_and_ltv(pg_session: AsyncSession):
    """Test client card, appointment counts, LTV and favorite service calculation."""
    owner = await _create_user(pg_session, 1001, "Owner 1")
    client_u = await _create_user(pg_session, 2001, "Client Anna", phone="+79991112233")
    master = await _create_master(pg_session, owner, "Salon 1")

    # Link client
    mc = MasterClient(
        master_id=master.id,
        user_id=client_u.id,
        notes="Любит кофе с молоком",
        is_marketing_allowed=True,
    )
    pg_session.add(mc)
    await pg_session.flush()

    svc1 = await _create_service(pg_session, master.id, "Маникюр", Decimal("2000.00"))
    svc2 = await _create_service(pg_session, master.id, "Педикюр", Decimal("2500.00"))

    now = datetime.now(timezone.utc)
    # 2 completed Manicures, 1 completed Pedicure, 1 cancelled
    await _create_appointment(pg_session, master.id, client_u.id, svc1, now - timedelta(days=20), AppointmentStatus.COMPLETED)
    await _create_appointment(pg_session, master.id, client_u.id, svc1, now - timedelta(days=10), AppointmentStatus.COMPLETED)
    await _create_appointment(pg_session, master.id, client_u.id, svc2, now - timedelta(days=2), AppointmentStatus.COMPLETED)
    await _create_appointment(pg_session, master.id, client_u.id, svc1, now - timedelta(days=1), AppointmentStatus.CANCELLED_BY_CLIENT)

    crm = MasterCrmService(pg_session)
    card = await crm.get_client_card(master.id, client_u.id)

    assert card["first_name"] == "Client Anna"
    assert card["phone"] == "+79991112233"
    assert card["total_bookings"] == 4
    assert card["completed"] == 3
    assert card["cancelled"] == 1
    assert card["no_show"] == 0
    # LTV = 2000 + 2000 + 2500 = 6500
    assert card["total_spent"] == Decimal("6500.00")
    assert "Маникюр (2 раз)" in card["favorite_service"]
    assert card["notes"] == "Любит кофе с молоком"
    assert card["is_marketing_allowed"] is True


@requires_postgres
@pytest.mark.asyncio
async def test_crm_tenant_isolation_and_idor_protection(pg_session: AsyncSession):
    """Ensure Master A cannot access or modify Master B's client cards or notes."""
    owner_a = await _create_user(pg_session, 1002, "Owner A")
    owner_b = await _create_user(pg_session, 1003, "Owner B")
    client_b = await _create_user(pg_session, 2002, "Client B")

    master_a = await _create_master(pg_session, owner_a, "Master A")
    master_b = await _create_master(pg_session, owner_b, "Master B")

    # Client B belongs exclusively to Master B
    mc_b = MasterClient(master_id=master_b.id, user_id=client_b.id, notes="Master B private note")
    pg_session.add(mc_b)
    await pg_session.flush()

    crm = MasterCrmService(pg_session)

    # Master A cannot access client B's card
    with pytest.raises(LookupError):
        await crm.get_client_card(master_a.id, client_b.id)

    # Master A cannot update client B's notes
    with pytest.raises(AccessDeniedError):
        await crm.update_client_notes(master_a.id, client_b.id, actor_user_id=owner_b.id, notes="Hacked")

    # Master B cannot update notes using Master A's tenant id
    with pytest.raises(AccessDeniedError):
        await crm.update_client_notes(master_b.id, client_b.id, actor_user_id=owner_a.id, notes="Forged")

    # Owner B can update notes
    updated = await crm.update_client_notes(master_b.id, client_b.id, actor_user_id=owner_b.id, notes="Updated note")
    assert updated.notes == "Updated note"


@requires_postgres
@pytest.mark.asyncio
async def test_crm_segmentation_logic(pg_session: AsyncSession):
    """Test client segmentation: regular, new, inactive30, inactive60."""
    owner = await _create_user(pg_session, 1004, "Owner Seg")
    master = await _create_master(pg_session, owner, "Master Seg")
    svc = await _create_service(pg_session, master.id, "Услуга", Decimal("1000.00"))

    now = datetime.now(timezone.utc)

    # Client 1: Regular (3 completed visits)
    u_reg = await _create_user(pg_session, 2011, "Reg Client")
    pg_session.add(MasterClient(master_id=master.id, user_id=u_reg.id))
    for i in range(3):
        await _create_appointment(pg_session, master.id, u_reg.id, svc, now - timedelta(days=10 + i), AppointmentStatus.COMPLETED)

    # Client 2: New (first visit 5 days ago)
    u_new = await _create_user(pg_session, 2012, "New Client")
    pg_session.add(MasterClient(master_id=master.id, user_id=u_new.id))
    await _create_appointment(pg_session, master.id, u_new.id, svc, now - timedelta(days=5), AppointmentStatus.COMPLETED)

    # Client 3: Inactive 30 (last visit 40 days ago)
    u_inact30 = await _create_user(pg_session, 2013, "Inact30 Client")
    pg_session.add(MasterClient(master_id=master.id, user_id=u_inact30.id))
    await _create_appointment(pg_session, master.id, u_inact30.id, svc, now - timedelta(days=40), AppointmentStatus.COMPLETED)

    # Client 4: Inactive 60 (last visit 70 days ago)
    u_inact60 = await _create_user(pg_session, 2014, "Inact60 Client")
    pg_session.add(MasterClient(master_id=master.id, user_id=u_inact60.id))
    await _create_appointment(pg_session, master.id, u_inact60.id, svc, now - timedelta(days=70), AppointmentStatus.COMPLETED)

    crm = MasterCrmService(pg_session)

    # Check "all" segment
    all_clients, total_all = await crm.list_clients_by_segment(master.id, "all")
    assert total_all == 4

    # Check "regular" segment
    reg_clients, total_reg = await crm.list_clients_by_segment(master.id, "regular")
    assert total_reg >= 1
    reg_ids = [c["user_id"] for c in reg_clients]
    assert u_reg.id in reg_ids

    # Check "new" segment
    new_clients, total_new = await crm.list_clients_by_segment(master.id, "new")
    new_ids = [c["user_id"] for c in new_clients]
    assert u_new.id in new_ids

    # Check "inactive30" segment
    inact30_clients, total_inact30 = await crm.list_clients_by_segment(master.id, "inactive30")
    inact30_ids = [c["user_id"] for c in inact30_clients]
    assert u_inact30.id in inact30_ids
    assert u_inact60.id in inact30_ids  # 70 days is also > 30 days
    assert u_reg.id not in inact30_ids

    # Check "inactive60" segment
    inact60_clients, total_inact60 = await crm.list_clients_by_segment(master.id, "inactive60")
    inact60_ids = [c["user_id"] for c in inact60_clients]
    assert u_inact60.id in inact60_ids
    assert u_inact30.id not in inact60_ids


@requires_postgres
@pytest.mark.asyncio
async def test_crm_search_clients(pg_session: AsyncSession):
    """Test client search by partial name, phone, or username with tenant scoping."""
    owner = await _create_user(pg_session, 1005, "Owner Search")
    master = await _create_master(pg_session, owner, "Master Search")

    u1 = await _create_user(pg_session, 2021, "Екатерина", phone="+79261112233")
    u2 = await _create_user(pg_session, 2022, "Светлана", phone="+79269998877")
    pg_session.add(MasterClient(master_id=master.id, user_id=u1.id))
    pg_session.add(MasterClient(master_id=master.id, user_id=u2.id))
    await pg_session.flush()

    crm = MasterCrmService(pg_session)

    # Search by name
    res, count = await crm.search_clients(master.id, "Екатер")
    assert count == 1
    assert res[0]["user_id"] == u1.id

    # Search by phone digits
    res, count = await crm.search_clients(master.id, "9998877")
    assert count == 1
    assert res[0]["user_id"] == u2.id

    # Non-existent
    res, count = await crm.search_clients(master.id, "Неизвестный")
    assert count == 0


@requires_postgres
@pytest.mark.asyncio
async def test_reviews_creation_validation_and_aggregation(pg_session: AsyncSession):
    """Test review creation, 1..5 star validation, single review per appointment and average aggregation."""
    owner = await _create_user(pg_session, 1006, "Owner Rev")
    client = await _create_user(pg_session, 2031, "Client Rev")
    master = await _create_master(pg_session, owner, "Master Rev")
    svc = await _create_service(pg_session, master.id, "Брови", Decimal("1500.00"))

    now = datetime.now(timezone.utc)
    app1 = await _create_appointment(pg_session, master.id, client.id, svc, now - timedelta(days=2), AppointmentStatus.COMPLETED)
    app2 = await _create_appointment(pg_session, master.id, client.id, svc, now - timedelta(days=1), AppointmentStatus.COMPLETED)
    app_unconfirmed = await _create_appointment(pg_session, master.id, client.id, svc, now, AppointmentStatus.CONFIRMED)

    crm = MasterCrmService(pg_session)

    # 1. Invalid rating (0 or 6)
    with pytest.raises(ValueError, match="от 1 до 5"):
        await crm.create_review(master.id, client.id, app1.id, rating=0)
    with pytest.raises(ValueError, match="от 1 до 5"):
        await crm.create_review(master.id, client.id, app1.id, rating=6)

    # 2. Cannot review uncompleted appointment
    with pytest.raises(ValueError, match="после завершения визита"):
        await crm.create_review(master.id, client.id, app_unconfirmed.id, rating=5)

    # 3. Valid review 1: 5 stars with comment
    rev1 = await crm.create_review(
        master.id, client.id, app1.id, rating=5, comment="Прекрасный мастер, вернусь снова!"
    )
    assert rev1.id is not None
    assert rev1.rating == 5
    assert rev1.comment == "Прекрасный мастер, вернусь снова!"

    # 4. Duplicate review for app1 is forbidden
    with pytest.raises(ValueError, match="уже оставлен"):
        await crm.create_review(master.id, client.id, app1.id, rating=4)

    # 5. Valid review 2: 4 stars without comment
    rev2 = await crm.create_review(master.id, client.id, app2.id, rating=4, comment=None)
    assert rev2.rating == 4

    # 6. Aggregated summary
    summary = await crm.get_reviews_summary(master.id)
    assert summary["total_count"] == 2
    # Avg: (5 + 4) / 2 = 4.5
    assert summary["avg_rating"] == 4.5
    assert summary["stars_5"] == 1
    assert summary["stars_4"] == 1
    assert summary["stars_3"] == 0
    assert len(summary["recent_reviews"]) == 2


@requires_postgres
@pytest.mark.asyncio
async def test_master_statistics_and_finances(pg_session: AsyncSession):
    """Test operational stats and financial revenue calculations strictly per master."""
    owner = await _create_user(pg_session, 1007, "Owner Stats")
    client_a = await _create_user(pg_session, 2041, "Client A")
    client_b = await _create_user(pg_session, 2042, "Client B")
    master = await _create_master(pg_session, owner, "Master Stats")
    svc = await _create_service(pg_session, master.id, "Стрижка", Decimal("3000.00"))

    now = datetime.now(timezone.utc)

    # Appointments in period
    await _create_appointment(pg_session, master.id, client_a.id, svc, now - timedelta(hours=2), AppointmentStatus.COMPLETED)
    await _create_appointment(pg_session, master.id, client_b.id, svc, now - timedelta(hours=1), AppointmentStatus.COMPLETED)
    await _create_appointment(pg_session, master.id, client_a.id, svc, now + timedelta(hours=5), AppointmentStatus.CONFIRMED)
    await _create_appointment(pg_session, master.id, client_b.id, svc, now - timedelta(hours=3), AppointmentStatus.NO_SHOW)

    crm = MasterCrmService(pg_session)

    # Statistics
    stats = await crm.get_master_statistics(master.id, period="all")
    assert stats["total_bookings"] == 4
    assert stats["completed"] == 2
    assert stats["confirmed"] == 1
    assert stats["no_show"] == 1
    assert stats["unique_clients"] == 2

    # Finances
    fin = await crm.get_master_finances(master.id, period="all")
    # 2 completed * 3000 = 6000
    assert fin["revenue"] == Decimal("6000.00")
    assert fin["completed_count"] == 2
    assert fin["avg_ticket"] == Decimal("3000.00")
    assert len(fin["top_services"]) == 1
    assert fin["top_services"][0]["title"] == "Стрижка"


@requires_postgres
@pytest.mark.asyncio
async def test_broadcast_service_with_segments(pg_session: AsyncSession):
    """Test marketing broadcast targeting segments while respecting user consent."""
    owner = await _create_user(pg_session, 1008, "Owner Bcast")
    master = await _create_master(pg_session, owner, "Master Bcast")
    svc = await _create_service(pg_session, master.id, "Ламинирование", Decimal("1800.00"))

    now = datetime.now(timezone.utc)

    # User 1: Regular, marketing allowed
    u1 = await _create_user(pg_session, 2051, "Opted In Reg")
    mc1 = MasterClient(master_id=master.id, user_id=u1.id, is_marketing_allowed=True, is_bot_blocked=False)
    pg_session.add(mc1)
    for _ in range(3):
        await _create_appointment(pg_session, master.id, u1.id, svc, now - timedelta(days=15), AppointmentStatus.COMPLETED)

    # User 2: Regular, but marketing NOT allowed
    u2 = await _create_user(pg_session, 2052, "Opted Out Reg")
    mc2 = MasterClient(master_id=master.id, user_id=u2.id, is_marketing_allowed=False, is_bot_blocked=False)
    pg_session.add(mc2)
    for _ in range(3):
        await _create_appointment(pg_session, master.id, u2.id, svc, now - timedelta(days=15), AppointmentStatus.COMPLETED)

    # User 3: New client, marketing allowed
    u3 = await _create_user(pg_session, 2053, "Opted In New")
    mc3 = MasterClient(master_id=master.id, user_id=u3.id, is_marketing_allowed=True, is_bot_blocked=False)
    pg_session.add(mc3)
    await _create_appointment(pg_session, master.id, u3.id, svc, now - timedelta(days=2), AppointmentStatus.COMPLETED)

    bcast_svc = BroadcastService(pg_session)

    # Segment REGULAR: only u1 should be eligible (u2 opted out)
    reg_users = await bcast_svc.get_eligible_users(master.id, segment="REGULAR")
    reg_ids = [u.id for u in reg_users]
    assert u1.id in reg_ids
    assert u2.id not in reg_ids
    assert u3.id not in reg_ids

    # Segment ALL: u1 and u3 eligible, u2 excluded
    all_users = await bcast_svc.get_eligible_users(master.id, segment="ALL")
    all_ids = [u.id for u in all_users]
    assert u1.id in all_ids
    assert u3.id in all_ids
    assert u2.id not in all_ids


@requires_postgres
@pytest.mark.asyncio
async def test_manager_bot_crm_handlers(pg_session: AsyncSession):
    """Test Manager Bot CRM navigation, segment viewing, statistics and finances."""
    from unittest.mock import AsyncMock, MagicMock
    from app.manager_bot.handlers import (
        cb_client_card,
        cb_crm_main_menu,
        cb_crm_segment,
        cb_master_finances,
        cb_master_reviews,
        cb_master_statistics,
        cb_mgr_contacts,
    )

    owner = await _create_user(pg_session, 3001, "Mgr Owner")
    client = await _create_user(pg_session, 3002, "Mgr Client")
    master = await _create_master(pg_session, owner, "Mgr Salon")
    svc = await _create_service(pg_session, master.id, "Брови", Decimal("1500.00"))

    # Link client
    mc = MasterClient(master_id=master.id, user_id=client.id, notes="Тестовая заметка")
    pg_session.add(mc)
    await _create_appointment(
        pg_session, master.id, client.id, svc, datetime.now(timezone.utc) - timedelta(days=1), AppointmentStatus.COMPLETED
    )
    await pg_session.flush()

    # 1. CRM Main Menu
    cb = AsyncMock()
    cb.data = f"mgr:crm:{master.id}"
    cb.from_user = MagicMock(id=3001, first_name="Mgr Owner", username="owner_3001", last_name="Test")
    cb.message = AsyncMock()
    cb.answer = AsyncMock()

    await cb_crm_main_menu(cb, session=pg_session)
    cb.message.edit_text.assert_called_once()
    menu_text = cb.message.edit_text.call_args[0][0]
    assert "CRM база клиентов" in menu_text

    # 2. CRM Segment View
    cb.message.edit_text.reset_mock()
    cb.data = f"mgr:crm:seg:{master.id}:all:1"
    await cb_crm_segment(cb, session=pg_session)
    cb.message.edit_text.assert_called_once()
    seg_text = cb.message.edit_text.call_args[0][0]
    assert "Все клиенты" in seg_text

    # 3. Client Profile Card
    cb.message.edit_text.reset_mock()
    cb.data = f"mgr:client:{master.id}:{client.id}:all:1"
    await cb_client_card(cb, session=pg_session)
    cb.message.edit_text.assert_called_once()
    card_text = cb.message.edit_text.call_args[0][0]
    assert "Карточка клиента" in card_text
    assert "1500" in card_text

    # 4. Master Statistics
    cb.message.edit_text.reset_mock()
    cb.data = f"mgr:stats:{master.id}:all"
    await cb_master_statistics(cb, session=pg_session)
    cb.message.edit_text.assert_called_once()
    stats_text = cb.message.edit_text.call_args[0][0]
    assert "Статистика проекта" in stats_text
    assert "Выполнено визитов: <b>1</b>" in stats_text

    # 5. Master Finances
    cb.message.edit_text.reset_mock()
    cb.data = f"mgr:finances:{master.id}:all"
    await cb_master_finances(cb, session=pg_session)
    cb.message.edit_text.assert_called_once()
    fin_text = cb.message.edit_text.call_args[0][0]
    assert "Финансы мастера" in fin_text
    assert "1500" in fin_text

    # 6. Master Reviews
    cb.message.edit_text.reset_mock()
    cb.data = f"mgr:reviews:{master.id}"
    await cb_master_reviews(cb, session=pg_session)
    cb.message.edit_text.assert_called_once()
    rev_text = cb.message.edit_text.call_args[0][0]
    assert "Отзывы и рейтинг" in rev_text

    # 7. Master Contacts
    cb.message.edit_text.reset_mock()
    cb.data = f"mgr:contacts:{master.id}"
    state = AsyncMock()
    await cb_mgr_contacts(cb, state=state, session=pg_session)
    cb.message.edit_text.assert_called_once()
    contact_text = cb.message.edit_text.call_args[0][0]
    assert "Контакты студии" in contact_text


@requires_postgres
@pytest.mark.asyncio
async def test_client_bot_repeat_booking_and_review_flow(pg_session: AsyncSession):
    """Test client bot actions for repeat booking and interactive review submission."""
    from unittest.mock import AsyncMock, MagicMock
    from app.bot.handlers.client.my_appointments import (
        cb_repeat_booking,
        cb_review_skip_comment,
        cb_review_star_selected,
        cb_start_review,
        msg_review_save_comment,
    )
    from app.bot.keyboards.client import BookingActionCallback

    owner = await _create_user(pg_session, 4001, "Bot Master")
    client = await _create_user(pg_session, 4002, "Bot Client")
    master = await _create_master(pg_session, owner, "Bot Salon")
    svc = await _create_service(pg_session, master.id, "Комплекс", Decimal("3500.00"))

    now = datetime.now(timezone.utc)
    app = await _create_appointment(pg_session, master.id, client.id, svc, now - timedelta(days=1), AppointmentStatus.COMPLETED)

    # 1. Repeat Booking callback
    cb = AsyncMock()
    cb.from_user = MagicMock(id=4002)
    cb.message = AsyncMock()
    cb.answer = AsyncMock()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()

    repeat_cb_data = BookingActionCallback(action="repeat", appointment_id=app.id)
    await cb_repeat_booking(cb, repeat_cb_data, db_user=client, session=pg_session, master_id=master.id, state=state)

    cb.message.edit_text.assert_called_once()
    repeat_text = cb.message.edit_text.call_args.kwargs.get("text") or cb.message.edit_text.call_args[0][0]
    assert "Повторная запись" in repeat_text
    assert "Комплекс" in repeat_text

    # 2. Start Review callback
    cb.message.edit_text.reset_mock()
    review_cb_data = BookingActionCallback(action="review", appointment_id=app.id)
    await cb_start_review(cb, review_cb_data, db_user=client, session=pg_session, master_id=master.id)

    cb.message.edit_text.assert_called_once()
    rev_prompt = cb.message.edit_text.call_args.kwargs.get("text") or cb.message.edit_text.call_args[0][0]
    assert "Оценка визита" in rev_prompt

    # 3. Select 5 stars
    cb.message.edit_text.reset_mock()
    cb.data = f"rev:star:{app.id}:5"
    await cb_review_star_selected(cb, state=state, session=pg_session, master_id=master.id, db_user=client)
    cb.message.edit_text.assert_called_once()
    star_text = cb.message.edit_text.call_args.kwargs.get("text") or cb.message.edit_text.call_args[0][0]
    assert "⭐⭐⭐⭐⭐ (5/5)" in star_text

    # 4. Save review with comment message
    state.get_data = AsyncMock(return_value={"appointment_id": app.id, "rating": 5})
    msg = AsyncMock()
    msg.text = "Восхитительный сервис, очень довольна!"
    msg.answer = AsyncMock()

    await msg_review_save_comment(msg, state=state, session=pg_session, master_id=master.id, db_user=client)
    msg.answer.assert_called_once()
    confirm_text = msg.answer.call_args[0][0]
    assert "Спасибо за ваш отзыв" in confirm_text

    # Verify Review record persisted in database
    rev = await pg_session.scalar(select(Review).where(Review.appointment_id == app.id))
    assert rev is not None
    assert rev.rating == 5
    assert rev.comment == "Восхитительный сервис, очень довольна!"
    assert rev.master_id == master.id
    assert rev.user_id == client.id

