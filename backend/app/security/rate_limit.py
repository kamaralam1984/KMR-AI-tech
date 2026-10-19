import time
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError


class RateLimiterUnavailable(Exception):  # noqa: N818 - domain term
    """The limiter backend failed. Callers must fail closed, not allow the request."""


class RateLimiter(Protocol):
    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Counts one attempt for `key`. Returns False once `limit` is exceeded in the window."""
        ...


class MemoryRateLimiter:
    """Single-process fixed-window limiter. For local development and tests only."""

    def __init__(self) -> None:
        self._counts: dict[str, tuple[float, int]] = {}

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        started, count = self._counts.get(key, (now, 0))
        if now - started >= window_seconds:
            started, count = now, 0
        count += 1
        self._counts[key] = (started, count)
        return count <= limit


class RedisRateLimiter:
    def __init__(self, client: Redis, prefix: str = "kmr:rl:") -> None:
        self._client = client
        self._prefix = prefix

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        redis_key = self._prefix + key
        try:
            count = await self._client.incr(redis_key)
            if count == 1:
                await self._client.expire(redis_key, window_seconds)
        except RedisError as exc:
            raise RateLimiterUnavailable from exc
        return int(count) <= limit
