"""Website adapters exercised against the existing migrated PostgreSQL fixture."""

import asyncio
import io
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
import test_miniapp as miniapp_tests
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config.settings import settings
from app.database.models import Appointment, BotInstance, User
from app.services.web_oidc import TelegramOIDC, digest
from app.web.web_booking import COOKIE

system = miniapp_tests.system
miniapp_database_url = miniapp_tests.miniapp_database_url

ORIGIN = "https://app.example.test"


@pytest_asyncio.fixture
async def website(system, monkeypatch):
    monkeypatch.setattr(settings, "web_booking_base_url", ORIGIN)
    redis = fakeredis.aioredis.FakeRedis()
    system.app.state.redis_client = redis
    async with system.factory() as session:
        bot = await session.get(BotInstance, system.bots[0].id)
        bot.web_booking_enabled = True
        bot.mini_app_enabled = False
        await session.commit()
    yield system
    await redis.aclose()


async def authenticated(system, client, telegram_id=456789):
    async with system.factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(telegram_id=telegram_id, first_name="Website Client")
            session.add(user)
            await session.flush()
        user_id = user.id
        await session.commit()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await system.app.state.redis_client.set(
        f"web:session:{digest(token)}",
        json.dumps(
            {
                "user_id": user_id,
                "csrf": csrf,
                "csrf_hash": digest(csrf),
                "intent": {},
            }
        ),
        ex=300,
    )
    client.cookies.set(COOKIE, token, domain="app.example.test", path="/api")
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": csrf})
    return user_id


def base(system, index=0):
    return f"/api/web-booking/{system.bots[index].public_id}"


async def selection(system):
    service, staff = system.services[0].id, system.staffs[0].id
    target = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    result = await system.client.get(
        base(system) + "/slots",
        params={"service_id": service, "staff_id": staff, "target_date": target},
    )
    assert result.status_code == 200, result.text
    return {
        "service_id": service,
        "staff_id": staff,
        "start_time": result.json()["slots"][0],
    }


async def post(client, path, body, key=None):
    return await client.post(
        path, json=body, headers={"Idempotency-Key": key or str(uuid.uuid4())}
    )


@pytest.mark.asyncio
async def test_anonymous_catalog_independent_of_miniapp_and_tenant_scope(website):
    s = website
    for path in ("/context", "/services"):
        assert (await s.client.get(base(s) + path)).status_code == 200
    assert (await s.client.get(base(s, 1) + "/services")).status_code == 404
    result = await s.client.get(
        base(s) + "/staff", params={"service_id": s.services[1].id}
    )
    assert result.status_code == 404
    selected = await selection(s)
    month = datetime.now(UTC)
    calendar = await s.client.get(
        base(s) + "/availability/calendar",
        params={
            "service_id": selected["service_id"],
            "staff_id": selected["staff_id"],
            "year": month.year,
            "month": month.month,
        },
    )
    assert calendar.status_code == 200
    assert all(
        not day["available"]
        for day in calendar.json()["days"]
        if day["date"] > calendar.json()["max_date"]
    )
    assert (await post(s.client, base(s) + "/holds", selected)).status_code == 401


@pytest.mark.asyncio
async def test_shared_booking_retry_account_csrf_cancel_logout(website):
    s = website
    await authenticated(s, s.client)
    selected = await selection(s)
    key = str(uuid.uuid4())
    first = await post(s.client, base(s) + "/holds", selected, key)
    assert first.status_code == 200, first.text
    duplicate = await post(s.client, base(s) + "/holds", selected, key)
    assert duplicate.json()["id"] == first.json()["id"]
    confirmation = await post(
        s.client,
        base(s) + "/confirm",
        {
            "appointment_id": first.json()["id"],
            "phone": "+79991234567",
            "policy_agreed": True,
        },
    )
    assert confirmation.status_code == 200, confirmation.text
    assert confirmation.json()["status"] == "CONFIRMED"
    account = await s.client.get("/api/web-booking/account/bookings")
    assert account.json()[0]["id"] == first.json()["id"]
    async with s.factory() as session:
        assert await session.scalar(select(func.count(Appointment.id))) == 1
        from app.database.models import Master

        master = await session.get(Master, s.masters[0].id)
        master.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
    # Expiration blocks new bookings, never a client's existing cancellation.
    assert (await post(s.client, base(s) + "/holds", selected)).status_code == 403
    invalid = await s.client.post(
        base(s) + f"/appointments/{first.json()['id']}/cancel",
        headers={"X-CSRF-Token": "bad", "Idempotency-Key": str(uuid.uuid4())},
        json={},
    )
    assert invalid.status_code == 403
    cancelled = await post(
        s.client, base(s) + f"/appointments/{first.json()['id']}/cancel", {}
    )
    assert cancelled.json()["status"] == "CANCELLED_BY_CLIENT"
    assert (await s.client.post("/api/auth/logout")).status_code == 200
    assert (await s.client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_two_website_users_one_slot_database_constraint(website):
    s = website
    await authenticated(s, s.client, 456789)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=s.app), base_url=ORIGIN
    ) as other:
        await authenticated(s, other, 456790)
        selected = await selection(s)
        results = await asyncio.gather(
            post(s.client, base(s) + "/holds", selected),
            post(other, base(s) + "/holds", selected),
        )
        assert sorted(r.status_code for r in results) == [200, 409]
        winner = next(r.json()["id"] for r in results if r.status_code == 200)
        loser_client = other if results[0].status_code == 200 else s.client
        assert (
            await loser_client.get(base(s) + f"/appointments/{winner}/payment")
        ).status_code == 404


