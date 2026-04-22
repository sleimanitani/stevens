"""Capability registry.

Capabilities are the operations the Security Agent performs on callers'
behalf — "send this Gmail draft," "charge this card," "complete this
prompt." Each capability is declared here with:

- a name (``"gmail.send_draft"``, ``"ping"``, etc)
- an async handler ``(agent, params, context) -> result_dict``
- a set of ``clear_params`` — the param names whose values are safe to
  log in the clear. Every other param is SHA-256 hashed in the audit
  log. Default: nothing is clear except ``account_id`` (always clear —
  it's a routing label, not a secret).

The module ships a ``default_registry`` singleton that real capability
modules register into on import (see :mod:`stevens_security.capabilities.ping`).
Tests can also create isolated ``CapabilityRegistry`` instances for
parallel/independent test runs.

Handler signature: ``async def h(agent, params, context)``. Older
two-arg handlers ``(agent, params)`` are still supported — the
registry introspects the signature and calls appropriately. New
capabilities should take three args.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, FrozenSet, Iterable, Optional

from ..context import CapabilityContext

Handler = Callable[..., Awaitable[Dict[str, Any]]]

ALWAYS_CLEAR: FrozenSet[str] = frozenset({"account_id"})


class RegistryError(Exception):
    """Raised when a capability is double-registered or looked up incorrectly."""


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    handler: Handler
    clear_params: FrozenSet[str] = field(default_factory=frozenset)
    wants_context: bool = False

    async def invoke(
        self, agent: Any, params: Dict[str, Any], context: CapabilityContext
    ) -> Dict[str, Any]:
        if self.wants_context:
            return await self.handler(agent, params, context)
        return await self.handler(agent, params)


class CapabilityRegistry:
    """A mutable map from capability name → CapabilitySpec."""

    def __init__(self) -> None:
        self._specs: Dict[str, CapabilitySpec] = {}

    def register(
        self,
        name: str,
        handler: Handler,
        *,
        clear_params: Iterable[str] = (),
    ) -> CapabilitySpec:
        if name in self._specs:
            raise RegistryError(f"capability {name!r} is already registered")
        spec = CapabilitySpec(
            name=name,
            handler=handler,
            clear_params=ALWAYS_CLEAR | frozenset(clear_params),
            wants_context=_handler_wants_context(handler),
        )
        self._specs[name] = spec
        return spec

    def capability(
        self,
        name: str,
        *,
        clear_params: Iterable[str] = (),
    ) -> Callable[[Handler], Handler]:
        def decorator(fn: Handler) -> Handler:
            self.register(name, fn, clear_params=clear_params)
            return fn

        return decorator

    def get(self, name: str) -> Optional[CapabilitySpec]:
        return self._specs.get(name)

    def names(self) -> FrozenSet[str]:
        return frozenset(self._specs)

    def unregister(self, name: str) -> None:
        self._specs.pop(name, None)


def _handler_wants_context(handler: Handler) -> bool:
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return False
    # Count only positional params (no varargs, no kwargs).
    positional = [
        p
        for p in sig.parameters.values()
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    return len(positional) >= 3


default_registry = CapabilityRegistry()


def capability(
    name: str,
    *,
    clear_params: Iterable[str] = (),
    registry: Optional[CapabilityRegistry] = None,
) -> Callable[[Handler], Handler]:
    reg = registry or default_registry
    return reg.capability(name, clear_params=clear_params)
