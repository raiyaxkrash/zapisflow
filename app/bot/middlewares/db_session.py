"""
Database session middleware providing an active AsyncSession to event handlers.
"""

import hashlib
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy import text

from app.database.models.processed_update import ProcessedWebhookUpdate
from app.database.session import async_session_factory


class DbSessionMiddleware(BaseMiddleware):
    """
    Middleware creating and committing an async database session per update.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            data["session"] = session
            try:
                scope = data.get("webhook_update_scope")
                update_id = event.update_id if isinstance(event, Update) else None
                if scope is not None and update_id is not None:
                    # Serialize the same update across replicas in PostgreSQL.
                    # The lock and the completion row are in this transaction.
                    lock_bytes = hashlib.blake2b(
                        f"{scope}:{update_id}".encode("utf-8"), digest_size=8
                    ).digest()
                    lock_key = int.from_bytes(lock_bytes, byteorder="big", signed=True)
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {"lock_key": lock_key},
                    )
                    if await session.get(ProcessedWebhookUpdate, (scope, update_id)):
                        return None

                result = await handler(event, data)
                if scope is not None and update_id is not None:
                    session.add(ProcessedWebhookUpdate(scope=scope, update_id=update_id))
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
