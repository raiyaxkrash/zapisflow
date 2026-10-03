"""Rate limiting utilities for critical actions, API calls, and anti-spam protection."""

import time
from typing import Optional
from redis.asyncio import Redis

# In-memory fallback if Redis client is not available or disconnected
_memory_cache: dict[str, float] = {}


async def check_rate_limit(
    redis_client: Optional[Redis],
    key: str,
    cooldown_seconds: int = 5,
) -> bool:
    """Return True if action is allowed within rate limit, False if throttled.

    Uses Redis atomic SET NX with TTL when Redis is provided, otherwise falls back
    to in-memory monotonic timestamp dict with automated pruning.
    """
    if redis_client is not None:
        try:
            ok = await redis_client.set(key, "1", ex=cooldown_seconds, nx=True)
            return bool(ok)
        except Exception:
            # Degrade gracefully to in-memory fallback
            pass

    now = time.monotonic()
    last = _memory_cache.get(key)
    if last is not None and now - last < cooldown_seconds:
        return False

    _memory_cache[key] = now
    # Prevent memory leak by pruning expired keys periodically
    if len(_memory_cache) > 2000:
        cutoff = now - cooldown_seconds
        for k in list(_memory_cache.keys()):
            if _memory_cache[k] < cutoff:
                del _memory_cache[k]

    return True
