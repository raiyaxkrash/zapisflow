"""Service for evaluating Master onboarding checklist and business readiness."""

from typing import List, Tuple
from zoneinfo import ZoneInfo
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.master import Master, MasterSettings
from app.database.models.schedule import ScheduleTemplate
from app.database.models.service import Service


class MasterReadinessService:
    """Evaluates whether a Master is fully configured and ready to be ACTIVATED."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check(self, master_id: int) -> Tuple[bool, List[str]]:
        """Check all mandatory readiness conditions for Master activation.

        Returns (is_ready, missing_items).
        """
        missing_items: List[str] = []

        # 1. Check Master profile
        master = await self.session.get(Master, master_id)
        if not master:
            return False, ["Мастер не найден в системе"]

        if not master.display_name or not master.display_name.strip():
            missing_items.append("Укажите название салона или имя мастера")

        if master.timezone:
            try:
                ZoneInfo(master.timezone)
            except Exception:
                missing_items.append(f"Некорректный часовой пояс ({master.timezone})")
        else:
            missing_items.append("Укажите часовой пояс")

        # 2. Check MasterSettings
        settings = await self.session.get(MasterSettings, master_id)
        if not settings:
            missing_items.append("Создайте основные настройки мастера")

        # 3. Check Services (at least 1 active service required)
        stmt_services = select(func.count(Service.id)).where(
            Service.master_id == master_id,
            Service.is_active == True,  # noqa: E712
        )
        services_count = (await self.session.execute(stmt_services)).scalar() or 0
        if services_count < 1:
            missing_items.append("Добавьте хотя бы одну активную услугу")

        # 4. Check Schedule (at least 1 working day template required)
        stmt_schedule = select(func.count(ScheduleTemplate.id)).where(
            ScheduleTemplate.master_id == master_id,
            ScheduleTemplate.is_day_off == False,  # noqa: E712
        )
        working_days_count = (await self.session.execute(stmt_schedule)).scalar() or 0
        if working_days_count < 1:
            missing_items.append("Настройте рабочие дни в расписании")

        # 5. Check Requisites if deposit is required
        # Check if any active service has a required deposit
        stmt_deposit_services = select(func.count(Service.id)).where(
            Service.master_id == master_id,
            Service.is_active == True,  # noqa: E712
            Service.deposit_value > 0,
        )
        deposit_services_count = (await self.session.execute(stmt_deposit_services)).scalar() or 0
        if deposit_services_count > 0:
            if not settings or not (settings.bank_card_number and settings.bank_card_number.strip()):
                missing_items.append("Укажите номер карты/счета для приёма предоплаты")

        is_ready = len(missing_items) == 0
        return is_ready, missing_items
