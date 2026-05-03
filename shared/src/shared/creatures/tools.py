"""Consumer-side types for blessed tools — what a Creature *sees*.

v0.11 step 3a. The dispatcher (Hephaestus) and the gods' blessing
implementations live elsewhere; this module is just the contract a
Creature interacts with at runtime.

A Creature gets a ``ToolRegistry`` in its context. ``await tools.invoke(
"gmail.send", to="...", body="...")`` routes through the appropriate
god's wrapper, which validates the blessing token before dispatching.
The Creature never sees the blessing token directly — that's machinery.

Universal tools (``think``, ``mortal.return``) are also in the registry,
but with no blessing (they don't go through any god). They're plain
local-effect operations that write to the observation feed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, Protocol


# ----------------------------- blessings (consumer-side) -----------------


@dataclass(frozen=True)
class Blessing:
    """Token a tool wrapper validates on every call.

    Minted by a god when granting a capability to a Creature. The wrapper
    holds it in closure; the Creature never sees it. Validation on every
    call is defense-in-depth (the policy-engine check is the primary).

    For v0.11 the token is a structured claim. v0.12+ may add an Ed25519
    signature from Enkidu so the token can't be replayed cross-Creature
    even if a wrapper is exfiltrated.
    """

    capability: str          # "gmail.send", "web.fetch", etc.
    creature_id: str         # who this blessing was issued to
    god: str                 # which god granted it ("enkidu", "arachne", ...)
    issued_at: datetime
    expires_at: Optional[datetime] = None
    scope: dict[str, Any] = field(default_factory=dict)
    # Free-form scope hints — e.g. {"account": "gmail.work"} or
    # {"domains": ["arxiv.org", "github.com"]}. The owning god uses this
    # at dispatch time to apply per-blessing constraints.

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        from datetime import timezone

        ref = now or datetime.now(tz=timezone.utc)
        return ref >= self.expires_at


@dataclass(frozen=True)
class Denial:
    """A god refused to grant a capability. Sibling of Blessing."""

    capability: str
    creature_id: str
    god: str
    reason: str
    requires_approval: bool = False
    # If True, a higher-level orchestrator (Zeus or Sol via the approvals
    # primitive) can convert this denial into a blessing by securing
    # operator confirmation.


# ----------------------------- tool dispatch (consumer side) -------------


class ToolNotBlessed(Exception):
    """Creature called a tool not in its blessed registry."""


class ToolDispatchError(Exception):
    """Tool was blessed but the call failed at dispatch (god-side error)."""


# A tool implementation is an async callable: `(ctx, **kwargs) -> result`.
# The ctx parameter is the CreatureContext (typed Any here to avoid an
# import cycle; consumers reify the typing in their own modules).
ToolImpl = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class RegisteredTool:
    """One entry in a Creature's tool registry.

    ``blessing`` is None for universal tools (think, mortal.return — they
    don't pass through any god). For blessed tools, the dispatcher binds
    the blessing into the wrapper's closure and references it here for
    introspection (so ``demiurge hire show <id>`` can list scope).
    """

    name: str
    impl: ToolImpl
    blessing: Optional[Blessing] = None
    description: str = ""


class ToolRegistry:
    """The set of tools a Creature has access to. Pre-filtered at forge time.

    Creatures see only what's here. Tools they didn't get blessed for
    aren't in the registry — calling ``invoke("forbidden.thing")`` raises
    ``ToolNotBlessed``, not a permissions error. The set is immutable
    after forge.
    """

    def __init__(self, tools: dict[str, RegisteredTool]):
        # Defensive copy + freeze the dict-like surface. Calling code
        # gets a read-only view; mutation requires a fresh registry.
        self._tools: dict[str, RegisteredTool] = dict(tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as e:
            raise ToolNotBlessed(
                f"tool {name!r} is not in this Creature's blessed registry"
            ) from e

    async def invoke(self, name: str, **kwargs: Any) -> Any:
        """Dispatch a tool call by name.

        For universal tools the impl runs locally (writes to the
        observation feed). For blessed tools the impl is a wrapper the
        dispatcher built at forge time; the wrapper validates the
        blessing and routes through the owning god.
        """
        from .context import CreatureContext  # local to avoid cycle

        tool = self.get(name)
        ctx = _resolve_context()
        if not isinstance(ctx, CreatureContext):
            raise ToolDispatchError(
                "tool dispatched without a CreatureContext bound to the "
                "current task — Creatures must run inside their own context"
            )
        return await tool.impl(ctx, **kwargs)


# ----------------------------- context plumbing --------------------------


# Context lookup is task-local — every Creature task runs with its
# CreatureContext pinned. ToolRegistry.invoke() pulls it out of the local
# rather than threading it through every call signature, so universal
# tool authors can write `def think(ctx, text): ...` without callers
# repeating ctx.
import contextvars  # noqa: E402

_current_context: contextvars.ContextVar = contextvars.ContextVar(
    "demiurge.creatures.current_context"
)


def _resolve_context():
    """Internal: read the contextvar set by ``with_context()``."""
    try:
        return _current_context.get()
    except LookupError:
        return None


def bind_context(ctx) -> contextvars.Token:
    """Pin a CreatureContext for the current async task.

    Call site: the supervisor calls this once when handing a Creature
    its turn, then ``unbind_context(token)`` after. Tests that exercise
    universal tools also bind a context via ``with_context()`` (the
    helper below).
    """
    return _current_context.set(ctx)


def unbind_context(token: contextvars.Token) -> None:
    _current_context.reset(token)


class with_context:
    """Sync context-manager pinning a CreatureContext for the duration of a block.

    Usage::

        with with_context(ctx):
            await tools.invoke("think", text="...")
    """

    def __init__(self, ctx):
        self._ctx = ctx
        self._token: Optional[contextvars.Token] = None

    def __enter__(self):
        self._token = bind_context(self._ctx)
        return self._ctx

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            unbind_context(self._token)
            self._token = None


# ----------------------------- godly side (re-exported for typing) -------


class GodlyBlessing(Protocol):
    """The interface every Pantheon god implements for Hephaestus.

    Defined here (consumer-facing module) only because Manifests and
    discovery code reference the type for annotations. The real
    implementations live in ``demiurge.pantheon.<god>``.
    """

    async def bless(
        self, *, creature_id: str, request: "ToolRequest"
    ) -> "Blessing | Denial": ...

    async def commission_angel(
        self, *, creature_id: str
    ) -> "Optional[AngelSpec]": ...


@dataclass(frozen=True)
class ToolRequest:
    """Hephaestus → God: 'this Creature wants this capability with this scope'."""

    capability: str
    creature_id: str
    requested_scope: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AngelSpec:
    """God → Hephaestus: 'attach an angel of this shape to that Creature'.

    v0.11 spec is intentionally minimal — we only ship Enkidu's audit
    angel as an in-process projection. The real out-of-process runtime
    arrives in v0.13 alongside Mnemosyne.
    """

    god: str                          # "enkidu", "mnemosyne", ...
    name: str                         # "audit", "memory", ...
    creature_id: str
    config: dict[str, Any] = field(default_factory=dict)
