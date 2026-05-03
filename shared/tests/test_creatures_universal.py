"""Tests for shared.creatures.universal — think + mortal.return."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from shared.creatures.context import MortalContext
from shared.creatures.feed import (
    KIND_MORTAL_RETURN,
    KIND_THINK,
    ObservationFeed,
)
from shared.creatures.tools import (
    RegisteredTool,
    ToolNotBlessed,
    ToolRegistry,
    with_context,
)
from shared.creatures.universal import (
    MortalReturn,
    mortal_return,
    think,
    universal_tool_registry_entries,
)


@pytest.fixture
def feed(tmp_path: Path) -> ObservationFeed:
    return ObservationFeed("test_creature", base=tmp_path)


def _mortal_ctx(feed: ObservationFeed) -> MortalContext:
    return MortalContext(
        creature_id=feed.creature_id,  # keep ctx + feed consistent
        display_name="Email PM",
        audit=feed,
        logger=logging.getLogger("test"),
        llm=object(),  # type: ignore[arg-type]
        tools=ToolRegistry({}),
        memory=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
    )


# ----------------------------- think -------------------------------------


def test_think_appends_to_feed(feed: ObservationFeed):
    ctx = _mortal_ctx(feed)
    asyncio.run(think(ctx, "drafting reply for Berwyn"))
    events = list(feed.read_all())
    assert len(events) == 1
    e = events[0]
    assert e.kind == KIND_THINK
    assert e.data == {"text": "drafting reply for Berwyn"}
    assert e.creature_id == feed.creature_id


def test_think_records_empty_string_with_warning(feed: ObservationFeed):
    """An empty think() is a bug in the LLM's tool call; we still append
    so the bug is visible in audit."""
    ctx = _mortal_ctx(feed)
    asyncio.run(think(ctx, ""))
    events = list(feed.read_all())
    assert len(events) == 1
    assert events[0].data.get("warning") == "empty_or_non_string"


def test_think_records_non_string_with_warning(feed: ObservationFeed):
    ctx = _mortal_ctx(feed)
    asyncio.run(think(ctx, 123))  # type: ignore[arg-type]
    events = list(feed.read_all())
    assert len(events) == 1
    assert events[0].data.get("warning") == "empty_or_non_string"


# ----------------------------- mortal.return -----------------------------


def test_mortal_return_appends_lifecycle_event_and_raises(feed: ObservationFeed):
    ctx = _mortal_ctx(feed)
    with pytest.raises(MortalReturn) as exc:
        asyncio.run(mortal_return(ctx, {"sent": "draft"}))
    assert exc.value.result == {"sent": "draft"}
    events = list(feed.read_all())
    assert len(events) == 1
    assert events[0].kind == KIND_MORTAL_RETURN
    assert events[0].data == {"result": {"sent": "draft"}}


def test_mortal_return_with_unserializable_result(feed: ObservationFeed):
    """Anything that can't be json.dumps'd gets stored as repr — we never
    want mortal.return to fail mid-teardown."""
    ctx = _mortal_ctx(feed)

    class NotSerializable:
        def __repr__(self):
            return "<NotSerializable instance>"

    with pytest.raises(MortalReturn):
        asyncio.run(mortal_return(ctx, NotSerializable()))
    events = list(feed.read_all())
    assert events[0].data == {"result": {"_unserializable_repr": "<NotSerializable instance>"}}


def test_mortal_return_with_none_result(feed: ObservationFeed):
    ctx = _mortal_ctx(feed)
    with pytest.raises(MortalReturn) as exc:
        asyncio.run(mortal_return(ctx))
    assert exc.value.result is None
    events = list(feed.read_all())
    assert events[0].data == {"result": None}


# ----------------------------- universal_tool_registry_entries -----------


def test_universal_tool_registry_entries_has_think_and_return():
    entries = universal_tool_registry_entries()
    assert set(entries.keys()) == {"think", "mortal.return"}
    for entry in entries.values():
        assert isinstance(entry, RegisteredTool)
        assert entry.blessing is None  # universal tools have no blessing
        assert entry.description  # has a non-empty description for the LLM


def test_universal_tool_registry_entries_are_independent_copies():
    """Mutating one call's result mustn't affect another."""
    a = universal_tool_registry_entries()
    b = universal_tool_registry_entries()
    a["new_thing"] = a["think"]
    assert "new_thing" not in b


# ----------------------------- ToolRegistry integration ------------------


def test_tool_registry_invokes_universal_tool_via_context(feed: ObservationFeed):
    """End-to-end: build a registry with universal tools, bind a context,
    invoke 'think' through the registry — feed receives the event."""
    ctx = _mortal_ctx(feed)
    registry = ToolRegistry(universal_tool_registry_entries())

    async def run():
        with with_context(ctx):
            await registry.invoke("think", text="hello via registry")

    asyncio.run(run())
    events = list(feed.read_all())
    assert len(events) == 1
    assert events[0].data == {"text": "hello via registry"}


def test_tool_registry_invoke_unknown_tool_raises_not_blessed():
    registry = ToolRegistry(universal_tool_registry_entries())
    with pytest.raises(ToolNotBlessed, match="forbidden.thing"):
        asyncio.run(registry.invoke("forbidden.thing", text="x"))


def test_tool_registry_names_listing(feed: ObservationFeed):
    """ctx.tools.names() is the LLM's view of what's callable."""
    registry = ToolRegistry(universal_tool_registry_entries())
    assert registry.names() == ["mortal.return", "think"]


def test_tool_registry_invoke_without_context_raises():
    """Calling a tool outside a bound CreatureContext must fail loudly."""
    from shared.creatures.tools import ToolDispatchError

    registry = ToolRegistry(universal_tool_registry_entries())
    with pytest.raises(ToolDispatchError, match="CreatureContext"):
        asyncio.run(registry.invoke("think", text="orphan"))
