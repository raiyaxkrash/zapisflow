"""Tenant-scoped MasterSettings repository.

Manages studio requisites, booking horizons, cancel policies and notifications
specifically per master_id.
"""

from typing import Any, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.master import MasterSettings


class MasterSettingsRepository:
    """Repository for managing MasterSettings strictly scoped to a master_id."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_master_id(self, master_id: int) -> Optional[MasterSettings]:
        """Fetch settings for a specific master."""
        query = select(MasterSettings).where(MasterSettings.master_id == master_id)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_or_create(self, master_id: int) -> MasterSettings:
        """Fetch settings for a master, or create defaults if not existing yet."""
        settings = await self.get_by_master_id(master_id)
        if not settings:
            settings = MasterSettings(master_id=master_id)
            self.session.add(settings)
            await self.session.flush()
            await self.session.refresh(settings)
        return settings

    async def update_settings(self, master_id: int, **kwargs: Any) -> MasterSettings:
        """Update specific settings attributes for a master."""
        settings = await self.get_or_create(master_id)
        for key, value in kwargs.items():
            if hasattr(settings, key) and key != "master_id":
                setattr(settings, key, value)
        await self.session.flush()
        await self.session.refresh(settings)
        return settings

    async def get_value(self, master_id: int, key: str, default: Any = None) -> Any:
        """Read a single configuration property for a master with fallback."""
        if key == "timezone":
            from app.database.models.master import Master
            master = await self.session.get(Master, master_id)
            if master and master.timezone:
                return master.timezone
            return default
        if key in ("master_name", "display_name"):
            from app.database.models.master import Master
            master = await self.session.get(Master, master_id)
            if master and master.display_name:
                return master.display_name
            return default
        settings = await self.get_by_master_id(master_id)
        if not settings:
            return default
        val = getattr(settings, key, None)
        return val if val is not None else default

    async def get_all_settings_dict(self, master_id: int) -> Dict[str, Any]:
        """Return master settings as a dictionary representation."""
        settings = await self.get_or_create(master_id)
        return {
            "master_id": settings.master_id,
            "bank_name": settings.bank_name,
            "bank_card_number": settings.bank_card_number,
            "bank_recipient_name": settings.bank_recipient_name,
            "studio_address": settings.studio_address,
            "studio_phone": settings.studio_phone,
            "whatsapp_phone": settings.whatsapp_phone,
            "working_hours_text": settings.working_hours_text,
            "contacts_intro_text": settings.contacts_intro_text,
            "telegram_username": settings.telegram_username,
            "vk_profile": settings.vk_profile,
            "about_text": settings.about_text,
            "hold_duration_minutes": settings.hold_duration_minutes,
            "cancel_policy_hours": settings.cancel_policy_hours,
            "booking_horizon_days": settings.booking_horizon_days,
            "min_advance_hours": settings.min_advance_hours,
            "grid_step_minutes": settings.grid_step_minutes,
            "default_buffer_minutes": settings.default_buffer_minutes,
            "reminder_24h_enabled": settings.reminder_24h_enabled,
            "reminder_3h_enabled": settings.reminder_3h_enabled,
        }
