"""Creature base classes — the four kinds (+ Angel).

v0.11 step 3a. Every Creature is forged from a manifest and lives inside a
Context. The base classes here define the *interface* — what method
shape Hephaestus expects to find when he forges, what the supervisor
calls to drive each Creature kind.

Four concrete kinds:

- ``Mortal`` — full agency. ``handle_event`` for bus-driven work,
  ``step`` for LLM-driven monologue.
- ``Beast`` — function-shaped. ``transform(input) → output``. No loop,
  no agency.
- ``Automaton`` — deterministic. ``tick()`` per scheduled invocation.
  No LLM, no agency.
- ``Angel`` — opaque god-extension. ``observe()`` reads its host's
  feed and projects to its god's substrate. Not exposed Creature-side.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from .context import (
    AngelContext,
    AutomatonContext,
    BeastContext,
    MortalContext,
)


# ----------------------------- shared base -------------------------------


class Creature(ABC):
    """Root ABC for everything Hephaestus forges.

    Subclasses don't subclass ``Creature`` directly — they pick one of
    ``Mortal``, ``Beast``, ``Automaton``, or ``Angel`` and inherit from
    that. ``Creature`` is the type-system handle the runtime uses
    polymorphically (e.g. for the supervisor's restart logic).
    """

    @property
    @abstractmethod
    def context(self):
        """The CreatureContext bound at forge time. Read-only."""

    @property
    def creature_id(self) -> str:
        return self.context.creature_id

    @property
    def display_name(self) -> str:
        return self.context.display_name

    async def on_spawn(self) -> None:
        """One-time hook called by the supervisor right after forge.

        Default: no-op. Subclasses override to wire bus subscriptions,
        load saved state via ``tools.memory.recall``, etc.
        """

    async def on_retire(self) -> None:
        """Cleanup hook called by the supervisor before Hades archives.

        Default: no-op. Subclasses override to flush in-flight work,
        publish a goodbye event, etc.
        """


# ----------------------------- Mortal ------------------------------------


class Mortal(Creature):
    """Full-agency LLM-driven Creature.

    Subclass this in a Mortal plugin (e.g. ``demiurge_mortal_email_pm``).
    Override ``handle_event`` for bus-driven work; override ``step`` only
    if your Mortal needs an exotic monologue shape (default ``step``
    impl arrives in step 3e along with the supervisor).
    """

    def __init__(self, ctx: MortalContext):
        self._ctx = ctx

    @property
    def context(self) -> MortalContext:
        return self._ctx

    @abstractmethod
    async def handle_event(self, event: dict[str, Any]) -> None:
        """Called by the supervisor when a subscribed bus event fires.

        ``event`` is a dict with at least ``topic``, ``payload``, and
        ``correlation_id`` (so the Mortal can chain its actions back to
        the inbound trigger).
        """

    async def step(self, prompt: str) -> Optional[Any]:
        """One LLM-driven monologue step.

        Default impl is intentionally not provided here — the supervisor
        in step 3e supplies a default that calls ``ctx.llm.chat``,
        feeds in tool descriptions, parses tool requests, dispatches
        through ``ctx.tools``, loops until the model emits
        ``mortal.return``. Subclasses can override for exotic shapes.
        """
        raise NotImplementedError(
            "default step() arrives in step 3e (supervisor); for now, "
            "override step() in your Mortal subclass or use handle_event"
        )


# ----------------------------- Beast -------------------------------------


class Beast(Creature):
    """Model-driven, function-shaped, no agency.

    Examples: image generators, embedders, classifiers, summarizers.
    A Beast is invoked once per request, runs its model, returns a
    result. No monologue loop, no bus subscription.

    Beasts are still Creatures (audit angel observes; can be retired)
    so they can be parameterized + tracked across calls.
    """

    def __init__(self, ctx: BeastContext):
        self._ctx = ctx

    @property
    def context(self) -> BeastContext:
        return self._ctx

    @abstractmethod
    async def transform(self, payload: Any, **kwargs: Any) -> Any:
        """Transform input to output. Stochastic (model-driven) but
        otherwise pure: no side effects, no state across calls."""


# ----------------------------- Automaton ---------------------------------


class Automaton(Creature):
    """Deterministic, no LLM, no agency.

    Examples: scheduler, RSS poller, log shipper, port scanner.
    The supervisor invokes ``tick()`` per the manifest's polling cadence
    (or per a bus event subscription); the Automaton does its work,
    publishes any discovered events to the bus, and returns.

    Default impl: no-op. Subclass and override.
    """

    def __init__(self, ctx: AutomatonContext):
        self._ctx = ctx

    @property
    def context(self) -> AutomatonContext:
        return self._ctx

    @abstractmethod
    async def tick(self) -> None:
        """One invocation. Called by supervisor per declared schedule
        (cron-like, polling-mode powers, or scheduler Automaton itself)."""


# ----------------------------- Angel -------------------------------------


class Angel(Creature):
    """Opaque god-extension. Observes a host Creature for its commissioning god.

    NOT instantiated by Creature plugins — only by gods (or by Hephaestus
    on a god's behalf). The host Creature does not know its angels exist.

    v0.11 ships in-process angels (e.g. Enkidu's audit angel runs as a
    method on the existing audit log writer, exposing the Angel API for
    forward compatibility). v0.13 promotes to out-of-process with full
    process isolation + dedicated IPC channels.
    """

    def __init__(self, ctx: AngelContext):
        self._ctx = ctx

    @property
    def context(self) -> AngelContext:
        return self._ctx

    @abstractmethod
    async def observe(self) -> None:
        """Read from the host's feed and project to the god's substrate.

        v0.11: called by the supervisor in-process when the host appends
        an event. v0.13+: runs as its own loop in a separate process,
        tailing the feed file with read-only handles.
        """
