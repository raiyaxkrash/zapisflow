"""Thin Mini App adapters. One trusted bot/tenant context, existing domain services.

Same-origin /api/miniapp proxy on the app host avoids cross-domain cookies/CORS.
No endpoint accepts a tenant, role, user, price or status as booking authority.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qsl
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import BufferedInputFile
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.config.settings import settings
from app.config.url_validation import miniapp_origin
from app.core.token_crypto import TokenCrypto
from app.database.models import (
    Appointment,
    AppointmentStatus,
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterClient,
    MasterStatus,
    MediaType,
    Payment,
    PaymentProof,
    ScheduleException,
    User,
)
from app.database.models.miniapp import MiniAppOperation, MiniAppSession
from app.database.session import async_session_factory
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.staff_repository import StaffRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditService
from app.services.booking_service import BookingService
from app.services.crm_service import MasterCrmService
from app.services.exceptions import (
    AccessDeniedError,
    AppException,
    SlotAlreadyBookedError,
    SubscriptionExpiredError,
)
from app.services.master_authorization_service import (
    AdminRole,
    MasterAuthorizationService,
)
from app.services.master_contacts import CONTACT_FIELD_LABELS, normalize_contact_value
from app.services.miniapp_auth import (
    MiniAppError,
    digest,
    validate_init_data,
)
from app.services.payment_service import PaymentService
from app.services.slot_engine import SlotEngine
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.services.telegram_outbox import enqueue_telegram_message
from app.web.miniapp_contracts import (
    AppointmentOutput,
    AuthInput,
    ConfirmInput,
    DecisionInput,
    HoldInput,
    ManualInput,
    NotesInput,
    ScheduleInput,
    ServiceInput,
    ServiceOutput,
    SettingsInput,
    SlotsOutput,
    StaffInput,
    StaffOutput,
)

router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])
log = logging.getLogger(__name__)
COOKIE = "__Host-zapisflow-miniapp"
MAX_UPLOAD = 8 * 1024 * 1024


def fail(code="NOT_FOUND", message="Данные недоступны", status=404):
    raise MiniAppError(code, message, status)


def origin_check(request):
    if not settings.mini_app_base_url:
        fail("UNAVAILABLE", "Mini App ещё не настроен", 503)
    origin = miniapp_origin(settings.mini_app_base_url)
    if request.headers.get("origin") != origin:
        fail("ORIGIN_DENIED", "Откройте приложение из бота", 403)


async def rate_limit(request, scope, limit):
    redis = request.app.state.redis_client
    if redis is None:
        fail("UNAVAILABLE", "Попробуйте позже", 503)
    # Proxy deployments must overwrite X-Real-IP; direct untrusted headers ignored.
    ip = request.client.host if request.client else "unknown"
    key = f"miniapp:limit:{scope}:{digest(ip)}:{int(time.time()) // 60}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 90)
    except (RedisError, OSError, TimeoutError):
        fail("UNAVAILABLE", "Попробуйте позже", 503)
    if count > limit:
        fail("RATE_LIMIT", "Слишком много запросов. Попробуйте через минуту", 429)


async def db(request: Request):
    factory = request.app.state.session_factory or async_session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def lock(session, scope):
    value = int.from_bytes(
        hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": value})


DB_SESSION = Depends(db, scope="function")


def available(bot, master):
    return (
        bot is not None
        and master is not None
        and bot.is_current
        and bot.encrypted_token
        and bot.telegram_bot_id
        and bot.status in {BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED}
        and master.status not in {MasterStatus.SUSPENDED, MasterStatus.ARCHIVED}
    )


@dataclass
class Context:
    session: object
    bot: BotInstance
    master: Master
    user: User
    role: AdminRole
    staff_id: int | None

    def admin(self):
        if self.role not in {AdminRole.OWNER, AdminRole.ADMIN}:
            fail("FORBIDDEN", "Недостаточно прав", 403)

    def manager(self):
        if self.role == AdminRole.NONE:
            fail("FORBIDDEN", "Недостаточно прав", 403)


async def context(request: Request, session=DB_SESSION):
    if not settings.mini_app_base_url:
        fail("UNAVAILABLE", "Mini App ещё не настроен", 503)
    raw = request.cookies.get(COOKIE, "")
    record = await session.get(MiniAppSession, digest(raw)) if raw else None
    if record is None or record.expires_at <= datetime.now(UTC):
        fail("SESSION_EXPIRED", "Откройте приложение заново из Telegram", 401)
    await rate_limit(request, f"session:{record.token_hash}", 120)
    bot = await session.get(BotInstance, record.bot_instance_id)
    master = await session.get(Master, bot.master_id) if bot else None
    if (
        not available(bot, master)
        or bot.token_version != record.token_version
        or request.headers.get("x-miniapp-bot") != str(bot.public_id)
    ):
        fail("SESSION_EXPIRED", "Откройте приложение заново из Telegram", 401)
    user = await session.get(User, record.user_id)
    if user is None:
        fail("SESSION_EXPIRED", "Откройте приложение заново из Telegram", 401)
    if request.method not in {"GET", "HEAD"}:
        origin_check(request)
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not secrets.compare_digest(digest(csrf), record.csrf_hash):
            fail("CSRF_INVALID", "Откройте приложение заново", 403)
        # Serialize security-sensitive mutations with bot disable/rotation.
        await session.scalar(
            select(BotInstance)
            .where(BotInstance.id == bot.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await session.scalar(
            select(Master)
            .where(Master.id == master.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not available(bot, master) or bot.token_version != record.token_version:
            fail("SESSION_EXPIRED", "Откройте приложение заново из Telegram", 401)
    auth = MasterAuthorizationService(session)
    role = await auth.get_role(master.id, user.id)
    staff_id = (
        await auth.get_staff_id_for_user(master.id, user.id)
        if role == AdminRole.STAFF
        else None
    )
    session.info.update(trusted_master_id=master.id, bot_instance_id=bot.id)
    return Context(session, bot, master, user, role, staff_id)


TENANT_CONTEXT = Depends(context, scope="function")
PROOF_FILE = File()


def capability(c):
    return {
        "role": "client" if c.role == AdminRole.NONE else c.role.value.lower(),
        "can_manage": c.role != AdminRole.NONE,
        "can_edit_project": c.role in {AdminRole.OWNER, AdminRole.ADMIN},
        "online_payment_available": False,
    }


@router.post("/auth")
async def auth(
    body: AuthInput,
    request: Request,
    response: Response,
    session=DB_SESSION,
):
    origin_check(request)
    await rate_limit(request, f"auth:{body.bot_public_id}", 60)
    bot = await session.scalar(
        select(BotInstance)
        .where(BotInstance.public_id == body.bot_public_id)
        .with_for_update()
    )
    master = await session.get(Master, bot.master_id) if bot else None
    if not available(bot, master):
        fail("AUTH_INVALID", "Откройте доступного бота проекта", 401)
    try:
        token = TokenCrypto().decrypt(
            bot.encrypted_token, associated_data=bot.telegram_bot_id
        )
        tg_user = validate_init_data(
            body.init_data, token, max_age=settings.mini_app_auth_max_age_seconds
        )
    except AppException:
        fail("AUTH_INVALID", "Откройте доступного бота проекта", 401)
    # Encoding/order variations of the same signed query are still one credential.
    signed_hash = dict(parse_qsl(body.init_data))["hash"].lower()
    init_hash = digest(f"{bot.id}:{signed_hash}")
    await lock(session, f"miniapp-auth:{tg_user['id']}")
    used = await session.scalar(
        select(MiniAppSession).where(MiniAppSession.init_hash == init_hash)
    )
    # Re-auth from the same browser is allowed, but stolen/replayed initData
    # cannot issue a second session. Rotate CSRF after reload; cookie unchanged.
    existing_cookie = request.cookies.get(COOKIE, "")
    csrf = secrets.token_urlsafe(32)
    if used:
        if (
            not existing_cookie
            or used.token_hash != digest(existing_cookie)
            or used.expires_at <= datetime.now(UTC)
        ):
            fail("AUTH_REPLAY", "Откройте приложение заново из Telegram", 401)
        raw = existing_cookie
        used.csrf_hash = digest(csrf)
        user = await session.get(User, used.user_id)
    else:
        user, _ = await UserRepository(session).get_or_create(
            telegram_id=tg_user["id"],
            first_name=tg_user["first_name"],
            last_name=tg_user.get("last_name"),
            username=tg_user.get("username"),
            master_id=master.id,
        )
        raw = secrets.token_urlsafe(32)
        session.add(
            MiniAppSession(
                token_hash=digest(raw),
                csrf_hash=digest(csrf),
                init_hash=init_hash,
                bot_instance_id=bot.id,
                user_id=user.id,
                token_version=bot.token_version,
                expires_at=datetime.now(UTC)
                + timedelta(seconds=settings.mini_app_session_seconds),
            )
        )
    role = await MasterAuthorizationService(session).get_role(master.id, user.id)
    response.set_cookie(
        COOKIE,
        raw,
        secure=True,
        httponly=True,
        samesite="none",
        path="/",
        max_age=settings.mini_app_session_seconds,
    )
    return {
        "csrf_token": csrf,
        "capabilities": capability(Context(session, bot, master, user, role, None)),
    }


async def mutation(c, request, body, execute):
    key = request.headers.get("idempotency-key", "")
    try:
        key = str(UUID(key))
    except ValueError:
        fail("IDEMPOTENCY_REQUIRED", "Обновите приложение", 400)
    request_hash = digest(
        request.method
        + request.url.path
        + json.dumps(jsonable_encoder(body), sort_keys=True)
    )
    await lock(c.session, f"miniapp-op:{c.bot.id}:{c.user.id}:{key}")
    prior = await c.session.scalar(
        select(MiniAppOperation).where(
            MiniAppOperation.bot_instance_id == c.bot.id,
            MiniAppOperation.user_id == c.user.id,
            MiniAppOperation.key == key,
        )
    )
    if prior:
        if prior.request_hash != request_hash:
            fail(
                "IDEMPOTENCY_CONFLICT",
                "Запрос уже использован для другого действия",
                409,
            )
        return prior.result
    result = jsonable_encoder(await execute())
    c.session.add(
        MiniAppOperation(
            bot_instance_id=c.bot.id,
            user_id=c.user.id,
            key=key,
            request_hash=request_hash,
            result=result,
        )
    )
    await AuditService(c.session).log_event(
        "MINIAPP_MUTATION",
        actor_user_id=c.user.id,
        master_id=c.master.id,
        entity_type="MiniApp",
        payload_after={"endpoint": request.url.path},
    )
    await c.session.flush()
    return result


def service_dto(s):
    return {
        "id": s.id,
        "title": s.title,
        "description": s.description,
        "price": str(s.price),
        "duration_min": s.duration_min,
        "buffer_min": s.buffer_min,
        "deposit_type": s.deposit_type.value,
        "deposit_value": str(s.deposit_value),
        "is_active": s.is_active,
    }


def staff_dto(s):
    return {
        "id": s.id,
        "display_name": s.display_name,
        "specialization": s.specialization,
        "is_active": s.is_active,
    }


def appointment_dto(a, *, manager=False, tz="UTC"):
    result = {
        "id": a.id,
        "service": a.snapshot_service_title,
        "staff": a.staff.display_name,
        "start_time": a.start_time.astimezone(ZoneInfo(tz)).isoformat(),
        "status": a.status.value,
        "status_label": a.status.display_name,
        "price": str(a.snapshot_service_price),
        "deposit": str(a.snapshot_deposit_amount),
        "hold_until": a.hold_until.isoformat() if a.hold_until else None,
        "policy_agreed": a.cancel_policy_agreed,
        "cancel_allowed": a.status
        in {
            AppointmentStatus.WAITING_PAYMENT,
            AppointmentStatus.PAYMENT_PROOF_SENT,
            AppointmentStatus.CONFIRMED,
        },
        "cancel_reason": None
        if a.status
        in {
            AppointmentStatus.WAITING_PAYMENT,
            AppointmentStatus.PAYMENT_PROOF_SENT,
            AppointmentStatus.CONFIRMED,
        }
        else "Запись уже завершена или отменена",
        "cancel_consequences": "Внесённая предоплата при отмене не возвращается",
        "payment": [
            {
                "id": p.id,
                "status": p.status.value,
                "amount": str(p.amount),
                "rejection_reason": p.rejection_reason,
                "proofs": [
                    {"id": proof.id, "media_type": proof.media_type.value}
                    for proof in p.proofs
                ],
            }
            for p in a.payments
        ],
    }
    if manager:
        result.update(
            client=f"{a.user.first_name} {a.user.last_name or ''}".strip(),
            phone=a.user.phone,
            notes=a.admin_notes,
            source="manual" if a.is_manual_by_admin else "client",
        )
    return result


async def appointment(c, id, *, manager=False):
    a = await AppointmentRepository(c.session).get_by_id_with_relations(id, c.master.id)
    if a is None:
        fail()
    if manager:
        c.manager()
        if c.role == AdminRole.STAFF and (
            c.staff_id is None or a.staff_id != c.staff_id
        ):
            fail()
    elif a.user_id != c.user.id:
        fail()
    return a


async def notify(c, a, action, *, event_id=""):
    a = await AppointmentRepository(c.session).get_by_id_with_relations(
        a.id, c.master.id
    )
    text_value = f"{action}: {html.escape(a.snapshot_service_title)}\n{a.start_time.astimezone(ZoneInfo(c.master.timezone)):%d.%m.%Y %H:%M}"
    recipients = {a.user.telegram_id}
    recipients.update(
        await MasterAuthorizationService(c.session).get_admin_recipients(c.master.id)
    )
    for chat_id in recipients:
        await enqueue_telegram_message(
            c.session,
            master_id=c.master.id,
            bot_instance_id=c.bot.id,
            chat_id=chat_id,
            text=text_value,
            idempotency_key=f"miniapp:{a.id}:{action}:{event_id}:{chat_id}",
        )


@router.get("/context")
async def get_context(c=TENANT_CONTEXT):
    config = await MasterSettingsRepository(c.session).get_or_create(c.master.id)
    contacts = {name: getattr(config, name) for name in CONTACT_FIELD_LABELS}
    return {
        "project": {
            "name": c.master.display_name,
            "timezone": c.master.timezone,
            "about": config.about_text,
        },
        "user": {"first_name": c.user.first_name, "phone": c.user.phone},
        "contacts": contacts,
        "capabilities": capability(c),
        "today": datetime.now(ZoneInfo(c.master.timezone)).date().isoformat(),
        "booking_horizon_days": config.booking_horizon_days,
        "cancel_policy_hours": config.cancel_policy_hours,
        "can_book": c.bot.status == BotInstanceStatus.ACTIVE
        and await SubscriptionAccessPolicy(c.session).can_accept_new_booking(
            c.master.id
        ),
    }


async def eligible_staff(c, service_id):
    s = await ServiceRepository(c.session).get_by_id(service_id, c.master.id)
    if s is None or not s.is_active or s.is_archived:
        fail()
    repo = StaffRepository(c.session)
    result = []
    for staff in await repo.list_active(c.master.id):
        mapping = await repo.list_services_for_staff(staff.id, c.master.id)
        if not mapping or service_id in mapping:
            result.append(staff)
    return result


@router.get("/client/services", response_model=list[ServiceOutput])
async def services(
    staff_id: int | None = Query(default=None, gt=0),
    c=TENANT_CONTEXT,
):
    rows = await ServiceRepository(c.session).list_active(c.master.id)
    if staff_id is not None:
        staff = await StaffRepository(c.session).get_by_id(staff_id, c.master.id)
        if not staff or not staff.is_active:
            fail()
        mapping = await StaffRepository(c.session).list_services_for_staff(
            staff_id, c.master.id
        )
        if mapping:
            rows = [s for s in rows if s.id in mapping]
    return [service_dto(s) for s in rows]


@router.get("/client/staff", response_model=list[StaffOutput])
async def staff(service_id: int = Query(gt=0), c=TENANT_CONTEXT):
    return [staff_dto(s) for s in await eligible_staff(c, service_id)]


@router.get("/client/slots", response_model=SlotsOutput)
async def slots(
    target_date: date,
    service_id: int = Query(gt=0),
    staff_id: int | None = Query(default=None, gt=0),
    c=TENANT_CONTEXT,
):
    if (
        not await SubscriptionAccessPolicy(c.session).can_accept_new_booking(
            c.master.id
        )
        or c.bot.status != BotInstanceStatus.ACTIVE
    ):
        fail(
            "BOOKING_UNAVAILABLE", "Онлайн-запись недоступна. Свяжитесь с мастером", 403
        )
    choices = await eligible_staff(c, service_id)
    if staff_id is not None:
        choices = [s for s in choices if s.id == staff_id]
        if not choices:
            fail()
    times = set()
    for staff in choices:
        times.update(
            await SlotEngine(c.session).get_available_slots(
                service_id, target_date, c.master.id, staff_id=staff.id
            )
        )
    return {
        "timezone": c.master.timezone,
        "slots": [s.isoformat() for s in sorted(times)],
    }


@router.post(
    "/client/holds", response_model=AppointmentOutput, response_model_exclude_none=True
)
async def hold(body: HoldInput, request: Request, c=TENANT_CONTEXT):
    async def execute():
        if c.bot.status != BotInstanceStatus.ACTIVE:
            fail("BOOKING_UNAVAILABLE", "Проект ещё не принимает записи", 403)
        choices = await eligible_staff(c, body.service_id)
        if body.staff_id is not None:
            choices = [s for s in choices if s.id == body.staff_id]
        selected = None
        # Master lock in context serializes all concurrent HTTP bookings; DB
        # exclusion also protects conflicts with Telegram handlers/schedulers.
        for staff in choices:
            if await SlotEngine(c.session).is_slot_available(
                body.service_id, body.start_time, c.master.id, staff_id=staff.id
            ):
                selected = staff
                break
        if selected is None:
            fail("SLOT_TAKEN", "Это время уже занято. Выберите другой слот", 409)
        a, _ = await BookingService(c.session).create_hold_booking(
            c.master.id,
            c.user.id,
            body.service_id,
            body.start_time,
            cancel_policy_agreed=False,
            staff_id=selected.id,
            reserve_only=True,
        )
        a = await appointment(c, a.id)
        return appointment_dto(a, tz=c.master.timezone)

    return await mutation(c, request, body.model_dump(), execute)


@router.post(
    "/client/appointments",
    response_model=AppointmentOutput,
    response_model_exclude_none=True,
)
async def confirm(body: ConfirmInput, request: Request, c=TENANT_CONTEXT):
    async def execute():
        a = await appointment(c, body.appointment_id)
        if not body.policy_agreed:
            fail("POLICY_REQUIRED", "Подтвердите условия отмены")
        c.user.phone = body.phone
        a.cancel_policy_agreed = True
        a = await BookingService(c.session).confirm_reserved_booking(
            c.master.id, a.id, c.user.id
        )
        config = await MasterSettingsRepository(c.session).get_by_master_id(c.master.id)
        await AuditService(c.session).log_event(
            action="MINIAPP_POLICY_ACKNOWLEDGED",
            actor_user_id=c.user.id,
            master_id=c.master.id,
            entity_type="Appointment",
            entity_id=a.id,
            payload_after={
                "agreed": True,
                "cancel_policy_hours": config.cancel_policy_hours,
            },
        )
        await notify(c, a, "Запись создана")
        return appointment_dto(a, tz=c.master.timezone)

    return await mutation(c, request, body.model_dump(), execute)


@router.get(
    "/client/appointments",
    response_model=list[AppointmentOutput],
    response_model_exclude_none=True,
)
async def my_appointments(c=TENANT_CONTEXT):
    ids = (
        await c.session.scalars(
            select(Appointment.id)
            .where(
                Appointment.master_id == c.master.id, Appointment.user_id == c.user.id
            )
            .order_by(Appointment.start_time.desc())
            .limit(100)
        )
    ).all()
    return [
        appointment_dto(await appointment(c, id), tz=c.master.timezone) for id in ids
    ]


@router.post(
    "/client/appointments/{id}/cancel",
    response_model=AppointmentOutput,
    response_model_exclude_none=True,
)
async def cancel(id: int, request: Request, c=TENANT_CONTEXT):
    async def execute():
        a = await appointment(c, id)
        if a.status != AppointmentStatus.CANCELLED_BY_CLIENT:
            await BookingService(c.session).cancel_booking_by_client(
                c.master.id, id, c.user.id
            )
            await notify(c, a, "Запись отменена")
        return appointment_dto(a, tz=c.master.timezone)

    return await mutation(c, request, {"id": id}, execute)


@router.get("/client/appointments/{id}/payment")
async def payment(id: int, c=TENANT_CONTEXT):
    a = await appointment(c, id)
    if not a.payments or not a.cancel_policy_agreed:
        fail()
    config = await MasterSettingsRepository(c.session).get_by_master_id(c.master.id)
    return {
        "appointment": appointment_dto(a, tz=c.master.timezone),
        "online_payment_available": False,
        "requisites": {
            n: getattr(config, n, None)
            for n in ("bank_name", "bank_card_number", "bank_recipient_name")
        },
    }


def safe_image(raw: bytes, content_type: str | None) -> bytes:
    """Decode and re-encode images: no extension trust, metadata or polyglots."""
    from PIL import Image, UnidentifiedImageError

    if (
        not raw
        or len(raw) > MAX_UPLOAD
        or content_type not in {"image/jpeg", "image/png"}
    ):
        fail("UPLOAD_INVALID", "Выберите JPEG или PNG до 8 МБ", 413)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if (
                image.format not in {"JPEG", "PNG"}
                or image.width * image.height > 16000000
            ):
                raise ValueError
            image.load()
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=90)
            if output.tell() > MAX_UPLOAD:
                raise ValueError
            return output.getvalue()
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        fail("UPLOAD_INVALID", "Файл не является допустимым изображением")


@router.post("/client/appointments/{id}/proof")
async def upload(
    id: int,
    request: Request,
    file: UploadFile = PROOF_FILE,
    c=TENANT_CONTEXT,
):
    a = await appointment(c, id)
    if not a.cancel_policy_agreed:
        fail("POLICY_REQUIRED", "Сначала подтвердите запись")
    raw = await file.read(MAX_UPLOAD + 1)
    await file.close()
    image = safe_image(raw, file.content_type)

    async def execute():
        if not a.payments:
            fail()
        # Existing storage is Telegram. Relay a normalized receipt to the same
        # client's bot chat; only returned Telegram file IDs enter PaymentProof.
        # A crash after sendDocument can leave a duplicate media message, never
        # a double booking/payment. The response ledger protects normal retries.
        bot = await request.app.state.registry.get_by_instance_id(
            c.bot.id, session=c.session, expected_token_version=c.bot.token_version
        )
        a2, _p, proof = await PaymentService(c.session).submit_payment_proof(
            c.master.id,
            a.id,
            c.user.id,
            "pending-upload",
            "pending-upload",
            MediaType.DOCUMENT,
        )
        sent = await bot.send_document(
            c.user.telegram_id,
            BufferedInputFile(image, filename="receipt.jpg"),
            caption="Чек для проверки мастером",
        )
        proof.telegram_file_id = sent.document.file_id
        proof.telegram_file_unique_id = sent.document.file_unique_id
        await notify(c, a2, "Чек на проверке", event_id=str(proof.id))
        return {
            "proof_id": proof.id,
            "appointment": appointment_dto(
                await appointment(c, a.id), tz=c.master.timezone
            ),
        }

    return await mutation(
        c, request, {"id": id, "file_hash": hashlib.sha256(image).hexdigest()}, execute
    )


@router.get("/proofs/{id}")
async def proof(id: int, request: Request, c=TENANT_CONTEXT):
    row = await c.session.scalar(
        select(PaymentProof)
        .join(Payment)
        .where(PaymentProof.id == id, Payment.master_id == c.master.id)
    )
    if row is None:
        fail()
    p = await c.session.get(Payment, row.payment_id)
    if p.user_id != c.user.id:
        if c.role == AdminRole.NONE:
            fail()
        await appointment(c, p.appointment_id, manager=True)
    bot = await request.app.state.registry.get_by_instance_id(
        c.bot.id, session=c.session, expected_token_version=c.bot.token_version
    )
    remote = await bot.get_file(row.telegram_file_id)
    if remote.file_size is None or remote.file_size > MAX_UPLOAD:
        fail("UPLOAD_INVALID", "Файл превышает лимит", 413)
    data = io.BytesIO()
    await bot.download_file(remote.file_path, destination=data)
    if data.tell() > MAX_UPLOAD:
        fail("UPLOAD_INVALID", "Файл превышает лимит", 413)
    return Response(
        data.getvalue(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="receipt"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get(
    "/master/appointments",
    response_model=list[AppointmentOutput],
    response_model_exclude_none=True,
)
async def dashboard(target_date: date, c=TENANT_CONTEXT):
    c.manager()
    tz = ZoneInfo(c.master.timezone)
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz)
    query = select(Appointment.id).where(
        Appointment.master_id == c.master.id,
        Appointment.start_time >= start,
        Appointment.start_time < start + timedelta(days=1),
    )
    if c.role == AdminRole.STAFF:
        if c.staff_id is None:
            fail("FORBIDDEN", "Нет профиля специалиста", 403)
        query = query.where(Appointment.staff_id == c.staff_id)
    ids = (
        await c.session.scalars(query.order_by(Appointment.start_time).limit(200))
    ).all()
    return [
        appointment_dto(
            await appointment(c, id, manager=True), manager=True, tz=c.master.timezone
        )
        for id in ids
    ]


@router.get(
    "/master/appointments/{id}",
    response_model=AppointmentOutput,
    response_model_exclude_none=True,
)
async def appointment_card(id: int, c=TENANT_CONTEXT):
    return appointment_dto(
        await appointment(c, id, manager=True), manager=True, tz=c.master.timezone
    )


@router.post(
    "/master/payments/{id}/decision",
    response_model=AppointmentOutput,
    response_model_exclude_none=True,
)
async def decision(id: int, body: DecisionInput, request: Request, c=TENANT_CONTEXT):
    c.admin()

    async def execute():
        admin = await UserRepository(c.session).get_or_create_legacy_admin(c.user.id)
        service = PaymentService(c.session)
        result = (
            await service.approve_payment(c.master.id, id, admin.id)
            if body.approve
            else await service.reject_payment(c.master.id, id, admin.id, body.reason)
        )
        if result.changed:
            await notify(
                c,
                result.appointment,
                "Оплата подтверждена" if body.approve else "Чек отклонён",
            )
        return appointment_dto(
            await appointment(c, result.appointment.id, manager=True),
            manager=True,
            tz=c.master.timezone,
        )

    return await mutation(c, request, body.model_dump(), execute)


@router.post(
    "/master/appointments",
    response_model=AppointmentOutput,
    response_model_exclude_none=True,
)
async def manual(body: ManualInput, request: Request, c=TENANT_CONTEXT):
    c.admin()

    async def execute():
        client = await c.session.scalar(
            select(MasterClient).where(
                MasterClient.id == body.master_client_id,
                MasterClient.master_id == c.master.id,
            )
        )
        if client is None:
            fail()
        if body.staff_id is None or body.staff_id not in {
            s.id for s in await eligible_staff(c, body.service_id)
        }:
            fail("STAFF_UNAVAILABLE", "Выберите доступного специалиста", 409)
        if body.phone is not None:
            await UserRepository(c.session).update_phone(client.user_id, body.phone)
        a, _ = await BookingService(c.session).create_hold_booking(
            c.master.id,
            client.user_id,
            body.service_id,
            body.start_time,
            is_manual=True,
            admin_notes=body.notes,
            staff_id=body.staff_id,
        )
        await notify(c, a, "Запись создана")
        return appointment_dto(
            await appointment(c, a.id, manager=True), manager=True, tz=c.master.timezone
        )

    return await mutation(c, request, body.model_dump(), execute)


@router.get("/master/clients")
async def clients(
    search: str = Query(default="", max_length=100),
    c=TENANT_CONTEXT,
):
    c.admin()
    query = (
        select(MasterClient)
        .join(User)
        .where(MasterClient.master_id == c.master.id)
        .options(selectinload(MasterClient.user))
    )
    if search:
        query = query.where(
            User.first_name.icontains(search, autoescape=True)
            | User.last_name.icontains(search, autoescape=True)
        )
    rows = (
        await c.session.scalars(query.order_by(MasterClient.id.desc()).limit(100))
    ).all()
    return [
        {
            "id": r.id,
            "name": f"{r.user.first_name} {r.user.last_name or ''}".strip(),
            "phone": r.user.phone,
        }
        for r in rows
    ]


async def crm_client(c, id):
    c.admin()
    row = await c.session.scalar(
        select(MasterClient).where(
            MasterClient.id == id, MasterClient.master_id == c.master.id
        )
    )
    if row is None:
        fail()
    return row


@router.get("/master/clients/{id}")
async def client_card(id: int, c=TENANT_CONTEXT):
    row = await crm_client(c, id)
    card = await MasterCrmService(c.session).get_client_card(c.master.id, row.user_id)
    ids = (
        await c.session.scalars(
            select(Appointment.id)
            .where(
                Appointment.master_id == c.master.id, Appointment.user_id == row.user_id
            )
            .order_by(Appointment.start_time.desc())
            .limit(100)
        )
    ).all()
    return {
        "id": row.id,
        "name": card["full_name"],
        "phone": card["phone"],
        "notes": card["notes"],
        "total_bookings": card["total_bookings"],
        "total_spent": str(card["total_spent"]),
        "history": [
            appointment_dto(
                await appointment(c, id, manager=True),
                manager=True,
                tz=c.master.timezone,
            )
            for id in ids
        ],
    }


@router.patch("/master/clients/{id}")
async def notes(id: int, body: NotesInput, request: Request, c=TENANT_CONTEXT):
    row = await crm_client(c, id)

    async def execute():
        await MasterCrmService(c.session).update_client_notes(
            c.master.id, row.user_id, c.user.id, body.notes
        )
        return {"id": row.id, "notes": body.notes}

    return await mutation(c, request, body.model_dump(), execute)


@router.get("/master/settings")
async def get_settings(c=TENANT_CONTEXT):
    c.admin()
    data = await MasterSettingsRepository(c.session).get_all_settings_dict(c.master.id)
    data.pop("master_id", None)
    return data


@router.patch("/master/settings")
async def edit_settings(body: SettingsInput, request: Request, c=TENANT_CONTEXT):
    c.admin()

    async def execute():
        changes = body.model_dump(exclude_unset=True)
        for name in changes.keys() & CONTACT_FIELD_LABELS.keys():
            changes[name] = normalize_contact_value(name, changes[name])
        await MasterSettingsRepository(c.session).update_settings(
            c.master.id, **changes
        )
        return await get_settings(c)

    return await mutation(c, request, body.model_dump(exclude_unset=True), execute)


@router.get("/master/services")
async def master_services(c=TENANT_CONTEXT):
    c.admin()
    return [
        service_dto(s)
        for s in await ServiceRepository(c.session).list_all_for_admin(c.master.id)
    ]


@router.post("/master/services")
async def create_service(body: ServiceInput, request: Request, c=TENANT_CONTEXT):
    c.admin()

    async def execute():
        return service_dto(
            await ServiceRepository(c.session).create_service(
                c.master.id, **body.model_dump()
            )
        )

    return await mutation(c, request, body.model_dump(), execute)


@router.put("/master/services/{id}")
async def edit_service(id: int, body: ServiceInput, request: Request, c=TENANT_CONTEXT):
    c.admin()

    async def execute():
        s = await ServiceRepository(c.session).update_service(
            id, c.master.id, **body.model_dump()
        )
        if s is None:
            fail()
        return service_dto(s)

    return await mutation(c, request, body.model_dump(), execute)


@router.get("/master/staff")
async def team(c=TENANT_CONTEXT):
    c.admin()
    repo = StaffRepository(c.session)
    return [
        {
            **staff_dto(s),
            "service_ids": list(await repo.list_services_for_staff(s.id, c.master.id)),
        }
        for s in await repo.list_all(c.master.id)
    ]


@router.put("/master/staff/{id}")
async def edit_staff(id: int, body: StaffInput, request: Request, c=TENANT_CONTEXT):
    c.admin()

    async def execute():
        repo = StaffRepository(c.session)
        row = await repo.get_by_id(id, c.master.id)
        if row is None:
            fail()
        for service_id in body.service_ids:
            if (
                await ServiceRepository(c.session).get_by_id(service_id, c.master.id)
                is None
            ):
                fail()
        row = await repo.update_staff(
            id, c.master.id, **body.model_dump(exclude={"service_ids"})
        )
        await repo.set_staff_services(id, c.master.id, body.service_ids)
        return {**staff_dto(row), "service_ids": body.service_ids}

    return await mutation(c, request, body.model_dump(), execute)


@router.get("/master/schedule")
async def get_schedule(staff_id: int = Query(gt=0), c=TENANT_CONTEXT):
    c.admin()
    if await StaffRepository(c.session).get_by_id(staff_id, c.master.id) is None:
        fail()
    templates = await ScheduleRepository(c.session).get_weekly_templates(
        c.master.id, staff_id
    )
    exceptions = (
        await c.session.scalars(
            select(ScheduleException)
            .where(
                ScheduleException.master_id == c.master.id,
                (
                    (ScheduleException.staff_id == staff_id)
                    | ScheduleException.staff_id.is_(None)
                ),
            )
            .options(selectinload(ScheduleException.breaks))
            .order_by(ScheduleException.date)
            .limit(200)
        )
    ).all()

    def dto(r):
        return {
            "is_day_off": r.is_day_off,
            "work_start": r.work_start.isoformat() if r.work_start else None,
            "work_end": r.work_end.isoformat() if r.work_end else None,
            "breaks": [
                [b.break_start.isoformat(), b.break_end.isoformat()] for b in r.breaks
            ],
        }

    return {
        "weekly": [{**dto(r), "weekday": r.day_of_week} for r in templates],
        "dates": [
            {
                **dto(r),
                "target_date": r.date.isoformat(),
                "scope": "project" if r.staff_id is None else "staff",
            }
            for r in exceptions
        ],
    }


@router.put("/master/schedule")
async def edit_schedule(body: ScheduleInput, request: Request, c=TENANT_CONTEXT):
    c.admin()

    async def execute():
        if (
            await StaffRepository(c.session).get_by_id(body.staff_id, c.master.id)
            is None
        ):
            fail()
        repo = ScheduleRepository(c.session)
        kwargs = {
            "is_day_off": body.is_day_off,
            "work_start": body.work_start,
            "work_end": body.work_end,
            "breaks": body.breaks,
            "master_id": c.master.id,
            "staff_id": body.staff_id,
        }
        if body.weekday is not None:
            await repo.set_template(body.weekday, **kwargs)
        else:
            await repo.set_date_exception(body.target_date, **kwargs)
        return await get_schedule(body.staff_id, c)

    return await mutation(c, request, body.model_dump(), execute)


def install_miniapp(app):
    app.include_router(router)

    @app.middleware("http")
    async def miniapp_safety(request, call_next):
        if not request.url.path.startswith("/api/miniapp"):
            return await call_next(request)
        started = time.monotonic()
        # Bound raw multipart/JSON body before parsers can buffer arbitrary data.
        from app.web.app import read_limited_request_body

        try:
            if request.method not in {"GET", "HEAD"}:
                request._body = await read_limited_request_body(
                    request, MAX_UPLOAD + 65536
                )
            response = await call_next(request)
            if response.status_code == 422:
                response = JSONResponse(
                    {"code": "INPUT_INVALID", "message": "Проверьте введённые данные"},
                    status_code=422,
                )
        except MiniAppError as exc:
            response = JSONResponse(
                {"code": exc.code, "message": exc.message}, status_code=exc.status
            )
        except HTTPException as exc:
            response = JSONResponse(
                {"code": "REQUEST_INVALID", "message": "Запрос недопустим"},
                status_code=exc.status_code,
            )
        except (
            SlotAlreadyBookedError,
            SubscriptionExpiredError,
            AccessDeniedError,
        ) as exc:
            code, message, status = (
                ("SLOT_TAKEN", "Это время уже занято", 409)
                if isinstance(exc, SlotAlreadyBookedError)
                else ("FORBIDDEN", "Действие сейчас недоступно", 403)
            )
            response = JSONResponse(
                {"code": code, "message": message}, status_code=status
            )
        except (AppException, ValueError, LookupError):
            response = JSONResponse(
                {
                    "code": "ACTION_UNAVAILABLE",
                    "message": "Действие недоступно. Обновите данные",
                },
                status_code=409,
            )
        except Exception as exc:  # noqa: BLE001 - central safe HTTP error boundary
            # Deliberately log type, never initData, upload, secret or SQL text.
            log.error(
                "Mini App failure endpoint=%s error_type=%s",
                request.url.path,
                type(exc).__name__,
            )
            response = JSONResponse(
                {
                    "code": "UNAVAILABLE",
                    "message": "Не удалось выполнить действие. Попробуйте позже",
                },
                status_code=503,
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        log.info(
            "Mini App endpoint=%s status=%s latency_ms=%s",
            request.url.path,
            response.status_code,
            round((time.monotonic() - started) * 1000),
        )
        return response
