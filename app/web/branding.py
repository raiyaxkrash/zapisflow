"""Tenant presentation adapters. Owner permissions never depend on browser IDs."""

import asyncio
import io
from typing import Literal
from uuid import UUID

from aiogram.exceptions import TelegramAPIError
from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from sqlalchemy import select

from app.database.models import (
    BotInstance,
    Master,
    MasterBrandAsset,
    PortfolioItem,
    Review,
)
from app.services.branding import (
    MAX_BRAND_UPLOAD,
    BrandingInput,
    BrandingSaveInput,
    validate_brand_filename,
    branding_context,
    normalize_brand_image,
    save_asset,
    save_branding,
)
from app.web import miniapp as shared

PROOF_FILE = File()
router = APIRouter(prefix="/api/miniapp")
public_router = APIRouter(prefix="/api/branding")


def owner(c):
    if c.user.id != c.master.owner_user_id:
        shared.fail("FORBIDDEN", "Оформление может менять только владелец", 403)


@router.get("/master/branding")
async def get_branding(c=shared.TENANT_CONTEXT):
    owner(c)
    return await branding_context(c.session, c.master, c.bot)


@router.post("/master/branding/save")
async def save_editor(request: Request, payload: str = Form(), logo: UploadFile | None = File(default=None), cover: UploadFile | None = File(default=None), c=shared.TENANT_CONTEXT):
    owner(c)
    await shared.rate_limit(request, "branding-save", 10)
    from pydantic import ValidationError
    from app.repositories.master_settings_repository import MasterSettingsRepository
    import hashlib
    try:
        body = BrandingSaveInput.model_validate_json(payload)
    except ValidationError:
        shared.fail("INPUT_INVALID", "Проверьте поля оформления", 422)
    images = {}
    for kind, file in (("logo", logo), ("cover", cover)):
        if file:
            try:
                validate_brand_filename(file.filename, file.content_type)
                images[kind] = normalize_brand_image(await file.read(MAX_BRAND_UPLOAD + 1), file.content_type, kind)
            except ValueError as exc:
                shared.fail("UPLOAD_INVALID", str(exc), 413)
            finally:
                await file.close()
    async def execute():
        await save_branding(c.session, c.master.id, body.branding)
        await MasterSettingsRepository(c.session).update_settings(c.master.id, **body.contacts.model_dump(exclude_unset=True))
        for kind in ("logo", "cover"):
            if getattr(body, "delete_" + kind):
                asset = await c.session.get(MasterBrandAsset, (c.master.id, kind))
                if asset:
                    await c.session.delete(asset)
                    await c.session.flush()
            if kind in images:
                await save_asset(c.session, c.master.id, kind, images[kind])
        return await branding_context(c.session, c.master, c.bot)
    return await shared.mutation(c, request, {"settings": body.model_dump(), "assets": {kind: hashlib.sha256(value).hexdigest() for kind, value in images.items()}}, execute)


@router.put("/master/branding")
async def put_branding(body: BrandingInput, request: Request, c=shared.TENANT_CONTEXT):
    owner(c)

    async def execute():
        await save_branding(c.session, c.master.id, body)
        return await branding_context(c.session, c.master, c.bot)

    return await shared.mutation(c, request, body.model_dump(), execute)


@router.post("/master/branding/reset")
async def reset_branding(request: Request, c=shared.TENANT_CONTEXT):
    owner(c)

    async def execute():
        await save_branding(c.session, c.master.id, BrandingInput())
        for kind in ("logo", "cover"):
            asset = await c.session.get(MasterBrandAsset, (c.master.id, kind))
            if asset:
                await c.session.delete(asset)
        await c.session.flush()
        return await branding_context(c.session, c.master, c.bot)

    return await shared.mutation(c, request, {}, execute)


@router.post("/master/branding/assets/{kind}")
async def upload_asset(
    kind: Literal["logo", "cover"],
    request: Request,
    file: UploadFile = PROOF_FILE,
    c=shared.TENANT_CONTEXT,
):
    owner(c)
    await shared.rate_limit(request, "branding-upload", 10)
    raw = await file.read(MAX_BRAND_UPLOAD + 1)
    try:
        content = normalize_brand_image(raw, file.content_type, kind)
    except ValueError as exc:
        shared.fail("UPLOAD_INVALID", str(exc), 413)
    import hashlib

    async def execute():
        await save_asset(c.session, c.master.id, kind, content)
        return await branding_context(c.session, c.master, c.bot)

    return await shared.mutation(
        c,
        request,
        {"kind": kind, "content_hash": hashlib.sha256(content).hexdigest()},
        execute,
    )


