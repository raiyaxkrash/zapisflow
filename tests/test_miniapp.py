"""Real PostgreSQL API tests. No production tokens, chats or data are used."""

import asyncio
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.core.token_crypto import TokenCrypto
from app.database.models import (
    Appointment,
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterClient,
    MasterSettings,
    MasterStatus,
    Payment,
    PaymentProof,
    StaffMember,
    SubscriptionStatus,
    User,
)
from app.database.models.miniapp import MiniAppOperation, MiniAppSession
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_repository import ServiceRepository
from app.services.miniapp_auth import MiniAppError, validate_init_data
from app.web.app import create_app

TOKEN = "123456789:" + "A" * 35
TOKEN_B = "123456790:" + "B" * 35
ORIGIN = "https://app.example.test"


def signed(user_id=10001, now=None, token=TOKEN, **fields):
    values = {
        "auth_date": str(int(datetime.now(UTC).timestamp()) if now is None else now),
        "user": json.dumps(
            {"id": user_id, "first_name": "Client"}, separators=(",", ":")
        ),
        **fields,
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_init_data():
    assert validate_init_data(signed(now=1000), TOKEN, now=1001)["id"] == 10001


@pytest.mark.parametrize(
    "raw",
    [
        signed(now=1000).replace("10001", "10002"),
        signed(now=1000).replace("hash=", "hash=0"),
        signed(now=1),
        signed(now=5000),
        signed(now=1000) + "&auth_date=1000",
    ],
)
def test_invalid_init_data(raw):
    with pytest.raises(MiniAppError):
        validate_init_data(raw, TOKEN, now=1001)


def test_init_data_cannot_use_another_bot_token():
    with pytest.raises(MiniAppError):
        validate_init_data(signed(now=1000), "987654321:" + "B" * 35, now=1001)


class Redis:
    def __init__(self):
        self.count = {}

    async def incr(self, key):
        self.count[key] = self.count.get(key, 0) + 1
        return self.count[key]

    async def expire(self, key, ttl):
        return True


class FakeBot:
    def __init__(self):
        self.sends = 0

    async def send_document(self, *args, **kwargs):
        self.sends += 1
        return SimpleNamespace(
            document=SimpleNamespace(
                file_id="receipt-file", file_unique_id="receipt-unique"
            )
        )

    async def get_file(self, file_id):
        return SimpleNamespace(file_size=4, file_path="safe/test.jpg")

    async def download_file(self, path, destination):
        destination.write(b"jpeg")


class Registry:
    def __init__(self):
        self.bot = FakeBot()

    async def get_by_instance_id(self, *args, **kwargs):
        return self.bot


@pytest.fixture(scope="session")
def miniapp_database_url(postgres_url):
    """Create and migrate an isolated database, never truncate the caller's DB."""
    name = "zapisflow_miniapp_test_" + uuid.uuid4().hex
    admin_url = make_url(postgres_url).set(database="postgres")
    db_url = admin_url.set(database=name)

    async def manage(command):
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'{command} DATABASE "{name}"'))
        finally:
            await engine.dispose()

    asyncio.run(manage("CREATE"))
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": db_url.render_as_string(hide_password=False),
    }
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            check=True,
            capture_output=True,
        )
        yield db_url
    finally:
        asyncio.run(manage("DROP"))


