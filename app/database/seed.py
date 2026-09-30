"""
Initial database seeding script.
Fills initial services, schedule templates, studio requisites, categories and admin records.
Idempotent and safe to run multiple times.
"""

import asyncio
from datetime import time
from decimal import Decimal
import logging
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.portfolio import PortfolioCategory
from app.database.models.schedule import ScheduleTemplate, ScheduleTemplateBreak
from app.database.models.service import DepositType, Service
from app.database.models.user import Admin, User, UserMarketingPreference
from app.database.session import async_session_maker, close_db, engine
from app.repositories.settings_repository import SettingsRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app.database.seed")


async def seed_settings(session: AsyncSession) -> None:
    """
    Populate default studio settings and payment requisites.
    """
    repo = SettingsRepository(session)
    default_settings = [
        ("studio_address", "г. Москва, ул. Арбат, д. 15, студия 204", "Фактический адрес студии"),
        ("studio_phone", "+7 (999) 111-22-33", "Контактный телефон студии"),
        ("bank_card_number", settings.bank_card_number, "Номер карты для предоплаты"),
        ("bank_name", settings.bank_name, "Банк получателя"),
        ("bank_recipient_name", settings.bank_recipient_name, "ФИО получателя перевода"),
        ("hold_duration_minutes", settings.hold_duration_minutes or 30, "Время удержания неоплаченного слота (мин)"),
        ("cancel_policy_hours", 24, "Срок бесплатной отмены (ч)"),
        ("timezone", settings.timezone or "Europe/Moscow", "Часовой пояс мастера"),
    ]

    for key, val, desc in default_settings:
        existing = await repo.get_value(key)
        if existing is None:
            await repo.set_value(key, val, description=desc)
            logger.info(f"Seeded setting: {key} = {val}")


async def seed_schedule_templates(session: AsyncSession, master_id: int = 1) -> None:
    """
    Populate default weekly schedule for master (Mon-Fri 10-19 with 13-14 lunch, Sat-Sun off).
    """
    query = select(ScheduleTemplate).where(ScheduleTemplate.master_id == master_id)
    res = await session.execute(query)
    existing_templates = res.scalars().all()

    if existing_templates:
        logger.info(f"Schedule templates already exist for master {master_id}, skipping.")
        return

    # Weekdays: 0 (Mon) to 6 (Sun)
    for day in range(7):
        is_weekend = day in [5, 6]
        template = ScheduleTemplate(
            master_id=master_id,
            day_of_week=day,
            is_day_off=is_weekend,
            work_start=time(10, 0),
            work_end=time(19, 0),
        )
        session.add(template)
        await session.flush()

        if not is_weekend:
            lunch = ScheduleTemplateBreak(
                template_id=template.id,
                break_start=time(13, 0),
                break_end=time(14, 0),
            )
            session.add(lunch)

    logger.info(f"Seeded standard weekly schedule templates for master {master_id}.")


