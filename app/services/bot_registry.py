"""Central BotRegistry for dynamic tenant bot instances.

Handles:
- Status verification & policy enforcement
- Decrypting encrypted tokens on demand via TokenCrypto
- CachedBot runtime pool with LRU bounds, TTL expiration, and graceful session closing
- Local and Redis Pub/Sub invalidation bus
- Dynamic token rotation without process restarts
"""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import json
import logging
import time
from typing import Optional, Union

from aiogram import Bot
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.security import mask_token
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstance, BotInstanceStatus
from app.database.session import async_session_factory
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.services.bot_factory import BotFactory
from app.services.exceptions import (
    BotDisabledError,
    BotNotFoundError,
    BotProvisioningError,
    BotRegistryError,
    BotSetupRequiredError,
    BotUnavailableError,
)

logger = logging.getLogger("app.services.bot_registry")


@dataclass
class CachedBotInstance:
    """Cached aiogram Bot runtime object with metadata."""

    bot_instance_id: int
    telegram_bot_id: int
    master_id: int
    token_version: int
    created_at: float
    expires_at: float
    bot: Bot


class BotRegistry:
    """Dynamic tenant bot registry and runtime pool."""

    def __init__(
        self,
        token_crypto: Optional[TokenCrypto] = None,
        max_size: Optional[int] = None,
        ttl_seconds: Optional[int] = None,
        redis_client: Optional[Redis] = None,
        invalidation_channel: Optional[str] = None,
    ) -> None:
        self._token_crypto = token_crypto or TokenCrypto()
        self.max_size = (
            max_size if max_size is not None else settings.bot_registry_cache_max_size
        )
        self.ttl_seconds = (
            ttl_seconds if ttl_seconds is not None else settings.bot_registry_cache_ttl_seconds
        )
        self.invalidation_channel = (
            invalidation_channel
            if invalidation_channel is not None
            else settings.bot_registry_invalidation_channel
        )
        self._redis_client = redis_client

        # LRU cache: bot_instance_id -> CachedBotInstance
        self._cache: OrderedDict[int, CachedBotInstance] = OrderedDict()
        self._telegram_bot_id_to_instance_id: dict[int, int] = {}
        self._master_id_to_instance_id: dict[int, int] = {}

        self._lock = asyncio.Lock()
        self._listener_task: Optional[asyncio.Task] = None
        self._pubsub = None

    def _validate_status_policy(self, instance: BotInstance) -> None:
        """Validate whether a BotInstance is permitted to obtain an aiogram.Bot runtime object.

        ACTIVE and SETUP_REQUIRED are permitted (SETUP_REQUIRED allows the master to run /admin setup).
        DISABLED, ERROR, and unfinished PROVISIONING are rejected.
        """
        if instance.status == BotInstanceStatus.DISABLED:
            raise BotDisabledError(f"BotInstance #{instance.id} is DISABLED")
        if instance.status == BotInstanceStatus.ERROR:
            err = instance.last_error or "Unknown error"
            raise BotUnavailableError(f"BotInstance #{instance.id} is in ERROR status: {err}")
        if instance.status == BotInstanceStatus.PROVISIONING:
            raise BotProvisioningError(f"BotInstance #{instance.id} is still PROVISIONING")
        if not instance.is_current:
            raise BotUnavailableError(f"BotInstance #{instance.id} is no longer current")
        if instance.status not in (BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED):
            raise BotUnavailableError(f"BotInstance #{instance.id} status is {instance.status}")

    async def _close_bot_session(self, bot: Bot) -> None:
        """Safely close network HTTP session of a bot instance."""
        try:
            if bot.session:
                await bot.session.close()
        except Exception as e:
            logger.warning("Error while closing bot session: %s", e)

    async def _evict_oldest_if_needed(self) -> None:
        """Evict least recently used (LRU) bot when cache size limit is reached."""
        while len(self._cache) >= self.max_size:
            oldest_id, oldest_entry = self._cache.popitem(last=False)
            self._telegram_bot_id_to_instance_id.pop(oldest_entry.telegram_bot_id, None)
            self._master_id_to_instance_id.pop(oldest_entry.master_id, None)
            logger.info(
                "Evicted BotInstance #%s (bot_id=%s) from cache due to capacity limit",
                oldest_id,
                oldest_entry.telegram_bot_id,
            )
            await self._close_bot_session(oldest_entry.bot)

    async def get_by_instance_id(
        self,
        bot_instance_id: int,
        session: Optional[AsyncSession] = None,
        expected_token_version: Optional[int] = None,
    ) -> Bot:
        """Get a bot, rechecking PostgreSQL after TTL or a known version change.

        Webhook ingress has already loaded the BotInstance from PostgreSQL and
        supplies its token version. This prevents a missed Pub/Sub message from
        serving a rotated token on the next incoming update.
        """
        now = time.time()

        # 1. Fast read path: Check cache
        async with self._lock:
            cached = self._cache.get(bot_instance_id)
            if (
                cached
                and now < cached.expires_at
                and (
                    expected_token_version is None
                    or cached.token_version == expected_token_version
                )
            ):
                self._cache.move_to_end(bot_instance_id)
                return cached.bot

        # 2. Database lookup required (cache miss or TTL expired)
        if session is not None:
            return await self._resolve_bot(bot_instance_id, session, now)

        async with async_session_factory() as local_session:
            return await self._resolve_bot(bot_instance_id, local_session, now)

    async def _resolve_bot(
        self,
        bot_instance_id: int,
        session: AsyncSession,
        now: float,
    ) -> Bot:
        """Resolve, validate, decrypt and cache bot instance."""
        repo = BotInstanceRepository(session)
        instance = await repo.get_by_id(bot_instance_id)
        if not instance:
            raise BotNotFoundError(f"BotInstance #{bot_instance_id} not found")

        async with self._lock:
            cached = self._cache.get(bot_instance_id)

            # If cached entry exists but TTL expired, check if metadata changed
            if cached:
                if (
                    instance.status in (BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED)
                    and instance.is_current
                    and instance.token_version == cached.token_version
                    and instance.master_id == cached.master_id
                    and (instance.telegram_bot_id or 0) == cached.telegram_bot_id
                ):
                    # Metadata unchanged: refresh TTL and return cached bot
                    cached.expires_at = now + self.ttl_seconds
                    self._cache.move_to_end(bot_instance_id)
                    return cached.bot

                # Status changed or token rotated: remove old bot
                await self._evict_entry(bot_instance_id, reason="metadata_changed")

            # Validate operational status
            self._validate_status_policy(instance)

            if not instance.encrypted_token:
                raise BotUnavailableError(
                    f"BotInstance #{bot_instance_id} has no encrypted token configured"
                )

            # Decrypt token with AAD tied to telegram_bot_id
            aad = instance.telegram_bot_id
            plaintext_token = self._token_crypto.decrypt(
                instance.encrypted_token, associated_data=aad
            )

            # Construct new Bot without side effects
            bot = BotFactory.create(plaintext_token)

            # Evict LRU if capacity exceeded
            await self._evict_oldest_if_needed()

            # Cache new instance
            entry = CachedBotInstance(
                bot_instance_id=instance.id,
                telegram_bot_id=instance.telegram_bot_id or 0,
                master_id=instance.master_id,
                token_version=instance.token_version,
                created_at=now,
                expires_at=now + self.ttl_seconds,
                bot=bot,
            )
            self._cache[instance.id] = entry
            if instance.telegram_bot_id:
                self._telegram_bot_id_to_instance_id[instance.telegram_bot_id] = instance.id
            self._master_id_to_instance_id[instance.master_id] = instance.id

            logger.info(
                "Cached active BotInstance #%s (bot_id=%s, version=%s, preview=%s)",
                instance.id,
                instance.telegram_bot_id,
                instance.token_version,
                mask_token(plaintext_token),
            )
            return bot

    async def get_by_telegram_bot_id(
        self,
        telegram_bot_id: int,
        session: Optional[AsyncSession] = None,
    ) -> Bot:
        """Resolve Bot by Telegram ID using current PostgreSQL metadata."""
        if session is not None:
            repo = BotInstanceRepository(session)
            instance = await repo.get_by_telegram_bot_id(telegram_bot_id)
            if not instance:
                raise BotNotFoundError(f"Bot with telegram_bot_id {telegram_bot_id} not found")
            self._validate_status_policy(instance)
            return await self.get_by_instance_id(
                instance.id, session=session, expected_token_version=instance.token_version
            )

        async with async_session_factory() as local_session:
            repo = BotInstanceRepository(local_session)
            instance = await repo.get_by_telegram_bot_id(telegram_bot_id)
            if not instance:
                raise BotNotFoundError(f"Bot with telegram_bot_id {telegram_bot_id} not found")
            self._validate_status_policy(instance)
            return await self.get_by_instance_id(
                instance.id, session=local_session, expected_token_version=instance.token_version
            )

    async def get_by_master_id(
        self,
        master_id: int,
        session: Optional[AsyncSession] = None,
    ) -> Bot:
        """Resolve the current active bot from PostgreSQL before any cache lookup.

        Scheduler sends must not use a former bot after disable or reconnect,
        even if this replica missed the Pub/Sub invalidation event.
        """
        if session is not None:
            repo = BotInstanceRepository(session)
            instance = await repo.get_active_by_master_id(master_id)
            if not instance:
                raise BotNotFoundError(f"Active bot instance for master #{master_id} not found")
            return await self.get_by_instance_id(
                instance.id, session=session, expected_token_version=instance.token_version
            )

        async with async_session_factory() as local_session:
            repo = BotInstanceRepository(local_session)
            instance = await repo.get_active_by_master_id(master_id)
            if not instance:
                raise BotNotFoundError(f"Active bot instance for master #{master_id} not found")
            return await self.get_by_instance_id(
                instance.id, session=local_session, expected_token_version=instance.token_version
            )

    async def _evict_entry(self, bot_instance_id: int, reason: str = "manual") -> bool:
        """Internal eviction helper under lock."""
        entry = self._cache.pop(bot_instance_id, None)
        if not entry:
            return False
        self._telegram_bot_id_to_instance_id.pop(entry.telegram_bot_id, None)
        self._master_id_to_instance_id.pop(entry.master_id, None)
        logger.info(
            "Evicted BotInstance #%s from registry cache (reason: %s)",
            bot_instance_id,
            reason,
        )
        await self._close_bot_session(entry.bot)
        return True

    async def invalidate_bot_instance(
        self, bot_instance_id: int, reason: str = "manual"
    ) -> bool:
        """Evict a specific bot instance from local cache and close its HTTP session."""
        async with self._lock:
            return await self._evict_entry(bot_instance_id, reason=reason)

    async def invalidate_master(self, master_id: int, reason: str = "manual") -> None:
        """Evict all cached bot instances belonging to a master."""
        async with self._lock:
            instance_id = self._master_id_to_instance_id.get(master_id)
            if instance_id:
                await self._evict_entry(instance_id, reason=reason)

    async def publish_invalidation(
        self,
        bot_instance_id: int,
        token_version: Optional[int] = None,
        reason: str = "token_rotated",
    ) -> None:
        """Locally invalidate bot and broadcast event across replicas via Redis Pub/Sub."""
        # 1. Local invalidation
        await self.invalidate_bot_instance(bot_instance_id, reason=reason)

        # 2. Remote invalidation broadcast
        if self._redis_client is not None:
            try:
                payload = json.dumps(
                    {
                        "bot_instance_id": bot_instance_id,
                        "token_version": token_version,
                        "reason": reason,
                    }
                )
                await self._redis_client.publish(self.invalidation_channel, payload)
                logger.info(
                    "Published invalidation event for BotInstance #%s to channel '%s'",
                    bot_instance_id,
                    self.invalidation_channel,
                )
            except Exception as e:
                logger.warning(
                    "Failed to broadcast invalidation via Redis Pub/Sub (%s). DB remains source of truth.",
                    e,
                )

    async def invalidate_bot(
        self,
        bot_instance_id: int,
        token_version: Optional[int] = None,
        reason: str = "manual",
    ) -> None:
        """Alias for publish_invalidation to ensure local eviction, session closing, and Redis Pub/Sub broadcast."""
        await self.publish_invalidation(
            bot_instance_id=bot_instance_id,
            token_version=token_version,
            reason=reason,
        )

    async def start_invalidation_listener(self) -> None:
        """Start background task listening to Redis Pub/Sub invalidation events."""
        if self._redis_client is None:
            logger.info("Redis client not configured for BotRegistry; Pub/Sub invalidation listener skipped")
            return

        if self._listener_task and not self._listener_task.done():
            return

        self._listener_task = asyncio.create_task(self._listen_invalidation_loop())
        logger.info("Started Redis Pub/Sub invalidation listener on channel '%s'", self.invalidation_channel)

    async def _listen_invalidation_loop(self) -> None:
        """Reconnect after Redis outages; PostgreSQL and TTL remain the fallback."""
        while True:
            try:
                self._pubsub = self._redis_client.pubsub()
                await self._pubsub.subscribe(self.invalidation_channel)

                async for message in self._pubsub.listen():
                    if message and message.get("type") == "message":
                        try:
                            data = json.loads(message["data"])
                            bot_instance_id = data.get("bot_instance_id")
                            reason = data.get("reason", "pubsub_invalidation")
                            if bot_instance_id:
                                logger.info(
                                    "Received remote invalidation for BotInstance #%s (reason=%s)",
                                    bot_instance_id,
                                    reason,
                                )
                                await self.invalidate_bot_instance(bot_instance_id, reason=f"remote:{reason}")
                        except Exception as err:
                            logger.warning("Error parsing invalidation message: %s", err)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Invalidation listener disconnected; retrying: %s", exc)
            finally:
                if self._pubsub:
                    try:
                        await self._pubsub.unsubscribe(self.invalidation_channel)
                        await self._pubsub.aclose()
                    except Exception:
                        pass
                    self._pubsub = None
            await asyncio.sleep(1)

    async def close(self) -> None:
        """Gracefully shutdown BotRegistry: stop listener and close all cached bot sessions."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        async with self._lock:
            for instance_id, entry in list(self._cache.items()):
                await self._close_bot_session(entry.bot)
            self._cache.clear()
            self._telegram_bot_id_to_instance_id.clear()
            self._master_id_to_instance_id.clear()
            logger.info("BotRegistry successfully closed and all bot sessions released")
