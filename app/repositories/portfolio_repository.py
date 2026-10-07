"""Tenant-scoped Portfolio repository for categories and work showcase items.

Guarantees that categories and portfolio works are strictly isolated per master_id
and protects against cross-tenant IDOR access.
"""

from typing import Any, Optional, Sequence
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.portfolio import PortfolioCategory, PortfolioItem


class PortfolioRepository:
    """Repository managing portfolio categories and showcase images per master."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_categories(
        self, master_id: int, active_only: bool = True
    ) -> Sequence[PortfolioCategory]:
        """List categories belonging strictly to the specified master."""
        query = select(PortfolioCategory).where(PortfolioCategory.master_id == master_id)
        if active_only:
            query = query.where(PortfolioCategory.is_active.is_(True))
        query = query.order_by(PortfolioCategory.display_order.asc(), PortfolioCategory.id.asc())
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_category_by_id(
        self, category_id: int, master_id: int
    ) -> Optional[PortfolioCategory]:
        """Fetch category ensuring it belongs to the given master."""
        query = select(PortfolioCategory).where(
            PortfolioCategory.id == category_id,
            PortfolioCategory.master_id == master_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def list_items(
        self, category_id: int, master_id: int, active_only: bool = True
    ) -> Sequence[PortfolioItem]:
        """Fetch items within category verifying category ownership by master."""
        category = await self.get_category_by_id(category_id, master_id)
        if not category:
            return []
        query = select(PortfolioItem).where(PortfolioItem.category_id == category_id)
        if active_only:
            query = query.where(PortfolioItem.is_active.is_(True))
        query = query.order_by(PortfolioItem.display_order.asc(), PortfolioItem.id.asc())
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_item_by_id(self, item_id: int, master_id: int) -> Optional[PortfolioItem]:
        """Fetch a portfolio item verifying tenant ownership via parent category."""
        query = (
            select(PortfolioItem)
            .join(PortfolioCategory, PortfolioItem.category_id == PortfolioCategory.id)
            .where(
                PortfolioItem.id == item_id,
                PortfolioCategory.master_id == master_id,
            )
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def create_category(
        self, master_id: int, title: str, display_order: int = 0
    ) -> PortfolioCategory:
        """Create a new portfolio category for master."""
        category = PortfolioCategory(
            master_id=master_id,
            title=title,
            display_order=display_order,
            is_active=True,
        )
        self.session.add(category)
        await self.session.flush()
        await self.session.refresh(category)
        return category

    async def add_item(
        self,
        master_id: int,
        category_id: int,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        caption: Optional[str] = None,
        title: Optional[str] = None,
        service_id: Optional[int] = None,
        display_order: int = 0,
        is_active: bool = True,
    ) -> Optional[PortfolioItem]:
        """Add a showcase item to category ensuring tenant ownership."""
        category = await self.get_category_by_id(category_id, master_id)
        if not category:
            return None
        item = PortfolioItem(
            master_id=master_id,
            category_id=category_id,
            service_id=service_id,
            title=title,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            caption=caption,
            display_order=display_order,
            is_active=is_active,
        )
        self.session.add(item)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def update_item(
        self,
        item_id: int,
        master_id: int,
        **kwargs: Any,
    ) -> Optional[PortfolioItem]:
        """Update portfolio item ensuring tenant ownership."""
        item = await self.get_item_by_id(item_id, master_id)
        if not item:
            return None
        for key, value in kwargs.items():
            if hasattr(item, key) and key not in {"id", "category_id"}:
                setattr(item, key, value)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def delete_item(self, item_id: int, master_id: int) -> bool:
        """Delete an item ensuring it belongs to this master."""
        item = await self.get_item_by_id(item_id, master_id)
        if not item:
            return False
        await self.session.delete(item)
        await self.session.flush()
        return True

    async def delete_category(self, category_id: int, master_id: int) -> bool:
        """Delete category ensuring it belongs to this master."""
        category = await self.get_category_by_id(category_id, master_id)
        if not category:
            return False
        await self.session.delete(category)
        await self.session.flush()
        return True
