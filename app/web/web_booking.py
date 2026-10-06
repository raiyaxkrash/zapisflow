"""Website transport: separate OIDC sessions, shared booking operations."""

import json
import secrets
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config.settings import settings
from app.database.models import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterStatus,
    User,
)
from app.repositories.user_repository import UserRepository
from app.services.master_authorization_service import AdminRole
from app.services.web_oidc import TelegramOIDC, digest
from app.web import miniapp as shared

router = APIRouter(prefix="/api")
COOKIE = "zf_web_session"
BINDING = "zf_web_login"


def website_origin():
    value = settings.web_booking_base_url.rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        shared.fail("UNAVAILABLE", "Веб-запись ещё не настроена", 503)
    return value


def check_origin(request):
    if request.headers.get("origin") != website_origin():
        shared.fail("ORIGIN_DENIED", "Запрос недопустим", 403)


async def identity(request: Request, session=shared.DB_SESSION):
    raw = request.cookies.get(COOKIE, "")
    redis = request.app.state.redis_client
    record = await redis.get(f"web:session:{digest(raw)}") if raw and redis else None
    if not record:
        shared.fail("SESSION_EXPIRED", "Войдите через Telegram", 401)
    record = json.loads(record)
    user = await session.get(User, record["user_id"])
    if not user:
        shared.fail("SESSION_EXPIRED", "Войдите через Telegram", 401)
    if request.method not in {"GET", "HEAD"}:
        check_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not secrets.compare_digest(digest(csrf), record["csrf_hash"]):
            shared.fail("CSRF_INVALID", "Обновите страницу", 403)
    return user, record


IDENTITY = Depends(identity, scope="function")


async def public_context(
    request: Request, bot_public_id: UUID, session=shared.DB_SESSION
):
    await shared.rate_limit(request, "web-public", 90)
    bot = await session.scalar(
        select(BotInstance).where(BotInstance.public_id == bot_public_id)
    )
    master = await session.get(Master, bot.master_id) if bot else None
    if (
        not shared.available(bot, master)
        or not bot.web_booking_enabled
        or bot.status != BotInstanceStatus.ACTIVE
        or master.status != MasterStatus.ACTIVE
    ):
        shared.fail("BOOKING_UNAVAILABLE", "Веб-запись недоступна", 404)
    if not await shared.SubscriptionAccessPolicy(session).can_accept_new_booking(
        master.id
    ):
        shared.fail("BOOKING_UNAVAILABLE", "Запись временно недоступна", 403)
    if (
        await shared.MasterSettingsRepository(session).get_by_master_id(master.id)
        is None
    ):
        shared.fail("BOOKING_UNAVAILABLE", "Запись временно недоступна", 403)
    return shared.Context(session, bot, master, None, AdminRole.NONE, None)


PUBLIC = Depends(public_context, scope="function")


