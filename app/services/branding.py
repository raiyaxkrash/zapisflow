"""Validated project presentation. No booking rules, transactions or Telegram calls."""

from __future__ import annotations

import hashlib
import io
import re
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from app.database.models import MasterBrandAsset
from app.repositories.master_settings_repository import MasterSettingsRepository


class BrandingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    brand_name: str = Field(default="", max_length=128)
    tagline: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=2000)
    welcome_text: str = Field(default="", max_length=1000)
    booking_cta_label: str = Field(default="Записаться", max_length=32)
    accent_color: str = "#1D72FE"
    theme_mode: Literal["system", "light", "dark"] = "system"
    appearance_preset: Literal["clean", "soft", "compact"] = "clean"
    show_portfolio: bool = True
    show_reviews: bool = True
    show_contacts: bool = True
    show_staff: bool = True

    @field_validator("accent_color")
    @classmethod
    def color(cls, value):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Укажите цвет в формате #RRGGBB")
        return value.upper()

    @field_validator(
        "brand_name", "tagline", "description", "welcome_text", "booking_cta_label"
    )
    @classmethod
    def plain_text(cls, value):
        if re.search(r"[<>\x00-\x08\x0b\x0c\x0e-\x1f]", value):
            raise ValueError("Используйте обычный текст без HTML")
        return value

    @field_validator("booking_cta_label")
    @classmethod
    def cta(cls, value):
        return value or "Записаться"


async def branding_context(session, master, bot=None):
    config = await MasterSettingsRepository(session).get_or_create(master.id)
    try:
        brand = BrandingInput.model_validate(config.branding or {}).model_dump()
    except (ValidationError, TypeError):
        brand = BrandingInput().model_dump()
    brand["brand_name"] = brand["brand_name"] or master.display_name
    brand["description"] = brand["description"] or config.about_text or ""
    brand["powered_by"] = "Работает на ZapisFlow"
    assets = (
        await session.execute(
            select(MasterBrandAsset.kind, MasterBrandAsset.revision).where(
                MasterBrandAsset.master_id == master.id
            )
        )
    ).all()
    for kind in ("logo", "cover"):
        asset = next((a for a in assets if a.kind == kind), None)
        brand[f"{kind}_url"] = (
            f"/api/branding/{bot.public_id}/assets/{kind}?v={asset.revision}"
            if bot and asset
            else None
        )
    return brand


async def save_branding(session, master_id, body):
    await MasterSettingsRepository(session).update_settings(
        master_id, branding=body.model_dump()
    )
    await session.flush()


MAX_BRAND_UPLOAD = 4 * 1024 * 1024


def normalize_brand_image(raw, mime, kind):
    """Same decode/re-encode boundary as payment proofs, with WebP support and bounded output."""
    if (
        kind not in {"logo", "cover"}
        or mime not in {"image/jpeg", "image/png", "image/webp"}
        or not raw
        or len(raw) > MAX_BRAND_UPLOAD
    ):
        raise ValueError("Выберите PNG, JPEG или WebP до 4 МБ")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if (
                image.format not in {"JPEG", "PNG", "WEBP"}
                or image.width * image.height > 16000000
            ):
                raise ValueError("Недопустимое изображение")
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGBA")
            image.thumbnail((320, 320) if kind == "logo" else (1200, 600))
            result = io.BytesIO()
            image.save(result, format="WEBP", quality=82, method=4)
            if result.tell() > 262144:
                raise ValueError("Изображение слишком большое после обработки")
            return result.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Не удалось прочитать изображение") from exc


async def save_asset(session, master_id, kind, content):
    asset = await session.get(MasterBrandAsset, (master_id, kind))
    if asset is None:
        asset = MasterBrandAsset(master_id=master_id, kind=kind)
        session.add(asset)
    asset.content = content
    asset.revision = hashlib.sha256(content).hexdigest()
    await session.flush()

class BrandContactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    studio_address: str | None = Field(default=None, max_length=500)
    studio_phone: str | None = Field(default=None, max_length=64)
    whatsapp_phone: str | None = Field(default=None, max_length=64)
    telegram_username: str | None = Field(default=None, max_length=64)
    vk_profile: str | None = Field(default=None, max_length=128)

    @field_validator("studio_address", "studio_phone", "whatsapp_phone", "telegram_username", "vk_profile")
    @classmethod
    def safe_contact(cls, value, info):
        from app.services.master_contacts import normalize_contact_value
        return normalize_contact_value(info.field_name, value) if value else None


class BrandingSaveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branding: BrandingInput
    contacts: BrandContactsInput = Field(default_factory=BrandContactsInput)
    delete_logo: bool = False
    delete_cover: bool = False


def validate_brand_filename(filename, mime):
    extensions = {"image/png": {"png"}, "image/jpeg": {"jpg", "jpeg"}, "image/webp": {"webp"}}
    if not filename or any(c in filename for c in ["/", "\\", "\x00"]) or filename.rsplit(".", 1)[-1].lower() not in extensions.get(mime, set()):
        raise ValueError("Имя файла должно иметь расширение PNG, JPEG или WebP")