@pytest_asyncio.fixture
async def system(miniapp_database_url, monkeypatch):
    db_url = miniapp_database_url
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "mini_app_base_url", ORIGIN)
    monkeypatch.setattr(settings, "bot_token_encryption_key", "01" * 32)
    async with factory() as session:
        owners = [
            User(telegram_id=11001 + i, first_name=f"Owner {i}") for i in range(2)
        ]
        session.add_all(owners)
        await session.flush()
        masters = [
            Master(
                owner_user_id=u.id,
                display_name=f"Studio {i}",
                status=MasterStatus.ACTIVE,
                subscription_status=SubscriptionStatus.TRIAL,
                trial_ends_at=datetime.now(UTC) + timedelta(days=14),
                timezone="UTC",
            )
            for i, u in enumerate(owners)
        ]
        session.add_all(masters)
        await session.flush()
        bots = []
        staffs = []
        offerings = []
        for i, m in enumerate(masters):
            session.add(
                MasterSettings(
                    master_id=m.id,
                    bank_name="Bank",
                    bank_card_number="Test requisites",
                    bank_recipient_name="Master",
                    booking_horizon_days=14,
                )
            )
            bot = BotInstance(
                master_id=m.id,
                telegram_bot_id=123456789 + i,
                encrypted_token=TokenCrypto().encrypt(
                    TOKEN if i == 0 else TOKEN_B, associated_data=123456789 + i
                ),
                status=BotInstanceStatus.ACTIVE,
                is_current=True,
            )
            staff = StaffMember(
                master_id=m.id, display_name="Specialist", is_active=True
            )
            session.add_all([bot, staff])
            await session.flush()
            service = await ServiceRepository(session).create_service(
                m.id,
                title="Haircut",
                price=499,
                duration_min=60,
                buffer_min=15,
                deposit_value=0,
            )
            for day in range(7):
                await ScheduleRepository(session).set_template(
                    day, False, time(10), time(19), master_id=m.id, staff_id=staff.id
                )
            bots.append(bot)
            staffs.append(staff)
            offerings.append(service)
        staff_user = User(telegram_id=12001, first_name="Staff")
        session.add(staff_user)
        await session.flush()
        staffs[0].user_id = staff_user.id
        await session.commit()
    registry = Registry()
    app = create_app(session_factory=factory, redis_client=Redis(), registry=registry)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    value = SimpleNamespace(
        client=client,
        app=app,
        factory=factory,
        owners=owners,
        masters=masters,
        bots=bots,
        staffs=staffs,
        services=offerings,
        registry=registry,
    )
    yield value
    await client.aclose()
    # Only our random disposable database is cleared. Main suite DB untouched.
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE masters, users CASCADE"))
    await engine.dispose()


async def login(system, user=10001, bot_index=0, client=None, raw=None):
    client = client or system.client
    result = await client.post(
        "/api/miniapp/auth",
        headers={"Origin": ORIGIN},
        json={
            "bot_public_id": str(system.bots[bot_index].public_id),
            "init_data": raw
            or signed(user, token=TOKEN if bot_index == 0 else TOKEN_B),
        },
    )
    assert result.status_code == 200, result.text
    client.headers.update(
        {
            "Origin": ORIGIN,
            "X-CSRF-Token": result.json()["csrf_token"],
            "X-MiniApp-Bot": str(system.bots[bot_index].public_id),
        }
    )
    return client


async def post(client, path, data, key=None, method="POST"):
    return await client.request(
        method,
        "/api/miniapp" + path,
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
        json=data,
    )


async def first_slot(s):
    target = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    result = await s.client.get(
        "/api/miniapp/client/slots",
        params={"service_id": s.services[0].id, "target_date": target},
    )
    assert result.status_code == 200, result.text
    return result.json()["slots"][0]


async def make_hold(s, key=None):
    result = await post(
        s.client,
        "/client/holds",
        {
            "service_id": s.services[0].id,
            "staff_id": s.staffs[0].id,
            "start_time": await first_slot(s),
        },
        key,
    )
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.asyncio
async def test_client_auth_context_and_services(system):
    c = await login(system)
    context = (await c.get("/api/miniapp/context")).json()
    assert context["project"]["name"] == "Studio 0"
    assert context["capabilities"]["can_manage"] is False
    data = (await c.get("/api/miniapp/client/services")).json()
    assert data[0]["price"] == "499.00"
    assert len(data) == 1
    assert (await c.get("/api/miniapp/master/settings")).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("user,role", [(11001, "owner"), (12001, "staff")])
async def test_roles(system, user, role):
    c = await login(system, user=user)
    capabilities = (await c.get("/api/miniapp/context")).json()["capabilities"]
    assert capabilities["role"] == role
    assert capabilities["can_manage"] is True
    assert (await c.get("/api/miniapp/master/settings")).status_code == (
        200 if role == "owner" else 403
    )


@pytest.mark.asyncio
async def test_auth_unknown_disabled_invalid_and_replay(system):
    for public_id, raw in [
        (str(uuid.uuid4()), signed()),
        (str(system.bots[0].public_id), signed().replace("hash=", "hash=f")),
    ]:
        r = await system.client.post(
            "/api/miniapp/auth",
            headers={"Origin": ORIGIN},
            json={"bot_public_id": public_id, "init_data": raw},
        )
        assert r.status_code == 401
    raw = signed()
    await login(system, raw=raw)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=system.app), base_url=ORIGIN
    ) as stranger:
        r = await stranger.post(
            "/api/miniapp/auth",
            headers={"Origin": ORIGIN},
            json={"bot_public_id": str(system.bots[0].public_id), "init_data": raw},
        )
        assert r.status_code == 401 and r.json()["code"] == "AUTH_REPLAY"
    async with system.factory() as session:
        row = await session.get(BotInstance, system.bots[0].id)
        row.status = BotInstanceStatus.DISABLED
        await session.commit()
    assert (await system.client.get("/api/miniapp/context")).status_code == 401


