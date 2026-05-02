"""Tests for TTLCache + DomainRateLimiter."""

from __future__ import annotations

import pytest

from demiurge.outbound.web_state import DomainRateLimiter, TTLCache


def test_cache_hit_then_miss_after_expiry():
    now = [0.0]
    cache = TTLCache(clock=lambda: now[0])
    cache.put("k", "v", ttl_seconds=10.0)
    now[0] = 5.0
    assert cache.get("k") == "v"
    now[0] = 11.0
    assert cache.get("k") is None


def test_cache_miss_for_unknown_key():
    cache = TTLCache()
    assert cache.get("nope") is None


def test_cache_lru_eviction():
    now = [0.0]
    cache = TTLCache(max_entries=2, clock=lambda: now[0])
    cache.put("a", 1, ttl_seconds=100.0)
    cache.put("b", 2, ttl_seconds=100.0)
    cache.put("c", 3, ttl_seconds=100.0)  # evicts "a" (oldest)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_cache_get_refreshes_lru_position():
    now = [0.0]
    cache = TTLCache(max_entries=2, clock=lambda: now[0])
    cache.put("a", 1, ttl_seconds=100.0)
    cache.put("b", 2, ttl_seconds=100.0)
    cache.get("a")              # touch a → b is now oldest
    cache.put("c", 3, ttl_seconds=100.0)  # evicts "b"
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_cache_put_replaces_existing():
    cache = TTLCache(max_entries=10)
    cache.put("k", "old", ttl_seconds=10.0)
    cache.put("k", "new", ttl_seconds=10.0)
    assert cache.get("k") == "new"


def test_rate_limiter_initial_burst():
    now = [0.0]
    rl = DomainRateLimiter(rate_per_second=1.0, burst=3, clock=lambda: now[0])
    assert rl.allow("example.com")
    assert rl.allow("example.com")
    assert rl.allow("example.com")
    assert not rl.allow("example.com")  # exhausted


def test_rate_limiter_refills_over_time():
    now = [0.0]
    rl = DomainRateLimiter(rate_per_second=10.0, burst=1, clock=lambda: now[0])
    assert rl.allow("x.com")
    assert not rl.allow("x.com")
    now[0] = 0.2  # 0.2s × 10/sec = 2 tokens, but burst caps at 1
    assert rl.allow("x.com")
    assert not rl.allow("x.com")


def test_rate_limiter_independent_buckets():
    now = [0.0]
    rl = DomainRateLimiter(rate_per_second=1.0, burst=1, clock=lambda: now[0])
    assert rl.allow("a.com")
    assert rl.allow("b.com")  # different bucket — own budget
    assert not rl.allow("a.com")
    assert not rl.allow("b.com")
