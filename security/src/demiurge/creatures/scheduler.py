"""The Scheduler Automaton — v0.11 step 3e.4.

A concrete ``Automaton`` proving the kind works end-to-end. Holds a
subscription registry of ``(creature_id, interval)``. Each call to
``tick()`` walks the registry and fires a ``creature.tick.<id>`` bus
event for every subscription whose interval has elapsed since its last
fire.

Subscribers in v0.11 are typically:

- **Polling powers** (RSS reader, log shipper) — get woken to do their
  poll cadence.
- **Mortals** that want a periodic wake-up — e.g. email_pm could ask
  to be ticked hourly to catch up on anything Pub/Sub missed.

The scheduler itself runs as an Automaton (no LLM, no agency,
deterministic). The supervisor (v0.11 step 7) drives ``tick()`` on a
fixed cadence (default 1s); the scheduler decides which subscriptions
are due each call.

Subscription registry is in-memory in v0.11. Restart loses the registry
— Mortals / powers that want to subscribe re-subscribe at startup. v0.12+
will persist this if it becomes load-bearing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from shared.creatures.base import Automaton
from shared.creatures.context import AutomatonContext
from shared.creatures.feed import KIND_LIFECYCLE


# ----------------------------- interval parsing --------------------------


_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")


def parse_interval(spec: str) -> int:
    """Parse a duration string like ``"30s"``, ``"5m"``, ``"1h"``,
    ``"2d"`` into seconds. Raises ``ValueError`` on malformed input.

    Intentionally narrow: only the four units a scheduler cares about.
    Cron-style strings (``"0 */5 * * *"``) are out of scope for v0.11
    — if they're needed later, add a separate parser; don't bend this
    one.
    """
    m = _INTERVAL_RE.match(spec)
    if not m:
        raise ValueError(
            f"interval {spec!r} doesn't match <int><s|m|h|d>"
        )
    n = int(m.group(1))
    unit = m.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return n * multiplier


# ----------------------------- subscription type -------------------------


@dataclass(frozen=True)
class Subscription:
    """One scheduled tick target.

    ``creature_id`` identifies the subscriber — events fire on the topic
    ``creature.tick.<creature_id>``. ``interval_seconds`` is the cadence;
    ``last_fired_ts`` is when the scheduler last fired for this
    subscription (in ``time.time()`` seconds).
    """

    creature_id: str
    interval_seconds: int
    last_fired_ts: float = 0.0  # 0 = never fired; first tick fires immediately


# ----------------------------- the scheduler -----------------------------


# Type alias for the bus handle's publish surface — async fn(topic, payload).
BusPublish = Callable[..., Awaitable[Any]]
# Type alias for the clock — sync fn() → float seconds (time.time-shape).
Clock = Callable[[], float]


class Scheduler(Automaton):
    """The Scheduler Automaton. Holds subscriptions; fires due ones on tick.

    Constructed by Hephaestus during forge with an ``AutomatonContext``.
    The supervisor (v0.11 step 7) drives ``tick()`` on a regular cadence
    (default 1s); each call walks the subscription registry and fires
    bus events for any subscription whose ``interval_seconds`` has
    elapsed since ``last_fired_ts``.

    Subscriptions can be added/removed at runtime via ``subscribe()`` /
    ``unsubscribe()`` — the typical pattern is for a polling power to
    register itself in its bootstrap hook.

    A ``clock`` function is injectable for tests; defaults to
    ``time.time``. The bus is taken from the AutomatonContext.
    """

    def __init__(
        self,
        ctx: AutomatonContext,
        *,
        clock: Optional[Clock] = None,
    ):
        super().__init__(ctx)
        import time as _time

        self._clock = clock or _time.time
        self._subscriptions: dict[str, Subscription] = {}

    # ----------------------------- subscription API ----------------------

    def subscribe(self, *, creature_id: str, interval: str | int) -> Subscription:
        """Register a creature to receive periodic tick events.

        ``interval`` may be a duration string (``"5m"``, ``"1h"``) or
        an int in seconds. The subscription's ``last_fired_ts`` starts
        at 0 so the first tick after subscription fires immediately
        (gives polling powers a chance to do an initial poll without
        waiting a full interval).
        """
        if isinstance(interval, str):
            interval_seconds = parse_interval(interval)
        else:
            interval_seconds = int(interval)
        if interval_seconds <= 0:
            raise ValueError(f"interval must be > 0 seconds, got {interval_seconds}")

        sub = Subscription(
            creature_id=creature_id,
            interval_seconds=interval_seconds,
            last_fired_ts=0.0,
        )
        self._subscriptions[creature_id] = sub
        return sub

    def unsubscribe(self, *, creature_id: str) -> bool:
        """Remove a subscription. Returns True if one existed."""
        return self._subscriptions.pop(creature_id, None) is not None

    def subscriptions(self) -> list[Subscription]:
        """Snapshot of current subscriptions, ordered by creature_id."""
        return [self._subscriptions[k] for k in sorted(self._subscriptions.keys())]

    # ----------------------------- the tick ------------------------------

    async def tick(self) -> int:
        """Walk the registry; fire bus events for due subscriptions.

        Returns the number of subscriptions fired this tick. The supervisor
        uses this for observability (a tick that fires nothing is a no-op
        and shouldn't generate noise; one that fires N events should record
        them).
        """
        now = self._clock()
        fired = 0

        for creature_id in sorted(self._subscriptions.keys()):
            sub = self._subscriptions[creature_id]
            elapsed = now - sub.last_fired_ts
            # last_fired_ts == 0 is the sentinel for "never fired" — fire
            # immediately on the first tick so polling powers can do their
            # initial poll without waiting a full interval.
            if sub.last_fired_ts > 0 and elapsed < sub.interval_seconds:
                continue

            topic = f"creature.tick.{creature_id}"
            payload = {
                "creature_id": creature_id,
                "scheduled_at": now,
                "elapsed_since_last": elapsed if sub.last_fired_ts > 0 else None,
                "interval_seconds": sub.interval_seconds,
            }
            try:
                await self._ctx.bus.publish(topic, payload)
            except Exception as e:  # noqa: BLE001 — keep ticking other subs
                # A failed publish is logged but doesn't stop the
                # scheduler. The audit feed records it; the supervisor
                # can investigate.
                self._ctx.audit.append(
                    kind=KIND_LIFECYCLE,
                    data={
                        "scheduler_event": "publish_failed",
                        "topic": topic,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                continue

            self._subscriptions[creature_id] = Subscription(
                creature_id=sub.creature_id,
                interval_seconds=sub.interval_seconds,
                last_fired_ts=now,
            )
            fired += 1

        return fired
