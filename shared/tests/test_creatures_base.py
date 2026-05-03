"""Tests for shared.creatures.base + context — the ABC contracts."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from shared.creatures.base import (
    Angel,
    Automaton,
    Beast,
    Creature,
    Mortal,
)
from shared.creatures.context import (
    AngelContext,
    AutomatonContext,
    BeastContext,
    CreatureContext,
    MortalContext,
)
from shared.creatures.feed import ObservationFeed
from shared.creatures.tools import RegisteredTool, ToolRegistry


def _logger() -> logging.Logger:
    return logging.getLogger("test")


@pytest.fixture
def feed(tmp_path: Path) -> ObservationFeed:
    return ObservationFeed("test_creature", base=tmp_path)


# ----------------------------- abstractness ------------------------------


def test_creature_is_abstract():
    with pytest.raises(TypeError):
        Creature()  # type: ignore[abstract]


def test_mortal_requires_handle_event():
    """A Mortal subclass without handle_event can't be instantiated."""

    class IncompleteMortal(Mortal):
        pass

    with pytest.raises(TypeError):
        IncompleteMortal(ctx=None)  # type: ignore[abstract,arg-type]


def test_beast_requires_transform():
    class IncompleteBeast(Beast):
        pass

    with pytest.raises(TypeError):
        IncompleteBeast(ctx=None)  # type: ignore[abstract,arg-type]


def test_automaton_requires_tick():
    class IncompleteAutomaton(Automaton):
        pass

    with pytest.raises(TypeError):
        IncompleteAutomaton(ctx=None)  # type: ignore[abstract,arg-type]


def test_angel_requires_observe():
    class IncompleteAngel(Angel):
        pass

    with pytest.raises(TypeError):
        IncompleteAngel(ctx=None)  # type: ignore[abstract,arg-type]


# ----------------------------- minimal valid subclasses ------------------


def _mortal_ctx(feed: ObservationFeed) -> MortalContext:
    """Build a minimal valid MortalContext for tests.

    All handles are stubs — we're testing the type system + ABC shape,
    not behavior. Real handles arrive in step 3c (tool routing) and
    step 3e (forge).
    """
    return MortalContext(
        creature_id="email_pm.personal",
        display_name="Email PM",
        audit=feed,
        logger=_logger(),
        llm=object(),  # type: ignore[arg-type]
        tools=ToolRegistry({}),
        memory=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
    )


def test_mortal_minimal_subclass_instantiates(feed: ObservationFeed):
    class Echo(Mortal):
        async def handle_event(self, event):
            return None

    m = Echo(ctx=_mortal_ctx(feed))
    assert m.creature_id == "email_pm.personal"
    assert m.display_name == "Email PM"


def test_beast_minimal_subclass_instantiates(feed: ObservationFeed):
    class IdentityBeast(Beast):
        async def transform(self, payload, **kwargs):
            return payload

    ctx = BeastContext(
        creature_id="image_gen.default",
        display_name="Image Generator",
        audit=feed,
        logger=_logger(),
        model=object(),  # type: ignore[arg-type]
    )
    b = IdentityBeast(ctx=ctx)
    assert b.creature_id == "image_gen.default"


def test_automaton_minimal_subclass_instantiates(feed: ObservationFeed):
    class NoopAutomaton(Automaton):
        async def tick(self):
            return None

    ctx = AutomatonContext(
        creature_id="scheduler",
        display_name="Scheduler",
        audit=feed,
        logger=_logger(),
        bus=object(),  # type: ignore[arg-type]
    )
    a = NoopAutomaton(ctx=ctx)
    assert a.creature_id == "scheduler"


def test_angel_minimal_subclass_instantiates(feed: ObservationFeed):
    class WatcherAngel(Angel):
        async def observe(self):
            return None

    ctx = AngelContext(
        creature_id="enkidu.audit.email_pm.personal",
        display_name="Enkidu Audit Angel — Email PM",
        audit=feed,
        logger=_logger(),
        god="enkidu",
        angel_name="audit",
        host_creature_id="email_pm.personal",
        host_feed=feed,  # in real life this is a different (read-only) handle
    )
    g = WatcherAngel(ctx=ctx)
    assert g.context.god == "enkidu"
    assert g.context.angel_name == "audit"
    assert g.context.host_creature_id == "email_pm.personal"


# ----------------------------- context immutability ----------------------


def test_creature_context_is_frozen(feed: ObservationFeed):
    """Frozen dataclass — no attribute assignment after construction."""
    ctx = _mortal_ctx(feed)
    with pytest.raises(Exception):  # FrozenInstanceError
        ctx.display_name = "Renamed"  # type: ignore[misc]


def test_default_on_spawn_and_on_retire_are_noops(feed: ObservationFeed):
    class Echo(Mortal):
        async def handle_event(self, event):
            return None

    import asyncio

    m = Echo(ctx=_mortal_ctx(feed))
    asyncio.run(m.on_spawn())   # no exception
    asyncio.run(m.on_retire())  # no exception


# ----------------------------- step() default ----------------------------


def test_default_mortal_step_raises_until_supervisor_ships(feed: ObservationFeed):
    """Step 3a deliberately leaves Mortal.step() unimplemented; the default
    arrives with the supervisor in step 3e. This test pins the contract."""
    import asyncio

    class Echo(Mortal):
        async def handle_event(self, event):
            return None

    m = Echo(ctx=_mortal_ctx(feed))
    with pytest.raises(NotImplementedError, match="step"):
        asyncio.run(m.step("anything"))
