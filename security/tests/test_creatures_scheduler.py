"""Tests for the Scheduler Automaton — v0.11 step 3e.4."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from demiurge.creatures import Scheduler, Subscription, parse_interval
from shared.creatures.context import AutomatonContext
from shared.creatures.feed import KIND_LIFECYCLE, ObservationFeed


# ----------------------------- parse_interval ---------------------------


def test_parse_interval_seconds():
    assert parse_interval("30s") == 30


def test_parse_interval_minutes():
    assert parse_interval("5m") == 300


def test_parse_interval_hours():
    assert parse_interval("2h") == 7200


def test_parse_interval_days():
    assert parse_interval("1d") == 86400


def test_parse_interval_with_whitespace():
    assert parse_interval("  10m  ") == 600


def test_parse_interval_rejects_cron():
    with pytest.raises(ValueError, match="match"):
        parse_interval("0 */5 * * *")


def test_parse_interval_rejects_bare_int():
    with pytest.raises(ValueError):
        parse_interval("60")


def test_parse_interval_rejects_unknown_unit():
    with pytest.raises(ValueError):
        parse_interval("5w")  # weeks not supported


# ----------------------------- fixtures + helpers ------------------------


class FakeBus:
    """In-memory bus collecting published events."""

    def __init__(self, raise_for: set[str] | None = None):
        self.published: list[tuple[str, dict]] = []
        self._raise_for = raise_for or set()

    async def publish(self, topic: str, payload: dict) -> None:
        if topic in self._raise_for:
            raise RuntimeError(f"fake-bus refuses to publish {topic!r}")
        self.published.append((topic, payload))

    async def subscribe(self, topic_pattern: str, handler) -> None:
        raise NotImplementedError


def _ctx(tmp_path: Path, bus: FakeBus) -> AutomatonContext:
    feed = ObservationFeed("scheduler.default", base=tmp_path / "feeds")
    return AutomatonContext(
        creature_id="scheduler.default",
        display_name="Scheduler",
        audit=feed,
        logger=logging.getLogger("test"),
        bus=bus,  # type: ignore[arg-type]
    )


def _scheduler(tmp_path: Path, bus: FakeBus, *, clock_value: float = 0.0):
    # Mutable container so tests can advance the clock between ticks.
    state = {"now": clock_value}
    s = Scheduler(_ctx(tmp_path, bus), clock=lambda: state["now"])
    return s, state


# ----------------------------- subscribe / unsubscribe -------------------


def test_subscribe_with_string_interval(tmp_path: Path):
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    sub = s.subscribe(creature_id="rss_reader.default", interval="5m")
    assert sub.interval_seconds == 300
    assert sub.last_fired_ts == 0.0
    assert s.subscriptions() == [sub]


def test_subscribe_with_int_interval(tmp_path: Path):
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    s.subscribe(creature_id="x", interval=10)
    assert s.subscriptions()[0].interval_seconds == 10


def test_subscribe_rejects_zero_or_negative(tmp_path: Path):
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    with pytest.raises(ValueError, match=">"):
        s.subscribe(creature_id="x", interval=0)
    with pytest.raises(ValueError, match=">"):
        s.subscribe(creature_id="x", interval=-1)


def test_subscribe_overwrites_existing(tmp_path: Path):
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    s.subscribe(creature_id="x", interval="5m")
    s.subscribe(creature_id="x", interval="10m")
    subs = s.subscriptions()
    assert len(subs) == 1
    assert subs[0].interval_seconds == 600


def test_unsubscribe_existing(tmp_path: Path):
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    s.subscribe(creature_id="x", interval="5m")
    assert s.unsubscribe(creature_id="x") is True
    assert s.subscriptions() == []


def test_unsubscribe_missing(tmp_path: Path):
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    assert s.unsubscribe(creature_id="nope") is False


def test_subscriptions_ordered_stably(tmp_path: Path):
    """Stable ordering by creature_id — important for deterministic tests."""
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    s.subscribe(creature_id="zeta", interval="1m")
    s.subscribe(creature_id="alpha", interval="1m")
    s.subscribe(creature_id="middle", interval="1m")
    names = [sub.creature_id for sub in s.subscriptions()]
    assert names == ["alpha", "middle", "zeta"]


# ----------------------------- tick: empty / no-due ----------------------


def test_tick_empty_registry(tmp_path: Path):
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    fired = asyncio.run(s.tick())
    assert fired == 0
    assert bus.published == []


def test_tick_first_call_fires_immediately(tmp_path: Path):
    """A subscription with last_fired_ts=0 is immediately due on first tick.

    Gives polling powers a chance to do their initial poll without
    waiting a full interval after subscription.
    """
    bus = FakeBus()
    s, state = _scheduler(tmp_path, bus, clock_value=1000.0)
    s.subscribe(creature_id="rss_reader.default", interval="1h")

    fired = asyncio.run(s.tick())
    assert fired == 1
    assert len(bus.published) == 1
    topic, payload = bus.published[0]
    assert topic == "creature.tick.rss_reader.default"
    assert payload["creature_id"] == "rss_reader.default"
    assert payload["scheduled_at"] == 1000.0
    assert payload["elapsed_since_last"] is None  # first tick
    assert payload["interval_seconds"] == 3600


def test_tick_does_not_re_fire_within_interval(tmp_path: Path):
    bus = FakeBus()
    s, state = _scheduler(tmp_path, bus, clock_value=1000.0)
    s.subscribe(creature_id="x", interval=60)

    asyncio.run(s.tick())  # first tick fires
    state["now"] = 1030.0  # only 30s elapsed
    asyncio.run(s.tick())

    assert len(bus.published) == 1


def test_tick_re_fires_after_interval_elapses(tmp_path: Path):
    bus = FakeBus()
    s, state = _scheduler(tmp_path, bus, clock_value=1000.0)
    s.subscribe(creature_id="x", interval=60)

    asyncio.run(s.tick())  # fire at t=1000
    state["now"] = 1061.0  # 61s later — past 60s interval
    asyncio.run(s.tick())

    assert len(bus.published) == 2
    assert bus.published[1][1]["elapsed_since_last"] == pytest.approx(61.0)


def test_tick_independent_intervals(tmp_path: Path):
    """Two subs with different intervals fire independently."""
    bus = FakeBus()
    s, state = _scheduler(tmp_path, bus, clock_value=1000.0)
    s.subscribe(creature_id="fast", interval=10)
    s.subscribe(creature_id="slow", interval=100)

    asyncio.run(s.tick())
    assert len(bus.published) == 2  # both fire on first tick

    state["now"] = 1015.0  # 15s later
    asyncio.run(s.tick())
    assert len(bus.published) == 3  # only fast fires; slow not due
    assert bus.published[2][0] == "creature.tick.fast"

    state["now"] = 1105.0  # 105s after start
    asyncio.run(s.tick())
    # Both due now (fast last fired at 1015, 90s ago; slow last fired at
    # 1000, 105s ago)
    assert len(bus.published) == 5


def test_tick_publish_failure_recorded_to_feed(tmp_path: Path):
    """A failed publish goes in the audit feed but doesn't stop the scheduler."""
    bus = FakeBus(raise_for={"creature.tick.broken"})
    s, _ = _scheduler(tmp_path, bus, clock_value=1000.0)
    s.subscribe(creature_id="broken", interval=10)
    s.subscribe(creature_id="working", interval=10)

    fired = asyncio.run(s.tick())
    # Working sub fired; broken sub failed.
    assert fired == 1
    assert len(bus.published) == 1
    assert bus.published[0][0] == "creature.tick.working"

    # Failed publish recorded as a lifecycle event.
    feed_events = list(s.context.audit.read_all())
    failure_events = [
        e for e in feed_events
        if e.kind == KIND_LIFECYCLE and e.data.get("scheduler_event") == "publish_failed"
    ]
    assert len(failure_events) == 1
    assert "broken" in failure_events[0].data["topic"]