@pytest.mark.asyncio
async def test_expired_session_token_rotation_csrf_and_origin(system):
    c = await login(system, 11001)
    r = await post(c, "/master/settings", {"booking_horizon_days": 14}, method="PATCH")
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = "forged"
    assert (
        await post(c, "/master/settings", {"booking_horizon_days": 15}, method="PATCH")
    ).status_code == 403
    c.headers["Origin"] = "https://evil.test"
    assert (
        await post(c, "/master/settings", {"booking_horizon_days": 15}, method="PATCH")
    ).status_code == 403
    async with system.factory() as session:
        row = await session.get(BotInstance, system.bots[0].id)
        row.token_version += 1
        await session.commit()
    assert (await c.get("/api/miniapp/context")).status_code == 401


@pytest.mark.asyncio
async def test_hold_confirm_duplicate_and_my_appointments(system):
    c = await login(system)
    hold = await make_hold(system)
    assert hold["status"] == "WAITING_PAYMENT" and hold["hold_until"]
    body = {
        "appointment_id": hold["id"],
        "phone": "+79991234567",
        "policy_agreed": True,
    }
    key = str(uuid.uuid4())
    r = await post(c, "/client/appointments", body, key)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED" and r.json()["payment"] == []
    assert (await post(c, "/client/appointments", body, key)).json() == r.json()
    assert len((await c.get("/api/miniapp/client/appointments")).json()) == 1
    async with system.factory() as session:
        a = await session.get(Appointment, hold["id"])
        assert a.cancel_policy_agreed
        assert (
            await session.scalar(
                select(func.count(Payment.id)).where(Payment.appointment_id == a.id)
            )
            == 0
        )
    cancel = await post(c, f"/client/appointments/{hold['id']}/cancel", {})
    assert cancel.status_code == 200, cancel.text
    assert (await post(c, f"/client/appointments/{hold['id']}/cancel", {})).json()[
        "status"
    ] == "CANCELLED_BY_CLIENT"


