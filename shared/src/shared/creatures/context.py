"""Per-Creature context objects — the dependency-injection container.

v0.11 step 3a. Every Creature kind has its own ``Context``, frozen at
forge time, handed in by Hephaestus, immutable for the Creature's life.

The shape is the *only* thing the Creature touches. Mortals see a
``MortalContext``; Beasts a ``BeastContext``; Automatons an
``AutomatonContext``. Angels see their own ``AngelContext`` but
that's an implementation detail of the angel runtime, not Creature-
facing — see ``demiurge.pantheon.<god>`` modules.

Three rules baked into the type signature:

1. **No raw secrets.** No ``MortalContext.gmail_password`` or similar.
   Anything secret-touching goes through a blessed tool that brokers
   via Enkidu under the hood.
2. **Only blessed tools are visible.** ``ctx.tools`` is pre-filtered.
   ``ctx.tools.names()`` returns *exactly* what this Creature can call.
3. **No cross-Creature state.** No shared dict, no global registry the
   Creature can poke. Memory is reached through ``tools.memory.recall``
   only, mediated by Mnemosyne.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .feed import ObservationFeed
from .tools import ToolRegistry


# ----------------------------- protocols (typing seats) ------------------


class LLMHandle(Protocol):
    """A Creature's handle to its LLM. Stub in v0.11; Iris-mediated v0.12+."""

    async def complete(self, prompt: str, **kwargs: Any) -> str: ...

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]: ...


class MemoryHandle(Protocol):
    """A Creature's handle to memory recall. Stub in v0.11; Mnemosyne-mediated v0.13+."""

    async def recall(self, query: str, **kwargs: Any) -> list[dict[str, Any]]: ...


class BusHandle(Protocol):
    """A Creature's handle to the event bus. Pre-filtered at forge time:
    only the topic patterns declared in the manifest are subscribable +
    publishable."""

    async def publish(self, topic: str, payload: dict[str, Any]) -> None: ...

    async def subscribe(self, topic_pattern: str, handler: Any) -> None: ...


class ModelHandle(Protocol):
    """A Beast's handle to its model. Lighter than LLMHandle — Beasts
    don't have a chat surface, just one-shot transforms."""

    async def transform(self, payload: Any, **kwargs: Any) -> Any: ...


# ----------------------------- contexts ----------------------------------


@dataclass(frozen=True)
class CreatureContext:
    """Base context, common to every Creature kind.

    The ``feed`` is the observation-feed writer. Universal tools call
    ``ctx.feed.append(...)`` to record events; the dispatcher (3c) wraps
    blessed tool calls so their start/end events land here too.
    """

    creature_id: str
    display_name: str
    audit: ObservationFeed         # the per-Creature events.jsonl writer
    logger: logging.Logger

    # Subclasses add kind-specific handles below. We keep the base small
    # to avoid leaking surfaces into kinds that don't need them — an
    # Automaton with a phantom ``llm`` attribute would be a category
    # error.


@dataclass(frozen=True)
class MortalContext(CreatureContext):
    """Mortal — full agency, LLM-driven, scoped capabilities.

    The ``tools`` registry is pre-filtered to this Mortal's blessings;
    ``ctx.tools.names()`` is the *complete* list of callable tools.
    """

    llm: LLMHandle
    tools: ToolRegistry
    memory: MemoryHandle
    bus: BusHandle


@dataclass(frozen=True)
class BeastContext(CreatureContext):
    """Beast — model-driven, function-shaped, no agency.

    No ``tools`` registry: Beasts don't make decisions or call other
    things. They consume input, run their model, return output. Audit
    angel still observes (every Creature is observed).
    """

    model: ModelHandle


@dataclass(frozen=True)
class AutomatonContext(CreatureContext):
    """Automaton — deterministic, no LLM, no agency.

    Bus access only (so the scheduler can publish ``creature.tick.<id>``,
    so a polling Automaton can publish discovered events). No model, no
    tools, no memory.
    """

    bus: BusHandle


@dataclass(frozen=True)
class AngelContext(CreatureContext):
    """Angel — opaque god-extension. Observes a host Creature.

    NOT visible to any Creature. NOT exposed via ``demiurge hire show``.
    Only the angel's commissioning god holds a handle to its substrate
    output. v0.11 ships in-process angels (no separate process); v0.13
    promotes to out-of-process with stricter isolation.
    """

    god: str                       # "enkidu", "mnemosyne", ...
    angel_name: str                # "audit", "memory", ...
    host_creature_id: str          # the Creature this angel observes
    host_feed: ObservationFeed     # read-only handle to the host's feed
    config: dict[str, Any] = field(default_factory=dict)
