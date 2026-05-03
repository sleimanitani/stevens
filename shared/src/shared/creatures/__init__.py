"""Creature interfaces — the operator-facing contract for plugin authors.

v0.11 step 3a. Anyone shipping a Mortal/Beast/Automaton plugin imports
from here. Hephaestus (in ``demiurge.pantheon.hephaestus``) is the
forge implementation that instantiates these.

Module map:

- ``base`` — Creature ABC + Mortal/Beast/Automaton/Angel subclasses
- ``context`` — CreatureContext + per-kind contexts (frozen dataclasses)
  + LLMHandle/MemoryHandle/BusHandle/ModelHandle protocols
- ``feed`` — observation-feed writer + UUIDv7 + envelope
- ``tools`` — Blessing/Denial/ToolRegistry consumer types + the
  contextvar plumbing that injects ``ctx`` into tool dispatch
- ``universal`` — ``think`` and ``mortal.return``, the only universal tools
"""

from .base import (  # noqa: F401
    Angel,
    Automaton,
    Beast,
    Creature,
    Mortal,
)
from .context import (  # noqa: F401
    AngelContext,
    AutomatonContext,
    BeastContext,
    BusHandle,
    CreatureContext,
    LLMHandle,
    MemoryHandle,
    ModelHandle,
    MortalContext,
)
from .feed import (  # noqa: F401
    KIND_LIFECYCLE,
    KIND_LLM_EXCHANGE,
    KIND_MORTAL_RETURN,
    KIND_THINK,
    KIND_TOOL_CALL_END,
    KIND_TOOL_CALL_START,
    SCHEMA_VERSION,
    FeedEvent,
    ObservationFeed,
    feed_path_for,
    feed_root,
    parse_uuid7_timestamp,
    uuid7,
)
from .tools import (  # noqa: F401
    AngelSpec,
    Blessing,
    Denial,
    GodlyBlessing,
    RegisteredTool,
    ToolDispatchError,
    ToolNotBlessed,
    ToolRegistry,
    ToolRequest,
    bind_context,
    unbind_context,
    with_context,
)
from .universal import (  # noqa: F401
    MortalReturn,
    mortal_return,
    think,
    universal_tool_registry_entries,
)