async def seed_services(session: AsyncSession, master_id: int = 1) -> None:
    """
    Populate default beauty services catalog.
    """
    query = select(Service).where(Service.master_id == master_id)
    res = await session.execute(query)
    existing = res.scalars().all()

    if existing:
        logger.info(f"Services already exist for master {master_id}, skipping.")
        return

    services_data = [
        {
            "title": "Маникюр с покрытием гель-лак",
            "description": "Комбинированный или аппаратный маникюр, выравнивание ногтевой пластины базой, покрытие гель-лаком премиум-класса.",
            "price": Decimal("2500.00"),
            "duration_min": 90,
            "buffer_min": 15,
            "deposit_type": DepositType.FIXED,
            "deposit_value": Decimal("500.00"),
            "display_order": 1,
        },
        {
            "title": "Снятие + Маникюр + Дизайн",
            "description": "Бережное снятие старого материала, аппаратный маникюр, укрепление и трендовый дизайн ногтей (френч, градиент).",
            "price": Decimal("3200.00"),
            "duration_min": 120,
            "buffer_min": 15,
            "deposit_type": DepositType.FIXED,
            "deposit_value": Decimal("500.00"),
            "display_order": 2,
        },
        {
            "title": "SMART-педикюр с покрытием",
            "description": "Аппаратная SMART-обработка стопы и пальчиков с молекулярным маслом, стойкое покрытие гель-лаком.",
            "price": Decimal("3500.00"),
            "duration_min": 90,
            "buffer_min": 15,
            "deposit_type": DepositType.FIXED,
            "deposit_value": Decimal("1000.00"),
            "display_order": 3,
        },
        {
            "title": "Ламинирование ресниц + окрашивание",
            "description": "Глубокое питание, создание красивого завитка и насыщенного цвета ресниц с уходовым кератиновым ботоксом.",
            "price": Decimal("2800.00"),
            "duration_min": 60,
            "buffer_min": 15,
            "deposit_type": DepositType.FIXED,
            "deposit_value": Decimal("500.00"),
            "display_order": 4,
        },
        {
            "title": "Наращивание ресниц 2D",
            "description": "Бархатный двойной объём, невесомые ультратонкие ресницы без склеек и утяжеления взгляда.",
            "price": Decimal("3200.00"),
            "duration_min": 120,
            "buffer_min": 15,
            "deposit_type": DepositType.FIXED,
            "deposit_value": Decimal("500.00"),
            "display_order": 5,
        },
    ]

    for item in services_data:
        svc = Service(
            master_id=master_id,
            is_active=True,
            is_archived=False,
            **item,
        )
        session.add(svc)

    logger.info(f"Seeded {len(services_data)} default services for master {master_id}.")


async def seed_portfolio_categories(session: AsyncSession) -> None:
    """
    Populate default portfolio categories.
    """
    query = select(PortfolioCategory)
    res = await session.execute(query)
    existing = res.scalars().all()

    if existing:
        logger.info("Portfolio categories already exist, skipping.")
        return

    cats = [
        PortfolioCategory(title="Маникюр и дизайн", display_order=1, is_active=True),
        PortfolioCategory(title="SMART-педикюр", display_order=2, is_active=True),
        PortfolioCategory(title="Ресницы и брови", display_order=3, is_active=True),
    ]
    for c in cats:
        session.add(c)

    logger.info(f"Seeded {len(cats)} portfolio categories.")


async def seed_admins(session: AsyncSession) -> None:
    """
    Ensure admins configured in settings.admin_ids have User and Admin records.
    """
    for admin_tg_id in settings.admin_ids:
        query = select(User).where(User.telegram_id == admin_tg_id)
        res = await session.execute(query)
        user = res.scalars().first()

        if not user:
            user = User(
                telegram_id=admin_tg_id,
                first_name="Мастер-Администратор",
                username="admin",
            )
            session.add(user)
            await session.flush()
            pref = UserMarketingPreference(user_id=user.id, is_marketing_allowed=True)
            session.add(pref)
            await session.flush()

        admin_query = select(Admin).where(Admin.user_id == user.id)
        admin_res = await session.execute(admin_query)
        admin = admin_res.scalars().first()

        if not admin:
            admin = Admin(
                user_id=user.id,
                role="owner",
                is_active=True,
            )
            session.add(admin)
            logger.info(f"Granted owner admin privileges to user Telegram ID {admin_tg_id}.")


async def seed_database() -> None:
    """
    Execute all seeding steps in an isolated database transaction.
    """
    logger.info("Starting database seeding...")
    async with async_session_maker() as session:
        try:
            await seed_settings(session)
            await seed_schedule_templates(session, master_id=1)
            await seed_services(session, master_id=1)
            await seed_portfolio_categories(session)
            await seed_admins(session)
            await session.commit()
            logger.info("Database seeding completed successfully! 🎉")
        except Exception as e:
            await session.rollback()
            logger.error(f"Seeding failed: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    try:
        asyncio.run(seed_database())
    finally:
        asyncio.run(close_db())
