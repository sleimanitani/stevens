"""Bus abstraction.

The one mandatory shared contract in the system. Everything else is a detail;
this one decision shapes how agents and channels interact forever.

v0.1: Postgres-backed. Events are rows in the `events` table. Subscribers
poll with a cursor in `subscription_cursors`. LISTEN/NOTIFY wakes subscribers
immediately when new events land, so latency is near-zero despite being a
polling model underneath.

v0.2: same API, NATS JetStream implementation. No agent or channel code
changes when we migrate.

Topic matching uses dot-separated segments with `*` wildcards, matching the
NATS convention so patterns port cleanly:
  email.received.*          matches email.received.gmail.personal
  email.received.gmail.*    matches email.received.gmail.atheer
  *.received.*              matches any channel's received events

Note: `*` matches exactly one segment. Use `>` at the end for multi-segment
(also a NATS-ism) — not implemented in v0.1 since we don't need it yet.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable
from uuid import UUID

from psycopg.rows import dict_row

from .db import connection, get_pool
from .events import BaseEvent, parse_event


log = logging.getLogger(__name__)

EventHandler = Callable[[BaseEvent], Awaitable[None]]


async def publish(event: BaseEvent) -> UUID:
    """Publish an event to the bus.

    Writes to the events table and sends a LISTEN/NOTIFY wake-up so any
    subscriber waiting on new events can pick it up immediately.
    """
    async with connection() as conn:
        payload_json = event.model_dump_json(by_alias=True)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO events (event_id, topic, account_id, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                RETURNING event_id
                """,
                (str(event.event_id), event.topic, event.account_id, payload_json),
            )
            row = await cur.fetchone()
            # Wake up any subscribers listening on 'events_new'
            await cur.execute("NOTIFY events_new")
        await conn.commit()
        return row[0] if row else event.event_id


def _pattern_to_sql_like(pattern: str) -> str:
    """Convert a dotted wildcard pattern to a SQL LIKE pattern.

    email.received.*       -> email.received.%
    email.received.gmail.* -> email.received.gmail.%
    *.received.*           -> %.received.%
    """
    return pattern.replace(".", "__DOT__").replace("*", "%").replace("__DOT__", ".")


async def _fetch_new_events(
    conn, subscriber_id: str, pattern: str, sql_like: str, batch_size: int = 50
) -> list[tuple[str, str, dict]]:
    """Fetch events matching a pattern that haven't been processed by this subscriber."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT e.event_id, e.topic, e.payload, e.published_at
            FROM events e
            LEFT JOIN subscription_cursors c
              ON c.subscriber_id = %s AND c.topic_pattern = %s
            WHERE e.topic LIKE %s
              AND (c.last_published_at IS NULL OR e.published_at > c.last_published_at)
            ORDER BY e.published_at ASC
            LIMIT %s
            """,
            (subscriber_id, pattern, sql_like, batch_size),
        )
        rows = await cur.fetchall()
        return [(r["event_id"], r["topic"], r["payload"], r["published_at"]) for r in rows]


async def _advance_cursor(conn, subscriber_id: str, pattern: str, event_id: str, published_at) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO subscription_cursors (subscriber_id, topic_pattern, last_event_id, last_published_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (subscriber_id, topic_pattern) DO UPDATE
            SET last_event_id = EXCLUDED.last_event_id,
                last_published_at = EXCLUDED.last_published_at,
                updated_at = now()
            """,
            (subscriber_id, pattern, event_id, published_at),
        )
    await conn.commit()


async def subscribe(
    subscriber_id: str,
    pattern: str,
    handler: EventHandler,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Subscribe to all events matching `pattern` and invoke `handler` for each.

    Durable: the subscriber's cursor is persisted, so restarts resume from where
    they left off. `subscriber_id` must be stable across restarts for this to work
    — use the agent name.

    This coroutine runs forever (until stop_event is set). Run each subscription
    in its own asyncio.Task.
    """
    sql_like = _pattern_to_sql_like(pattern)
    stop_event = stop_event or asyncio.Event()
    log.info("subscriber=%s pattern=%s starting", subscriber_id, pattern)

    # Listen for new-event notifications to avoid polling when idle.
    pool = await get_pool()
    async with pool.connection() as listen_conn:
        await listen_conn.execute("LISTEN events_new")
        # Autocommit so NOTIFY is received without waiting for a transaction.
        await listen_conn.set_autocommit(True)

        while not stop_event.is_set():
            # Drain any matching events.
            async with connection() as conn:
                batch = await _fetch_new_events(conn, subscriber_id, pattern, sql_like)

            for event_id, topic, payload, published_at in batch:
                try:
                    event = parse_event(topic, payload if isinstance(payload, dict) else json.loads(payload))
                    await handler(event)
                except Exception:
                    log.exception("subscriber=%s event_id=%s handler failed", subscriber_id, event_id)
                    # Still advance cursor to avoid infinite retry loop.
                    # (v0.2 will add a dead-letter table and retry policy.)

                async with connection() as conn:
                    await _advance_cursor(conn, subscriber_id, pattern, event_id, published_at)

            if batch:
                # More may have landed while we processed — loop immediately.
                continue

            # Wait for NOTIFY or short timeout as backstop.
            try:
                async with asyncio.timeout(5.0):
                    async for _notify in listen_conn.notifies():
                        break
            except TimeoutError:
                pass
