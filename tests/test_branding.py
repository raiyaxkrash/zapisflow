"""Presentation security and real PostgreSQL cross-channel regression coverage."""

import io
import uuid
from unittest.mock import AsyncMock

import pytest
import test_miniapp as base
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from app.database.models import MasterBrandAsset, MasterSettings
from app.services.branding import BrandingInput, normalize_brand_image

system = base.system
miniapp_database_url = base.miniapp_database_url


@pytest.mark.parametrize(
    "color",
    [
        "url(javascript:alert(1))",
        "var(--x)",
        "#fff",
        "expression(x)",
        "red",
        "#12345G",
        "javascript:",
    ],
)
def test_accent_injection_rejected(color):
    with pytest.raises(ValidationError):
        BrandingInput(accent_color=color)


@pytest.mark.parametrize(
    "field",
    ["brand_name", "tagline", "description", "welcome_text", "booking_cta_label"],
)
def test_html_and_huge_text_rejected(field):
    with pytest.raises(ValidationError):
        BrandingInput(**{field: "<script>alert(1)</script>"})
    with pytest.raises(ValidationError):
        BrandingInput(**{field: "x" * 3000})


def test_defaults_and_plain_text():
    body = BrandingInput(booking_cta_label="", brand_name="  Studio Anna  ")
    assert body.booking_cta_label == "Записаться"
    assert body.brand_name == "Studio Anna"
    assert body.show_contacts and body.show_staff and body.theme_mode == "system"
    with pytest.raises(ValidationError):
        BrandingInput(custom_css="body{}")