def test_tick_failed_publish_does_not_advance_last_fired(tmp_path: Path):
    """If publish fails, the subscription's last_fired_ts stays at 0 so
    the next tick retries immediately."""
    bus = FakeBus(raise_for={"creature.tick.x"})
    s, state = _scheduler(tmp_path, bus, clock_value=1000.0)
    s.subscribe(creature_id="x", interval=60)

    asyncio.run(s.tick())  # publish fails
    sub_after_fail = s.subscriptions()[0]
    assert sub_after_fail.last_fired_ts == 0.0

    # Now the bus works and we tick again — should retry.
    bus._raise_for.clear()
    asyncio.run(s.tick())
    assert len(bus.published) == 1


def test_tick_returns_count_fired(tmp_path: Path):
    bus = FakeBus()
    s, state = _scheduler(tmp_path, bus, clock_value=1000.0)
    for i in range(5):
        s.subscribe(creature_id=f"sub_{i}", interval=10)

    fired = asyncio.run(s.tick())
    assert fired == 5

    # No advance in time → next tick fires nothing.
    fired = asyncio.run(s.tick())
    assert fired == 0


# ----------------------------- Automaton ABC compliance ------------------


def test_scheduler_is_an_automaton():
    from shared.creatures.base import Automaton

    assert issubclass(Scheduler, Automaton)


def test_scheduler_has_creature_id_property(tmp_path: Path):
    bus = FakeBus()
    s, _ = _scheduler(tmp_path, bus)
    assert s.creature_id == "scheduler.default"
    assert s.display_name == "Scheduler"
