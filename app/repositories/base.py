"""
Base generic repository implementation for SQLAlchemy 2.0 async sessions.
"""

from typing import Any, Generic, List, Optional, Sequence, Type, TypeVar
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Generic CRUD repository providing standard asynchronous database operations.
    """

    def __init__(self, model: Type[ModelType], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get_by_id(self, entity_id: Any) -> Optional[ModelType]:
        """
        Fetch entity by its primary key.
        """
        return await self.session.get(self.model, entity_id)

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[ModelType]:
        """
        List all records with pagination.
        """
        query = select(self.model).limit(limit).offset(offset)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def count(self) -> int:
        """
        Return the total number of records for the entity.
        """
        query = select(func.count()).select_from(self.model)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def create(self, **kwargs: Any) -> ModelType:
        """
        Create and persist a new entity instance.
        """
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def update(self, entity_id: Any, **kwargs: Any) -> Optional[ModelType]:
        """
        Update entity fields by ID.
        """
        instance = await self.get_by_id(entity_id)
        if not instance:
            return None
        for key, value in kwargs.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def delete(self, entity_id: Any) -> bool:
        """
        Delete an entity by its ID.
        """
        instance = await self.get_by_id(entity_id)
        if not instance:
            return False
        await self.session.delete(instance)
        await self.session.flush()
        return True
