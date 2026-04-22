"""Postgres connection helper.

v0.1 uses psycopg3 with an async connection pool. Adequate for single-node,
single-user scale. If contention becomes an issue, reach for a proper pooler
(pgbouncer) before rewriting this.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from psycopg_pool import AsyncConnectionPool


_pool: AsyncConnectionPool | None = None


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return dsn


async def get_pool() -> AsyncConnectionPool:
    """Lazy-init a single shared pool per process."""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(_dsn(), min_size=1, max_size=10, open=False)
        await _pool.open()
    return _pool


@asynccontextmanager
async def connection() -> AsyncIterator:
    """Async context manager yielding a connection from the pool."""
    pool = await get_pool()
    async with pool.connection() as conn:
        yield conn


async def close_pool() -> None:
    """Call on graceful shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