@pytest.mark.asyncio
async def test_concurrent_duplicate_and_collision(system):
    c = await login(system)
    body = {"service_id": system.services[0].id, "start_time": await first_slot(system)}
    key = str(uuid.uuid4())
    responses = await asyncio.gather(
        post(c, "/client/holds", body, key), post(c, "/client/holds", body, key)
    )
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    r = await post(c, "/client/holds", body)
    assert r.status_code == 409
    async with system.factory() as session:
        assert (
            await session.scalar(
                select(func.count(Appointment.id)).where(
                    Appointment.master_id == system.masters[0].id
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_cross_tenant_entities_and_client_ownership(system):
    c = await login(system)
    assert (
        await c.get(
            "/api/miniapp/client/staff", params={"service_id": system.services[1].id}
        )
    ).status_code == 404
    assert (
        await c.get(
            "/api/miniapp/client/services", params={"staff_id": system.staffs[1].id}
        )
    ).status_code == 404
    hold = await make_hold(system)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=system.app), base_url=ORIGIN
    ) as other:
        await login(system, 10002, client=other)
        assert (
            await post(
                other,
                "/client/appointments",
                {
                    "appointment_id": hold["id"],
                    "phone": "+79991234567",
                    "policy_agreed": True,
                },
            )
        ).status_code == 404
        assert (
            await post(other, f"/client/appointments/{hold['id']}/cancel", {})
        ).status_code == 404
        assert (
            await other.get(f"/api/miniapp/client/appointments/{hold['id']}/payment")
        ).status_code == 404
    await login(system, 11001)
    assert (
        await post(
            c,
            f"/master/services/{system.services[1].id}",
            {"title": "hack", "price": 1, "duration_min": 30, "buffer_min": 0},
            method="PUT",
        )
    ).status_code == 404
    assert (
        await post(
            c,
            "/master/schedule",
            {"staff_id": system.staffs[1].id, "weekday": 1},
            method="PUT",
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_prepaid_proof_and_owner_approval(system):
    async with system.factory() as session:
        await ServiceRepository(session).update_service(
            system.services[0].id, system.masters[0].id, deposit_value=50
        )
        await session.commit()
    c = await login(system)
    hold = await make_hold(system)
    r = await post(
        c,
        "/client/appointments",
        {"appointment_id": hold["id"], "phone": "+79991234567", "policy_agreed": True},
    )
    assert r.status_code == 200, r.text
    assert (
        r.json()["status"] == "WAITING_PAYMENT"
        and r.json()["payment"][0]["amount"] == "50.00"
    )
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (10, 10)).save(image, format="PNG")
    key = str(uuid.uuid4())
    for _ in range(2):
        r = await c.post(
            f"/api/miniapp/client/appointments/{hold['id']}/proof",
            headers={"Idempotency-Key": key},
            files={"file": ("unsafe.exe", image.getvalue(), "image/png")},
        )
        assert r.status_code == 200, r.text
    assert system.registry.bot.sends == 1
    async with system.factory() as session:
        assert await session.scalar(select(func.count(PaymentProof.id))) == 1
    proof_id = r.json()["proof_id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=system.app), base_url=ORIGIN
    ) as stranger:
        await login(system, 10002, client=stranger)
        assert (
            await stranger.get(f"/api/miniapp/proofs/{proof_id}")
        ).status_code == 404
        assert (
            await stranger.post(
                f"/api/miniapp/client/appointments/{hold['id']}/proof",
                headers={"Idempotency-Key": str(uuid.uuid4())},
                files={"file": ("receipt.png", image.getvalue(), "image/png")},
            )
        ).status_code == 404
        await login(system, 11002, bot_index=1, client=stranger)
        assert (
            await stranger.get(f"/api/miniapp/proofs/{proof_id}")
        ).status_code == 404
    await login(system, 11001)
    assert (await c.get(f"/api/miniapp/proofs/{proof_id}")).content == b"jpeg"
    r = await post(
        c, f"/master/payments/{hold['payment'][0]['id']}/decision", {"approve": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED"
    assert (
        await post(
            c,
            f"/master/payments/{hold['payment'][0]['id']}/decision",
            {"approve": True},
        )
    ).json()["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_expired_subscription_and_missing_requisites(system):
    c = await login(system)
    async with system.factory() as session:
        m = await session.get(Master, system.masters[0].id)
        m.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
    assert (
        await c.get(
            "/api/miniapp/client/slots",
            params={
                "service_id": system.services[0].id,
                "target_date": (date_tomorrow()).isoformat(),
            },
        )
    ).status_code == 403


def date_tomorrow():
    return (datetime.now(UTC) + timedelta(days=1)).date()


@pytest.mark.asyncio
async def test_settings_schedule_horizon_and_crm(system):
    c = await login(system, 11001)
    r = await post(
        c,
        "/master/settings",
        {"studio_address": "<b>Address</b>", "booking_horizon_days": 14},
        method="PATCH",
    )
    assert r.status_code == 200, r.text
    assert r.json()["studio_address"] == "<b>Address</b>"
    day = date_tomorrow().isoformat()
    r = await post(
        c,
        "/master/schedule",
        {"staff_id": system.staffs[0].id, "target_date": day, "is_day_off": True},
        method="PUT",
    )
    assert r.status_code == 200, r.text
    assert r.json()["dates"][0]["is_day_off"]
    r = await c.get(
        "/api/miniapp/client/slots",
        params={"service_id": system.services[0].id, "target_date": day},
    )
    assert r.json()["slots"] == []
    r = await post(
        c,
        "/master/schedule",
        {
            "staff_id": system.staffs[0].id,
            "target_date": day,
            "work_start": "11:00",
            "work_end": "15:00",
        },
        method="PUT",
    )
    assert r.status_code == 200
    r = await c.get(
        "/api/miniapp/client/slots",
        params={"service_id": system.services[0].id, "target_date": day},
    )
    assert r.json()["slots"][0][11:16] == "11:00"
    r = await c.get(
        "/api/miniapp/client/slots",
        params={
            "service_id": system.services[0].id,
            "target_date": (date_tomorrow() + timedelta(days=15)).isoformat(),
        },
    )
    assert r.json()["slots"] == []
    assert (await c.get("/api/miniapp/master/clients/9999999")).status_code == 404
    assert (
        await c.get("/api/miniapp/master/appointments", params={"target_date": day})
    ).status_code == 200


@pytest.mark.asyncio
async def test_authoritative_fields_rejected(system):
    c = await login(system)
    r = await post(
        c,
        "/client/holds",
        {
            "service_id": system.services[0].id,
            "start_time": await first_slot(system),
            "price": 1,
            "master_id": system.masters[1].id,
        },
    )
    assert r.status_code == 422
    assert "price" not in r.text and "input" not in r.json()


def test_safe_image_rejects_executable_and_strips_metadata():
    from PIL import Image

    from app.web.miniapp import safe_image

    with pytest.raises(MiniAppError):
        safe_image(b"MZexecutable", "image/png")
    image = io.BytesIO()
    Image.new("RGB", (10, 10)).save(image, format="PNG")
    normalized = safe_image(image.getvalue() + b"<script>evil</script>", "image/png")
    assert b"<script>" not in normalized


@pytest.mark.asyncio
async def test_auth_rate_limit_fail_closed(system):
    system.app.state.redis_client = None
    r = await system.client.post(
        "/api/miniapp/auth",
        headers={"Origin": ORIGIN},
        json={"bot_public_id": str(system.bots[0].public_id), "init_data": signed()},
    )
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_signed_query_reordering_cannot_bypass_replay_guard(system):
    raw = signed()
    await login(system, raw=raw)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=system.app), base_url=ORIGIN
    ) as stranger:
        response = await stranger.post(
            "/api/miniapp/auth",
            headers={"Origin": ORIGIN},
            json={
                "bot_public_id": str(system.bots[0].public_id),
                "init_data": "&".join(reversed(raw.split("&"))),
            },
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_expiration_and_token_rotation(system):
    c = await login(system)
    async with system.factory() as session:
        record = await session.scalar(select(MiniAppSession))
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (await c.get("/api/miniapp/context")).status_code == 401
    await login(system, raw=signed(query_id="fresh"))
    async with system.factory() as session:
        bot = await session.get(BotInstance, system.bots[0].id)
        bot.token_version += 1
        await session.commit()
    assert (await c.get("/api/miniapp/context")).status_code == 401


@pytest.mark.asyncio
async def test_missing_requisites_does_not_create_hold_or_payment(system):
    c = await login(system)
    async with system.factory() as session:
        await ServiceRepository(session).update_service(
            system.services[0].id, system.masters[0].id, deposit_value=50
        )
        config = await session.scalar(
            select(MasterSettings).where(
                MasterSettings.master_id == system.masters[0].id
            )
        )
        config.bank_card_number = None
        await session.commit()
    response = await post(
        c,
        "/client/holds",
        {"service_id": system.services[0].id, "start_time": await first_slot(system)},
    )
    assert response.status_code >= 400
    async with system.factory() as session:
        assert await session.scalar(select(func.count(Appointment.id))) == 0
        assert await session.scalar(select(func.count(Payment.id))) == 0
        assert await session.scalar(select(func.count(MiniAppOperation.id))) == 0


@pytest.mark.asyncio
async def test_hold_expiration_blocks_confirmation(system):
    c = await login(system)
    hold = await make_hold(system)
    async with system.factory() as session:
        row = await session.get(Appointment, hold["id"])
        row.hold_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    response = await post(
        c,
        "/client/appointments",
        {"appointment_id": hold["id"], "phone": "+79991234567", "policy_agreed": True},
    )
    assert response.status_code >= 400
    async with system.factory() as session:
        assert (await session.get(Appointment, hold["id"])).status.value != "CONFIRMED"


@pytest.mark.asyncio
async def test_two_bot_contexts_and_crm_idor(system):
    await login(system)
    hold = await make_hold(system)
    async with system.factory() as session:
        foreign = await session.scalar(
            select(MasterClient).where(MasterClient.master_id == system.masters[0].id)
        )
        foreign_id = foreign.id
    await login(system, 11002, bot_index=1)
    context = (await system.client.get("/api/miniapp/context")).json()
    assert context["project"]["name"] == "Studio 1"
    assert [
        s["id"]
        for s in (await system.client.get("/api/miniapp/client/services")).json()
    ] == [system.services[1].id]
    assert (
        await system.client.get(f"/api/miniapp/master/appointments/{hold['id']}")
    ).status_code == 404
    assert (
        await system.client.get(f"/api/miniapp/master/clients/{foreign_id}")
    ).status_code == 404
    assert (
        await post(
            system.client,
            f"/master/clients/{foreign_id}",
            {"notes": "foreign"},
            method="PATCH",
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_staff_cannot_mutate_admin_resources(system):
    c = await login(system, 12001)
    assert (await c.get("/api/miniapp/master/clients")).status_code == 403
    assert (await c.get("/api/miniapp/master/settings")).status_code == 403
    assert (
        await post(c, "/master/settings", {"booking_horizon_days": 30}, method="PATCH")
    ).status_code == 403
    assert (
        await post(c, "/master/payments/1/decision", {"approve": True})
    ).status_code == 403


@pytest.mark.asyncio
async def test_owner_manual_booking_idempotency_and_collision(system):
    await login(system)
    await make_hold(system)
    # The existing client relation is reused, never replaced by a global ID.
    async with system.factory() as session:
        relation = await session.scalar(
            select(MasterClient).where(MasterClient.master_id == system.masters[0].id)
        )
        client_id = relation.id
    c = await login(system, 11001)
    start = (
        await c.get(
            "/api/miniapp/client/slots",
            params={
                "service_id": system.services[0].id,
                "target_date": date_tomorrow().isoformat(),
            },
        )
    ).json()["slots"][-1]
    body = {
        "master_client_id": client_id,
        "service_id": system.services[0].id,
        "staff_id": system.staffs[0].id,
        "start_time": start,
    }
    key = str(uuid.uuid4())
    responses = await asyncio.gather(
        post(c, "/master/appointments", body, key),
        post(c, "/master/appointments", body, key),
    )
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert (await post(c, "/master/appointments", body)).status_code == 409


@pytest.mark.asyncio
async def test_request_logs_never_contain_init_data_or_bot_token(system, caplog):
    import logging

    caplog.set_level(logging.INFO)
    raw = signed()
    await login(system, raw=raw)
    assert TOKEN not in caplog.text and raw not in caplog.text


@pytest.mark.asyncio
async def test_failure_before_commit_rolls_back_and_same_key_retries(
    system, monkeypatch, caplog
):
    import app.web.miniapp as module

    c = await login(system)
    hold = await make_hold(system)
    body = {
        "appointment_id": hold["id"],
        "phone": "+79991234567",
        "policy_agreed": True,
    }
    key = str(uuid.uuid4())
    original = module.notify

    async def fail_after_change(*args, **kwargs):
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(module, "notify", fail_after_change)
    assert (await post(c, "/client/appointments", body, key)).status_code == 503
    assert TOKEN not in caplog.text
    async with system.factory() as session:
        a = await session.get(Appointment, hold["id"])
        assert a.status.value == "WAITING_PAYMENT" and not a.cancel_policy_agreed
        assert (
            await session.scalar(
                select(func.count(MiniAppOperation.id)).where(
                    MiniAppOperation.key == key
                )
            )
            == 0
        )
    monkeypatch.setattr(module, "notify", original)
    result = await post(c, "/client/appointments", body, key)
    assert result.status_code == 200 and result.json()["status"] == "CONFIRMED"
    assert (await post(c, "/client/appointments", body, key)).json() == result.json()


@pytest.mark.asyncio
async def test_cookie_from_second_bot_cannot_be_used_on_first_bot_page(system):
    c = await login(system, 11002, bot_index=1)
    c.headers["X-MiniApp-Bot"] = str(system.bots[0].public_id)
    assert (await c.get("/api/miniapp/context")).status_code == 401


@pytest.mark.asyncio
async def test_two_clients_race_for_same_slot(system):
    c = await login(system)
    start = await first_slot(system)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=system.app), base_url=ORIGIN
    ) as other:
        await login(system, 10002, client=other)
        body = {
            "service_id": system.services[0].id,
            "staff_id": system.staffs[0].id,
            "start_time": start,
        }
        results = await asyncio.gather(
            post(c, "/client/holds", body), post(other, "/client/holds", body)
        )
        assert sorted(r.status_code for r in results) == [200, 409]
    async with system.factory() as session:
        assert await session.scalar(select(func.count(Appointment.id))) == 1


def test_miniapp_url_configuration_and_menu_binding(monkeypatch):
    from pydantic import ValidationError

    from app.bot.handlers.client.start import miniapp_url
    from app.bot.keyboards.client import MenuCallback, get_main_menu_keyboard
    from app.config.settings import Settings

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, MINI_APP_BASE_URL="https://user:password@example.test/path"
        )
    monkeypatch.setattr(settings, "mini_app_base_url", ORIGIN)
    bot = SimpleNamespace(public_id=uuid.uuid4())
    url = miniapp_url(bot)
    button = get_main_menu_keyboard(miniapp_url=url).inline_keyboard[0][0]
    assert (
        button.web_app.url == f"{ORIGIN}/b/{bot.public_id}"
        and button.callback_data is None
    )
    assert (
        MenuCallback.unpack(
            get_main_menu_keyboard().inline_keyboard[0][0].callback_data
        ).action
        == "book"
    )