@pytest.mark.asyncio
async def test_login_existing_user_session_rotation_and_intent(website, monkeypatch):
    s = website
    await authenticated(s, s.client, s.owners[0].telegram_id)
    old = s.client.cookies.get(COOKIE)
    monkeypatch.setattr(settings, "telegram_login_client_id", "123")
    monkeypatch.setattr(
        settings, "telegram_login_client_secret", SecretStr("test-only")
    )
    monkeypatch.setattr(
        settings, "telegram_login_redirect_uri", ORIGIN + "/api/auth/telegram/callback"
    )
    intent = {
        "bot_public_id": str(s.bots[0].public_id),
        "service_id": s.services[0].id,
        "staff_id": s.staffs[0].id,
        "start_time": "2026-10-10T10:00:00+00:00",
    }
    # Test-only verified identity boundary, cryptographic rejection tested separately.
    monkeypatch.setattr(
        TelegramOIDC,
        "complete",
        AsyncMock(
            return_value=(
                {"id": s.owners[0].telegram_id, "given_name": "Owner"},
                intent,
            )
        ),
    )
    response = await s.client.get("/api/auth/telegram/callback?state=test&code=test")
    assert response.status_code == 303, response.text
    assert response.headers["location"] == ORIGIN + "/book/" + str(s.bots[0].public_id)
    assert await s.app.state.redis_client.get(f"web:session:{digest(old)}") is None
    assert s.client.cookies.get(COOKIE) != old
    assert (await s.client.get("/api/auth/me")).json()["intent"] == intent
    async with s.factory() as session:
        assert (
            await session.scalar(
                select(func.count(User.id)).where(
                    User.telegram_id == s.owners[0].telegram_id
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_feature_flag_owner_only_and_disabled_subscription(website):
    from app.database.models import Master
    from app.services.bot_provisioning_service import BotProvisioningService
    from app.services.exceptions import AccessDeniedError

    s = website
    async with s.factory() as session:
        service = BotProvisioningService(session)
        with pytest.raises(AccessDeniedError):
            await service.set_web_booking_enabled(s.bots[0].id, s.owners[1].id, False)
        await service.set_web_booking_enabled(s.bots[0].id, s.owners[0].id, False)
        await session.commit()
    assert (await s.client.get(base(s) + "/services")).status_code == 404
    async with s.factory() as session:
        bot = await session.get(BotInstance, s.bots[0].id)
        bot.web_booking_enabled = True
        master = await session.get(Master, s.masters[0].id)
        master.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
    assert (await s.client.get(base(s) + "/services")).status_code == 403


@pytest.mark.asyncio
async def test_website_manual_prepayment_proof_shared_storage_without_client_chat(
    website,
):
    from app.database.models import PaymentProof
    from app.repositories.service_repository import ServiceRepository

    s = website
    await authenticated(s, s.client)
    async with s.factory() as session:
        await ServiceRepository(session).update_service(
            s.services[0].id, s.masters[0].id, deposit_value=50
        )
        await session.commit()
    held = await post(s.client, base(s) + "/holds", await selection(s))
    result = await post(
        s.client,
        base(s) + "/confirm",
        {
            "appointment_id": held.json()["id"],
            "phone": "+79991234567",
            "policy_agreed": True,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "WAITING_PAYMENT"
    payment = await s.client.get(base(s) + f"/appointments/{held.json()['id']}/payment")
    assert payment.json()["requisites"]["bank_name"] == "Bank"
    image = io.BytesIO()
    Image.new("RGB", (8, 8)).save(image, format="PNG")
    key = str(uuid.uuid4())
    endpoint = base(s) + f"/appointments/{held.json()['id']}/proof"
    invalid = await s.client.post(
        endpoint,
        headers={"Idempotency-Key": str(uuid.uuid4())},
        files={"file": ("../../unsafe.svg", b"<svg/>", "image/svg+xml")},
    )
    assert invalid.status_code == 413
    for _ in range(2):
        proof = await s.client.post(
            endpoint,
            headers={"Idempotency-Key": key},
            files={"file": ("../../receipt.png", image.getvalue(), "image/png")},
        )
        assert proof.status_code == 200, proof.text
    assert s.registry.bot.sends == 1
    async with s.factory() as session:
        assert await session.scalar(select(func.count(PaymentProof.id))) == 1


@pytest.mark.asyncio
async def test_website_appointment_visible_and_cancelled_in_existing_domain(website):
    from app.services.booking_service import BookingService

    s = website
    user_id = await authenticated(s, s.client)
    held = await post(s.client, base(s) + "/holds", await selection(s))
    confirmed = await post(
        s.client,
        base(s) + "/confirm",
        {
            "appointment_id": held.json()["id"],
            "phone": "+79991234567",
            "policy_agreed": True,
        },
    )
    assert confirmed.status_code == 200
    # Same cancellation operation used by Telegram; no website status mutation.
    async with s.factory() as session:
        await BookingService(session).cancel_booking_by_client(
            s.masters[0].id, held.json()["id"], user_id
        )
        await session.commit()
    account = await s.client.get("/api/web-booking/account/bookings")
    assert account.json()[0]["status"] == "CANCELLED_BY_CLIENT"