@router.delete("/master/branding/assets/{kind}")
async def delete_asset(
    kind: Literal["logo", "cover"], request: Request, c=shared.TENANT_CONTEXT
):
    owner(c)

    async def execute():
        asset = await c.session.get(MasterBrandAsset, (c.master.id, kind))
        if asset:
            await c.session.delete(asset)
        await c.session.flush()
        return await branding_context(c.session, c.master, c.bot)

    return await shared.mutation(c, request, {"kind": kind}, execute)


@router.post("/master/branding/sync-telegram")
async def sync_telegram(request: Request, c=shared.TENANT_CONTEXT):
    owner(c)
    await shared.rate_limit(request, "branding-profile", 5)
    brand = await branding_context(c.session, c.master, c.bot)
    try:
        bot = await request.app.state.registry.get_by_instance_id(
            c.bot.id, session=c.session, expected_token_version=c.bot.token_version
        )
        async with asyncio.timeout(10):
            await bot.set_my_name(name=brand["brand_name"][:64])
            await bot.set_my_description(description=brand["description"][:512])
            await bot.set_my_short_description(short_description=brand["tagline"][:120])
    except (TelegramAPIError, TimeoutError, OSError):
        # Desired brand is already stored. Explicit retry safely reconciles partial external updates.
        return {
            "ok": False,
            "message": "Оформление сохранено. Telegram временно недоступен — повторите синхронизацию.",
        }
    return {"ok": True, "message": "Профиль Telegram обновлён"}


@public_router.get("/{public_id}/assets/{kind}")
async def public_asset(
    public_id: UUID,
    kind: Literal["logo", "cover"],
    request: Request,
    session=shared.DB_SESSION,
):
    await shared.rate_limit(request, "branding-read", 120)
    bot = await session.scalar(
        select(BotInstance).where(BotInstance.public_id == public_id)
    )
    master = await session.get(Master, bot.master_id) if bot else None
    if not shared.available(bot, master):
        shared.fail()
    asset = await session.get(MasterBrandAsset, (master.id, kind))
    if not asset:
        shared.fail()
    return Response(
        asset.content,
        media_type="image/webp",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/client/reviews")
async def reviews(c=shared.TENANT_CONTEXT):
    brand = await branding_context(c.session, c.master, c.bot)
    if not brand["show_reviews"]:
        return []
    rows = (
        await c.session.scalars(
            select(Review)
            .where(Review.master_id == c.master.id)
            .order_by(Review.created_at.desc())
            .limit(30)
        )
    ).all()
    return [
        {
            "rating": r.rating,
            "comment": r.comment,
            "date": r.created_at.date().isoformat(),
        }
        for r in rows
    ]


@router.get("/client/portfolio")
async def portfolio(c=shared.TENANT_CONTEXT):
    brand = await branding_context(c.session, c.master, c.bot)
    if not brand["show_portfolio"]:
        return []
    rows = (
        await c.session.scalars(
            select(PortfolioItem)
            .where(
                PortfolioItem.master_id == c.master.id,
                PortfolioItem.is_active.is_(True),
            )
            .order_by(PortfolioItem.display_order, PortfolioItem.id)
            .limit(30)
        )
    ).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "caption": r.caption,
            "image_url": f"/api/miniapp/client/portfolio/{r.id}/image",
        }
        for r in rows
    ]


@router.get("/client/portfolio/{id}/image")
async def portfolio_image(id: int, request: Request, c=shared.TENANT_CONTEXT):
    brand = await branding_context(c.session, c.master, c.bot)
    row = await c.session.scalar(
        select(PortfolioItem).where(
            PortfolioItem.id == id,
            PortfolioItem.master_id == c.master.id,
            PortfolioItem.is_active.is_(True),
        )
    )
    if not row or not brand["show_portfolio"]:
        shared.fail()
    bot = await request.app.state.registry.get_by_instance_id(
        c.bot.id, session=c.session, expected_token_version=c.bot.token_version
    )
    remote = await bot.get_file(row.telegram_file_id)
    if remote.file_size and remote.file_size > shared.MAX_UPLOAD:
        shared.fail("UPLOAD_INVALID", "Изображение слишком большое", 413)

    class BoundedImage(io.BytesIO):
        def write(self, chunk):
            if self.tell() + len(chunk) > shared.MAX_UPLOAD:
                shared.fail("UPLOAD_INVALID", "Изображение слишком большое", 413)
            return super().write(chunk)

    content = await bot.download_file(
        remote.file_path, destination=BoundedImage(), timeout=10
    )
    raw = content.getvalue()
    image = shared.safe_image(raw, "image/jpeg")
    return Response(
        image, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"}
    )