async def authenticated_context(request: Request, c=PUBLIC, account=IDENTITY):
    c.user = account[0]
    if request.method not in {"GET", "HEAD"}:
        await c.session.scalar(
            select(BotInstance)
            .where(BotInstance.id == c.bot.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await c.session.scalar(
            select(Master)
            .where(Master.id == c.master.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            not c.bot.web_booking_enabled
            or c.bot.status != BotInstanceStatus.ACTIVE
            or c.master.status != MasterStatus.ACTIVE
        ):
            shared.fail("BOOKING_UNAVAILABLE", "Запись временно недоступна", 403)
    c.session.info.update(trusted_master_id=c.master.id, bot_instance_id=c.bot.id)
    return c


AUTHENTICATED = Depends(authenticated_context, scope="function")


async def record_context(
    request: Request, bot_public_id: UUID, account=IDENTITY, session=shared.DB_SESSION
):
    """Existing own bookings survive channel disable/subscription expiration.

    Record lookups still validate tenant and account ownership. Never used for
    new hold/confirm or the public catalog.
    """
    bot = await session.scalar(
        select(BotInstance).where(BotInstance.public_id == bot_public_id)
    )
    master = await session.get(Master, bot.master_id) if bot else None
    if bot is None or master is None:
        shared.fail()
    if request.method not in {"GET", "HEAD"}:
        await session.scalar(
            select(BotInstance).where(BotInstance.id == bot.id).with_for_update()
        )
        await session.scalar(
            select(Master).where(Master.id == master.id).with_for_update()
        )
    session.info.update(trusted_master_id=master.id, bot_instance_id=bot.id)
    return shared.Context(session, bot, master, account[0], AdminRole.NONE, None)


RECORD = Depends(record_context, scope="function")


def oidc(request):
    website_origin()
    redirect = urlsplit(settings.telegram_login_redirect_uri)
    if (
        not settings.telegram_login_client_id
        or not settings.telegram_login_client_secret.get_secret_value()
        or redirect.scheme != "https"
        or redirect.query
        or redirect.fragment
        or redirect.username
        or redirect.path != "/api/auth/telegram/callback"
        or f"{redirect.scheme}://{redirect.netloc}" != website_origin()
    ):
        shared.fail("LOGIN_UNAVAILABLE", "Вход через Telegram ещё не настроен", 503)
    return TelegramOIDC(
        request.app.state.redis_client,
        settings.telegram_login_client_id,
        settings.telegram_login_client_secret.get_secret_value(),
        settings.telegram_login_redirect_uri,
    )


@router.get("/auth/telegram/login")
async def login(
    request: Request,
    bot_public_id: UUID | None = None,
    service_id: int | None = Query(default=None, gt=0),
    staff_id: int | None = Query(default=None, gt=0),
    start_time: datetime | None = None,
):
    await shared.rate_limit(request, "web-login", 10)
    intent = {
        "bot_public_id": str(bot_public_id) if bot_public_id else None,
        "service_id": service_id,
        "staff_id": staff_id,
        "start_time": start_time.isoformat()
        if start_time and start_time.tzinfo
        else None,
    }
    url, binding = await oidc(request).begin(intent)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        BINDING,
        binding,
        secure=True,
        httponly=True,
        samesite="lax",
        max_age=600,
        path="/api/auth",
    )
    return response


@router.get("/auth/telegram/callback")
async def callback(
    request: Request, state: str = "", code: str = "", session=shared.DB_SESSION
):
    # Prevent the authorization code from appearing in Uvicorn access logs.
    request.scope["query_string"] = b""
    await shared.rate_limit(request, "web-login-callback", 20)
    try:
        claims, intent = await oidc(request).complete(
            state, code, request.cookies.get(BINDING, "")
        )
    except (ValueError, KeyError):
        shared.fail(
            "LOGIN_INVALID", "Вход не завершён. Повторите вход через Telegram", 401
        )
    await shared.lock(session, f"telegram-user:{claims['id']}")
    user, _ = await UserRepository(session).get_or_create(
        claims["id"],
        str(claims.get("given_name") or claims.get("name") or "Клиент")[:128],
        last_name=str(claims.get("family_name", ""))[:128] or None,
        username=str(claims.get("preferred_username", ""))[:64] or None,
    )
    if claims.get("phone_number_verified") is True and not user.phone:
        user.phone = str(claims.get("phone_number", ""))[:32] or None
    user_id = user.id
    # Commit identity before issuing a Redis browser session pointing to it.
    await session.commit()
    redis = request.app.state.redis_client
    old = request.cookies.get(COOKIE)
    if old:
        await redis.delete(f"web:session:{digest(old)}")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await redis.set(
        f"web:session:{digest(token)}",
        json.dumps(
            {
                "user_id": user_id,
                "csrf": csrf,
                "csrf_hash": digest(csrf),
                "intent": intent,
            }
        ),
        ex=settings.web_session_ttl_seconds,
    )
    destination = (
        f"/book/{intent['bot_public_id']}"
        if intent.get("bot_public_id")
        else "/account/bookings"
    )
    response = RedirectResponse(website_origin() + destination, status_code=303)
    response.set_cookie(
        COOKIE,
        token,
        secure=True,
        httponly=True,
        samesite="lax",
        max_age=settings.web_session_ttl_seconds,
        path="/api",
    )
    response.delete_cookie(
        BINDING, path="/api/auth", secure=True, httponly=True, samesite="lax"
    )
    return response


@router.get("/auth/me")
async def me(account=IDENTITY):
    user, record = account
    return {
        "first_name": user.first_name,
        "phone": user.phone,
        "csrf_token": record["csrf"],
        "intent": record["intent"],
    }


@router.post("/auth/logout")
async def logout(request: Request, response: Response, account=IDENTITY):
    await request.app.state.redis_client.delete(
        f"web:session:{digest(request.cookies[COOKIE])}"
    )
    response.delete_cookie(
        COOKIE, path="/api", secure=True, httponly=True, samesite="lax"
    )
    return {"ok": True}


@router.get("/web-booking/{bot_public_id}/context")
async def business(c=PUBLIC):
    config = await shared.MasterSettingsRepository(c.session).get_or_create(c.master.id)
    from app.services.branding import branding_context
    brand = await branding_context(c.session, c.master, c.bot)
    return {
        "branding": brand,
        "project": {
            "name": brand["brand_name"],
            "about": config.about_text,
            "timezone": c.master.timezone,
        },
        "contacts": {n: getattr(config, n) for n in shared.CONTACT_FIELD_LABELS},
        "cancel_policy_hours": config.cancel_policy_hours,
        "bot_username": c.bot.telegram_username,
    }


@router.get("/web-booking/{bot_public_id}/services")
async def services(c=PUBLIC):
    return await shared.services(staff_id=None, c=c)


@router.get("/web-booking/{bot_public_id}/staff")
async def staff(service_id: int = Query(gt=0), c=PUBLIC):
    return await shared.staff(service_id=service_id, c=c)


@router.get("/web-booking/{bot_public_id}/availability/calendar")
async def calendar(
    year: int = Query(ge=2000, le=2100),
    month: int = Query(ge=1, le=12),
    service_id: int = Query(gt=0),
    staff_id: int | None = Query(default=None, gt=0),
    c=PUBLIC,
):
    return await shared.client_calendar(
        year=year, month=month, service_id=service_id, staff_id=staff_id, c=c
    )


@router.get("/web-booking/{bot_public_id}/slots")
async def slots(
    target_date: shared.date,
    service_id: int = Query(gt=0),
    staff_id: int | None = Query(default=None, gt=0),
    c=PUBLIC,
):
    return await shared.slots(
        target_date=target_date, service_id=service_id, staff_id=staff_id, c=c
    )


@router.post("/web-booking/{bot_public_id}/holds")
async def hold(body: shared.HoldInput, request: Request, c=AUTHENTICATED):
    await shared.rate_limit(request, "web-create", 10)
    return await shared.hold(body, request, c)


@router.post("/web-booking/{bot_public_id}/confirm")
async def confirm(body: shared.ConfirmInput, request: Request, c=AUTHENTICATED):
    return await shared.confirm(body, request, c)


@router.get("/web-booking/{bot_public_id}/appointments")
async def appointments(c=RECORD):
    return await shared.my_appointments(c)


@router.post("/web-booking/{bot_public_id}/appointments/{id}/cancel")
async def cancel(id: int, request: Request, c=RECORD):
    return await shared.cancel(id, request, c)


@router.get("/web-booking/{bot_public_id}/appointments/{id}/payment")
async def payment(id: int, c=RECORD):
    return await shared.payment(id, c)


@router.get("/web-booking/account/bookings")
async def account_bookings(
    request: Request, account=IDENTITY, session=shared.DB_SESSION
):
    rows = (
        await session.execute(
            select(shared.Appointment.id, BotInstance.public_id)
            .join(BotInstance, BotInstance.master_id == shared.Appointment.master_id)
            .where(
                shared.Appointment.user_id == account[0].id,
                BotInstance.is_current.is_(True),
            )
            .order_by(shared.Appointment.start_time.desc())
            .limit(100)
        )
    ).all()
    result = []
    for appointment_id, public_id in rows:
        bot = await session.scalar(
            select(BotInstance).where(BotInstance.public_id == public_id)
        )
        master = await session.get(Master, bot.master_id)
        c = shared.Context(session, bot, master, account[0], AdminRole.NONE, None)
        item = shared.appointment_dto(
            await shared.appointment(c, appointment_id), tz=master.timezone
        )
        item["bot_public_id"] = str(public_id)
        result.append(item)
    return result


@router.post("/web-booking/{bot_public_id}/appointments/{id}/proof")
async def upload_proof(
    id: int, request: Request, file: UploadFile = shared.PROOF_FILE, c=RECORD
):
    await shared.rate_limit(request, "web-proof", 10)
    await shared.appointment(c, id)
    if not shared.available(c.bot, c.master):
        shared.fail(
            "PAYMENT_UNAVAILABLE", "Свяжитесь с мастером для передачи чека", 409
        )
    raw = await file.read(shared.MAX_UPLOAD + 1)
    await file.close()
    image = shared.safe_image(raw, file.content_type)
    # Telegram Login does not authorize this tenant bot to message the client.
    # Use the existing owner's chat for tenant-bot file storage instead.
    owner = await c.session.get(User, c.master.owner_user_id)
    if owner is None:
        shared.fail("PAYMENT_UNAVAILABLE", "Свяжитесь с мастером", 409)
    return await shared.submit_uploaded_proof(c, id, request, image, owner.telegram_id)


@router.get("/web-booking/{bot_public_id}/reviews")
async def public_reviews(c=PUBLIC):
    from app.web.branding import reviews
    return await reviews(c)


@router.get("/web-booking/{bot_public_id}/portfolio")
async def public_portfolio(c=PUBLIC):
    from app.web.branding import portfolio
    rows = await portfolio(c)
    for row in rows:
        row["image_url"] = f"/api/web-booking/{c.bot.public_id}/portfolio/{row['id']}/image"
    return rows


@router.get("/web-booking/{bot_public_id}/portfolio/{id}/image")
async def public_portfolio_image(id: int, request: Request, c=PUBLIC):
    from app.web.branding import portfolio_image
    return await portfolio_image(id, request, c)
