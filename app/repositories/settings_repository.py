"""
Settings repository for dynamic application configurations.
"""

from typing import Any, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.setting import AppSetting
from app.repositories.base import BaseRepository


class SettingsRepository(BaseRepository[AppSetting]):
    """
    Repository for managing dynamic system configuration stored as JSON.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AppSetting, session)

    async def get_value(self, key: str, default: Any = None) -> Any:
        """
        Get value of a setting by its key, or return default if not configured.
        """
        setting = await self.get_by_id(key)
        if not setting:
            return default
        return setting.value.get("value", default) if isinstance(setting.value, dict) else setting.value

    async def set_value(
        self, key: str, value: Any, description: Optional[str] = None
    ) -> AppSetting:
        """
        Set or update setting key with json value.
        """
        setting = await self.get_by_id(key)
        val_dict = {"value": value}
        if not setting:
            setting = AppSetting(key=key, value=val_dict, description=description)
            self.session.add(setting)
        else:
            setting.value = val_dict
            if description:
                setting.description = description
        await self.session.flush()
        await self.session.refresh(setting)
        return setting

    async def get_all_settings(self) -> Dict[str, Any]:
        """
        Return all settings as a flat dictionary.
        """
        query = select(AppSetting)
        result = await self.session.execute(query)
        settings_list = result.scalars().all()
        data = {}
        for s in settings_list:
            data[s.key] = s.value.get("value") if isinstance(s.value, dict) else s.value
        return data
