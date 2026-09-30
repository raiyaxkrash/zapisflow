"""Redis-backed two-phase atomic update deduplication service for Telegram webhooks.

Prevents duplicate delivery processing while allowing safe retries on handler crashes.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging
from typing import Optional
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class UpdateDeduplicator:
    """Manages update idempotency locks and completion status in Redis."""

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis],
        ttl_completed: int = 86400,
        ttl_processing: int = 60,
    ) -> None:
        self.redis = redis_client
        self.ttl_completed = ttl_completed
        self.ttl_processing = ttl_processing

    def _make_key(self, bot_instance_id: int, update_id: int) -> str:
        return f"telegram:update:{bot_instance_id}:{update_id}"

    async def should_process(self, bot_instance_id: int, update_id: int) -> bool:
        """
        Atomically attempts to acquire the processing lock for an update.
        Returns True if this is the first arrival and should be processed.
        Returns False if update is already PROCESSING or COMPLETED (duplicate).
        """
        if not self.redis:
            return True
        key = self._make_key(bot_instance_id, update_id)
        try:
            acquired = await self.redis.set(
                key, "PROCESSING", nx=True, ex=self.ttl_processing
            )
            return bool(acquired)
        except Exception as e:
            logger.warning(
                "Redis error during deduplication check for bot %s update %s: %s. Allowing processing.",
                bot_instance_id,
                update_id,
                e,
            )
            return True

    async def mark_completed(self, bot_instance_id: int, update_id: int) -> None:
        """Marks update as completed with long retention TTL."""
        if not self.redis:
            return
        key = self._make_key(bot_instance_id, update_id)
        try:
            await self.redis.set(key, "COMPLETED", ex=self.ttl_completed)
        except Exception as e:
            logger.warning(
                "Redis error during deduplication completion for bot %s update %s: %s",
                bot_instance_id,
                update_id,
                e,
            )

    async def release_lock(self, bot_instance_id: int, update_id: int) -> None:
        """Deletes lock on processing failure, allowing Telegram retry."""
        if not self.redis:
            return
        key = self._make_key(bot_instance_id, update_id)
        try:
            await self.redis.delete(key)
        except Exception as e:
            logger.warning(
                "Redis error during deduplication lock release for bot %s update %s: %s",
                bot_instance_id,
                update_id,
                e,
            )

    @asynccontextmanager
    async def process_context(
        self, bot_instance_id: int, update_id: int
    ) -> AsyncGenerator[bool, None]:
        """
        Context manager for end-to-end deduplication lifecycle:
        Yields True if update should be processed, False if duplicate.
        If processed successfully, marks COMPLETED.
        If an exception is raised, releases lock so Telegram can retry.
        """
        can_process = await self.should_process(bot_instance_id, update_id)
        if not can_process:
            yield False
            return

        try:
            yield True
            await self.mark_completed(bot_instance_id, update_id)
        except Exception:
            await self.release_lock(bot_instance_id, update_id)
            raise