@pytest.mark.parametrize("kind", ["logo", "cover"])
@pytest.mark.parametrize(
    "fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_images_normalized_bounded_metadata_free(kind, fmt, mime):
    raw = io.BytesIO()
    Image.new("RGB", (1300, 700), "red").save(raw, format=fmt)
    content = normalize_brand_image(raw.getvalue(), mime, kind)
    image = Image.open(io.BytesIO(content))
    assert image.format == "WEBP" and len(content) <= 262144
    assert image.width <= (320 if kind == "logo" else 1200)
    assert not image.getexif()


@pytest.mark.parametrize(
    "raw,mime",
    [
        (b'<svg onload="alert(1)"></svg>', "image/svg+xml"),
        (b"x", "image/png"),
        (b"x" * (4 * 1024 * 1024 + 1), "image/png"),
    ],
    ids=["svg", "invalid", "oversized"],
)
def test_invalid_and_oversized_upload(raw, mime):
    with pytest.raises(ValueError):
        normalize_brand_image(raw, mime, "logo")


async def owner(system, index=0):
    return await base.login(system, user=11001 + index, bot_index=index)


@pytest.mark.asyncio
async def test_branding_owner_only_csrf_defaults_cross_channel_reset(system):
    client = await base.login(system)
    assert (await client.get("/api/miniapp/master/branding")).status_code == 403
    client = await base.login(system, user=12001)
    result = await base.post(client, "/master/branding", {}, method="PUT")
    assert result.status_code == 403, result.text
    client = await owner(system)
    body = BrandingInput(
        brand_name="Studio Anna",
        accent_color="#FDFD20",
        show_portfolio=False,
        show_reviews=False,
        show_contacts=False,
    ).model_dump()
    result = await base.post(client, "/master/branding", body, method="PUT")
    assert result.status_code == 200, result.text
    context = (await client.get("/api/miniapp/context")).json()
    assert context["branding"]["accent_color"] == "#FDFD20"
    assert context["project"]["name"] == "Studio Anna"
    async with system.factory() as session:
        config = await session.get(MasterSettings, system.masters[0].id)
        assert config.branding["brand_name"] == "Studio Anna"
        other = await session.get(MasterSettings, system.masters[1].id)
        assert other.branding == {}
    wrong = {"X-CSRF-Token": "wrong", "Idempotency-Key": str(uuid.uuid4())}
    assert (
        await client.put("/api/miniapp/master/branding", headers=wrong, json=body)
    ).status_code == 403
    assert (await client.get("/api/miniapp/client/reviews")).json() == []
    reset = await base.post(client, "/master/branding/reset", {})
    assert reset.status_code == 200
    assert reset.json()["brand_name"] == "Studio 0"


@pytest.mark.asyncio
async def test_brand_asset_upload_replay_and_tenant_scope(system):
    client = await owner(system)
    data = io.BytesIO()
    Image.new("RGB", (600, 600), "blue").save(data, format="PNG")
    key = str(uuid.uuid4())
    headers = {"Idempotency-Key": key}
    for _ in range(2):
        result = await client.post(
            "/api/miniapp/master/branding/assets/logo",
            files={"file": ("../../etc/passwd.png", data.getvalue(), "image/png")},
            headers=headers,
        )
        assert result.status_code == 200, result.text
    url = result.json()["logo_url"]
    asset = await client.get(url)
    assert asset.status_code == 200 and asset.headers["content-type"] == "image/webp"
    other_url = url.replace(
        str(system.bots[0].public_id), str(system.bots[1].public_id)
    )
    assert (await client.get(other_url)).status_code == 404
    async with system.factory() as session:
        rows = (await session.scalars(select(MasterBrandAsset))).all()
        assert len(rows) == 1 and rows[0].master_id == system.masters[0].id
    result = await client.post(
        "/api/miniapp/master/branding/assets/cover",
        files={"file": ("image.svg", b'<svg onload="alert(1)"/>', "image/svg+xml")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert result.status_code == 413
    await base.post(client, "/master/branding/reset", {})
    assert (await client.get(url)).status_code == 404


@pytest.mark.asyncio
async def test_corrupted_branding_fails_safe(system):
    async with system.factory() as session:
        row = await session.get(MasterSettings, system.masters[0].id)
        row.branding = {"accent_color": "url(javascript:evil)"}
        await session.commit()
    client = await base.login(system)
    brand = (await client.get("/api/miniapp/context")).json()["branding"]
    assert brand["accent_color"] == "#1D72FE"


@pytest.mark.asyncio
async def test_telegram_sync_timeout_does_not_lose_branding(system):
    client = await owner(system)
    await base.post(
        client,
        "/master/branding",
        BrandingInput(brand_name="Studio Anna").model_dump(),
        method="PUT",
    )
    bot = AsyncMock()
    bot.set_my_name.side_effect = TimeoutError()
    system.registry.get_by_instance_id = AsyncMock(return_value=bot)
    response = await base.post(client, "/master/branding/sync-telegram", {})
    assert response.status_code == 200 and response.json()["ok"] is False
    assert (await client.get("/api/miniapp/context")).json()["project"][
        "name"
    ] == "Studio Anna"
    bot.set_my_name.side_effect = None
    response = await base.post(client, "/master/branding/sync-telegram", {})
    assert response.json()["ok"] is True
    assert (await client.get("/api/miniapp/master/branding")).json()[
        "brand_name"
    ] == "Studio Anna"


@pytest.mark.asyncio
async def test_branding_one_project_across_bot_miniapp_and_website(system, monkeypatch):
    from app.bot.handlers.client.start import client_brand, client_menu
    from app.config.settings import settings

    client = await owner(system)
    await base.post(
        client,
        "/master/branding",
        BrandingInput(
            brand_name="Barber House",
            welcome_text="Добро пожаловать",
            booking_cta_label="Выбрать время",
            show_portfolio=False,
        ).model_dump(),
        method="PUT",
    )
    async with system.factory() as session:
        bot = await session.get(base.BotInstance, system.bots[0].id)
        bot.web_booking_enabled = True
        await session.commit()
        brand = await client_brand(session, bot)
        keyboard = client_menu(False, brand)
        buttons = [b for row in keyboard.inline_keyboard for b in row]
        assert buttons[0].callback_data == "menu:book"
        assert buttons[0].web_app is None
        assert "Выбрать время" in buttons[0].text
        assert not any("Портфолио" in b.text for b in buttons)
    monkeypatch.setattr(settings, "web_booking_base_url", base.ORIGIN)
    web = await client.get(f"/api/web-booking/{system.bots[0].public_id}/context")
    assert web.status_code == 200, web.text
    assert web.json()["branding"]["brand_name"] == "Barber House"
    assert (await client.get("/api/miniapp/context")).json()["branding"][
        "brand_name"
    ] == "Barber House"
