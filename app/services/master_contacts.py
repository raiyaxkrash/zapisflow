"""Tenant-scoped contact editing and shared customer/preview presentation."""

import html
import re
from types import SimpleNamespace
from urllib.parse import urlencode

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client import MenuCallback
from app.database.models.master import MasterSettings
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.services.master_authorization_service import MasterAuthorizationService


CONTACT_FIELD_LABELS = {
    "studio_phone": "📱 Телефон",
    "whatsapp_phone": "💬 WhatsApp",
    "studio_address": "📍 Адрес",
    "working_hours_text": "🕒 Режим работы",
    "contacts_intro_text": "📝 Текст для клиентов",
    "telegram_username": "✈️ Telegram",
}
CONTACT_FIELD_LIMITS = {
    "studio_phone": 64,
    "whatsapp_phone": 64,
    "studio_address": 500,
    "working_hours_text": 300,
    "contacts_intro_text": 1000,
    "telegram_username": 64,
}
DEFAULT_INTRO = (
    "Если у вас есть вопросы по записи, индивидуальным дизайнам или вы хотите "
    "перенести визит — свяжитесь со мной удобным способом:"
)
_PHONE_PATTERN = re.compile(r"^\+?[0-9 ()\-]+$")
_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def contact_phone_e164(value: str | None) -> str | None:
    """Return a safe international phone number for Telegram and wa.me."""
    if not value or not _PHONE_PATTERN.fullmatch(value.strip()):
        return None
    digits = re.sub(r"\D", "", value)
    if not 10 <= len(digits) <= 15:
        return None
    return f"+{digits}"


def _safe_address(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value or len(value) > CONTACT_FIELD_LIMITS["studio_address"]:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _telegram_username(value: str | None) -> str | None:
    username = (value or "").strip().lstrip("@")
    return username if _USERNAME_PATTERN.fullmatch(username) else None


def normalize_contact_value(field: str, raw: str | None) -> str | None:
    """Validate an individual field. Empty input intentionally clears it."""
    if field not in CONTACT_FIELD_LABELS:
        raise ValueError("Неизвестное поле контактов")
    value = (raw or "").strip()
    if not value or value == "/clear":
        return None
    if len(value) > CONTACT_FIELD_LIMITS[field]:
        raise ValueError(f"Слишком длинное значение (максимум {CONTACT_FIELD_LIMITS[field]} символов)")
    if any((ord(char) < 32 and char != "\n") or ord(char) == 127 for char in value):
        raise ValueError("Уберите управляющие символы из текста")
    if field in ("studio_phone", "whatsapp_phone"):
        if not contact_phone_e164(value):
            raise ValueError("Введите номер в международном формате, например +7 999 000-00-00")
    elif field == "telegram_username":
        username = _telegram_username(value)
        if not username or value not in (username, f"@{username}"):
            raise ValueError("Введите Telegram username, например @master_name")
        value = username
    elif field == "studio_address" and not _safe_address(value):
        raise ValueError("Введите адрес без переносов строк")
    return value


class MasterContactsService:
    """Contact mutations are authorized and committed by the outer transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = MasterSettingsRepository(session)

    async def get(self, master_id: int) -> MasterSettings | None:
        return await self.repository.get_by_master_id(master_id)

    async def update_field(
        self, master_id: int, actor_user_id: int, field: str, raw: str | None
    ) -> MasterSettings:
        await MasterAuthorizationService(self.session).require_admin(master_id, actor_user_id)
        value = normalize_contact_value(field, raw)
        current = await self.repository.get_by_master_id(master_id)
        candidate = SimpleNamespace(**{
            key: (value if key == field else getattr(current, key, None))
            for key in CONTACT_FIELD_LABELS
        })
        if len(render_contacts(candidate)[0]) > 4000:
            raise ValueError("Контактный блок слишком длинный. Сократите текст.")
        return await self.repository.update_settings(master_id, **{field: value})


def render_contacts(settings: MasterSettings | None) -> tuple[str, InlineKeyboardMarkup]:
    """The exact customer card, also used by the admin preview."""
    get = lambda field: (getattr(settings, field, None) or "").strip()
    intro = get("contacts_intro_text") or DEFAULT_INTRO
    lines = ["<b>📞 Контакты мастера</b>", "", html.escape(intro), ""]
    phone = get("studio_phone")
    whatsapp = get("whatsapp_phone")
    address = get("studio_address")
    hours = get("working_hours_text")
    username = _telegram_username(get("telegram_username"))
    if phone:
        lines.append(f"📱 <b>Телефон:</b> {html.escape(phone)}")
    if whatsapp:
        lines.append(f"💬 <b>WhatsApp:</b> {html.escape(whatsapp)}")
    if address:
        lines.append(f"📍 <b>Адрес студии:</b> {html.escape(address)}")
    if hours:
        lines.append(f"🕒 <b>Режим работы:</b> {html.escape(hours)}")
    if username:
        lines.append(f"✈️ <b>Telegram:</b> @{html.escape(username)}")
    buttons: list[list[InlineKeyboardButton]] = []
    if contact_phone_e164(phone):
        # Telegram inline URL buttons accept HTTP(S)/tg links, not tel: links.
        # A contact card exposes the platform's native call action.
        buttons.append([InlineKeyboardButton(text="📞 Позвонить", callback_data="contact:call")])
    whatsapp_number = contact_phone_e164(whatsapp)
    if whatsapp_number:
        buttons.append([
            InlineKeyboardButton(
                text="💬 Написать в WhatsApp",
                url=f"https://wa.me/{whatsapp_number[1:]}",
            )
        ])
    if username:
        buttons.append([
            InlineKeyboardButton(text="✈️ Написать в Telegram", url=f"https://t.me/{username}")
        ])
    safe_address = _safe_address(address)
    if safe_address:
        map_url = "https://www.google.com/maps/search/?" + urlencode({"api": "1", "query": safe_address})
        if len(map_url) <= 2000:
            buttons.append([InlineKeyboardButton(text="📍 Открыть адрес", url=map_url)])
    buttons.append([
        InlineKeyboardButton(text="🏠 Главное меню", callback_data=MenuCallback(action="main").pack())
    ])
    return "\n".join(lines).rstrip(), InlineKeyboardMarkup(inline_keyboard=buttons)
