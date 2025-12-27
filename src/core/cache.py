"""
Redis cache management.
"""
import json
from typing import Any, Optional
import redis.asyncio as redis
from src.config import settings


class CacheClient:
    """Redis cache client wrapper."""

    def __init__(self):
        self._client: Optional[redis.Redis] = None

    async def connect(self):
        """Connect to Redis."""
        if not settings.CACHE_ENABLED:
            return

        self._client = await redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            encoding="utf-8",
            decode_responses=True,
        )

    async def disconnect(self):
        """Disconnect from Redis."""
        if self._client:
            await self._client.close()

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        if not self._client or not settings.CACHE_ENABLED:
            return None

        try:
            value = await self._client.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            print(f"Cache get error: {e}")
        return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (default: from settings)

        Returns:
            True if successful
        """
        if not self._client or not settings.CACHE_ENABLED:
            return False

        try:
            ttl = ttl or settings.CACHE_TTL
            serialized = json.dumps(value)
            await self._client.setex(key, ttl, serialized)
            return True
        except Exception as e:
            print(f"Cache set error: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """
        Delete key from cache.

        Args:
            key: Cache key

        Returns:
            True if successful
        """
        if not self._client or not settings.CACHE_ENABLED:
            return False

        try:
            await self._client.delete(key)
            return True
        except Exception as e:
            print(f"Cache delete error: {e}")
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.

        Args:
            pattern: Key pattern (e.g., "user:*")

        Returns:
            Number of keys deleted
        """
        if not self._client or not settings.CACHE_ENABLED:
            return 0

        try:
            keys = []
            async for key in self._client.scan_iter(match=pattern):
                keys.append(key)

            if keys:
                return await self._client.delete(*keys)
            return 0
        except Exception as e:
            print(f"Cache delete pattern error: {e}")
            return 0

    async def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists
        """
        if not self._client or not settings.CACHE_ENABLED:
            return False

        try:
            return await self._client.exists(key) > 0
        except Exception as e:
            print(f"Cache exists error: {e}")
            return False


# Global cache client instance
cache = CacheClient()
