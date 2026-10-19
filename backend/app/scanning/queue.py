import uuid

from redis.asyncio import Redis
from redis.exceptions import RedisError

QUEUE_KEY = "kmr:scan:queue"


class QueueUnavailable(Exception):  # noqa: N818 - domain term
    pass


class ScanQueue:
    """A plain Redis list of scan ids. Delivery is at-least-once and safe: workers claim a scan
    in the database with a status check, so a duplicate or stale message does nothing."""

    def __init__(self, client: Redis, key: str = QUEUE_KEY) -> None:
        self._client = client
        self._key = key

    async def enqueue(self, scan_id: uuid.UUID) -> None:
        try:
            await self._client.lpush(self._key, str(scan_id))
        except RedisError as exc:
            raise QueueUnavailable from exc

    async def dequeue(self, timeout: int = 5) -> uuid.UUID | None:
        try:
            item = await self._client.brpop([self._key], timeout=timeout)
        except RedisError as exc:
            raise QueueUnavailable from exc
        if item is None:
            return None
        raw = item[1].decode() if isinstance(item[1], bytes) else str(item[1])
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None  # ignore garbage rather than crash the worker
