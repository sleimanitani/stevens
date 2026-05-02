"""TTL cache + per-domain rate limiter, both in-memory.

Lives in Enkidu's process. v0.3.1 ships in-memory only; future shared
shape (Postgres-backed cache + Enkidu-mediated ACL via
``web.cache.{get,put}`` capabilities) is documented in
``docs/architecture/agent-isolation.md``.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    """Bounded LRU cache with per-key TTL.

    Pure data structure — caller does the get/put dance, we just track.
    Eviction policy: LRU once capacity is hit. Expired entries are
    cleaned up on access (lazy).
    """

    def __init__(
        self,
        *,
        max_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_entries
        self._clock = clock
        self._entries: "OrderedDict[Any, _CacheEntry]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: Any) -> Optional[Any]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at < self._clock():
            del self._entries[key]
            return None
        # Refresh LRU position.
        self._entries.move_to_end(key)
        return entry.value

    def put(self, key: Any, value: Any, *, ttl_seconds: float) -> None:
        now = self._clock()
        if key in self._entries:
            del self._entries[key]
        self._entries[key] = _CacheEntry(value=value, expires_at=now + ttl_seconds)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class DomainRateLimiter:
    """Token bucket per domain.

    Default: 10 tokens / sec / domain, burst capacity 20. Sub-second
    refill is supported (we recalculate on each ``allow`` call).
    """

    def __init__(
        self,
        *,
        rate_per_second: float = 10.0,
        burst: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate_per_second
        self._burst = burst
        self._clock = clock
        self._buckets: Dict[str, _Bucket] = {}

    def allow(self, domain: str) -> bool:
        """Consume one token from ``domain``'s bucket. True if allowed."""
        now = self._clock()
        bucket = self._buckets.get(domain)
        if bucket is None:
            bucket = _Bucket(tokens=float(self._burst), last_refill=now)
            self._buckets[domain] = bucket
        elapsed = now - bucket.last_refill
        if elapsed > 0:
            bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
            bucket.last_refill = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False
