"""Redis-backed, owner-fenced webhook update deduplication.

Redis is required for webhook processing. An unavailable dedup store must cause
Telegram to retry, rather than allowing parallel replicas to process an update.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
import secrets
from typing import AsyncIterator, Optional

import redis.asyncio as aioredis
from redis.exceptions import WatchError


class DedupUnavailableError(RuntimeError):
    """Redis could not establish or maintain a safe processing claim."""


class DedupState(str, Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"


@dataclass(frozen=True)
class UpdateClaim:
    key: str
    owner: str


@dataclass(frozen=True)
class AcquireResult:
    state: DedupState
    claim: Optional[UpdateClaim] = None


class UpdateDeduplicator:
    """One Redis claim per update; only its owner may renew or finish it."""

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis],
        ttl_completed: int = 86400,
        ttl_processing: int = 60,
    ) -> None:
        if ttl_processing < 3 or ttl_completed < 1:
            raise ValueError("Deduplication TTLs must be positive; processing TTL >= 3 seconds")
        self.redis = redis_client
        self.ttl_completed = ttl_completed
        self.ttl_processing = ttl_processing

    def _make_key(self, bot_instance_id: int, update_id: int) -> str:
        return f"telegram:update:{bot_instance_id}:{update_id}"

    def _make_manager_key(self, update_id: int) -> str:
        return f"manager:update:{update_id}"

    async def acquire(self, bot_instance_id: int, update_id: int) -> AcquireResult:
        return await self._acquire_key(self._make_key(bot_instance_id, update_id))

    async def acquire_manager(self, update_id: int) -> AcquireResult:
        return await self._acquire_key(self._make_manager_key(update_id))

    async def _acquire_key(self, key: str) -> AcquireResult:
        if self.redis is None:
            raise DedupUnavailableError("Redis deduplication is unavailable")
        claim = UpdateClaim(key=key, owner=f"PROCESSING:{secrets.token_hex(16)}")
        try:
            # A key may expire between SET NX and GET. Retry that narrow race.
            for _ in range(3):
                if await self.redis.set(key, claim.owner, nx=True, ex=self.ttl_processing):
                    return AcquireResult(DedupState.RECEIVED, claim)
                current = await self.redis.get(key)
                if current is None:
                    continue
                if current == b"COMPLETED" or current == "COMPLETED":
                    return AcquireResult(DedupState.COMPLETED)
                return AcquireResult(DedupState.PROCESSING)
        except Exception as exc:
            raise DedupUnavailableError("Redis deduplication failed") from exc
        raise DedupUnavailableError("Redis deduplication claim expired during acquisition")

    async def _owner_update(self, claim: UpdateClaim, action: str) -> bool:
        """Atomically compare owner token before SET, EXPIRE, or DELETE."""
        if self.redis is None:
            raise DedupUnavailableError("Redis deduplication is unavailable")
        for _ in range(5):
            try:
                async with self.redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(claim.key)
                    current = await pipe.get(claim.key)
                    if current not in (claim.owner, claim.owner.encode()):
                        return False
                    pipe.multi()
                    if action == "complete":
                        pipe.set(claim.key, "COMPLETED", ex=self.ttl_completed)
                    elif action == "renew":
                        pipe.expire(claim.key, self.ttl_processing)
                    elif action == "release":
                        pipe.delete(claim.key)
                    else:
                        raise ValueError(f"Unknown deduplication action: {action}")
                    await pipe.execute()
                    return True
            except WatchError:
                continue
            except Exception as exc:
                raise DedupUnavailableError("Redis deduplication state update failed") from exc
        raise DedupUnavailableError("Redis deduplication claim changed concurrently")

    async def complete(self, claim: UpdateClaim) -> None:
        if not await self._owner_update(claim, "complete"):
            raise DedupUnavailableError("Update processing claim was lost before completion")

    async def release(self, claim: UpdateClaim) -> None:
        await self._owner_update(claim, "release")

    @asynccontextmanager
    async def maintain(self, claim: UpdateClaim) -> AsyncIterator[None]:
        """Renew a live claim until the handler returns, then verify ownership."""
        lost = False

        async def renew_loop() -> None:
            nonlocal lost
            while True:
                await asyncio.sleep(max(1, self.ttl_processing // 3))
                try:
                    if not await self._owner_update(claim, "renew"):
                        lost = True
                        return
                except DedupUnavailableError:
                    lost = True
                    return

        task = asyncio.create_task(renew_loop())
        try:
            yield
            if lost:
                raise DedupUnavailableError("Update processing claim was lost")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
