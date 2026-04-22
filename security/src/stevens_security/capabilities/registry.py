"""Capability registry.

Capabilities are the operations the Security Agent performs on callers'
behalf — "send this Gmail draft," "charge this card," "complete this
prompt." Each capability is declared here with:

- a name (``"gmail.send_draft"``, ``"ping"``, etc)
- an async handler ``(agent, params) -> result_dict``
- a set of ``clear_params`` — the param names whose values are safe to
  log in the clear. Every other param is SHA-256 hashed in the audit
  log. Default: nothing is clear except ``account_id`` (always clear —
  it's a routing label, not a secret).

The module ships a ``default_registry`` singleton that real capability
modules register into on import (see :mod:`stevens_security.capabilities.ping`).
Tests can also create isolated ``CapabilityRegistry`` instances for
parallel/independent test runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, FrozenSet, Iterable, Optional

Handler = Callable[..., Awaitable[Dict[str, Any]]]

# account_id is always considered clear — it's a routing/tenant identifier,
# not a secret. Anything else defaults to sensitive.
ALWAYS_CLEAR: FrozenSet[str] = frozenset({"account_id"})


class RegistryError(Exception):
    """Raised when a capability is double-registered or looked up incorrectly."""


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    handler: Handler
    clear_params: FrozenSet[str] = field(default_factory=frozenset)


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
        )
        self._specs[name] = spec
        return spec

    def capability(
        self,
        name: str,
        *,
        clear_params: Iterable[str] = (),
    ) -> Callable[[Handler], Handler]:
        """Decorator form — ``@registry.capability("foo")``."""

        def decorator(fn: Handler) -> Handler:
            self.register(name, fn, clear_params=clear_params)
            return fn

        return decorator

    def get(self, name: str) -> Optional[CapabilitySpec]:
        return self._specs.get(name)

    def names(self) -> FrozenSet[str]:
        return frozenset(self._specs)

    def unregister(self, name: str) -> None:
        """Intended for tests; production code should not remove capabilities."""
        self._specs.pop(name, None)


# The process-wide default registry. Real capabilities register into this
# on module import. Tests should prefer a local CapabilityRegistry to keep
# state isolated across cases.
default_registry = CapabilityRegistry()


def capability(
    name: str,
    *,
    clear_params: Iterable[str] = (),
    registry: Optional[CapabilityRegistry] = None,
) -> Callable[[Handler], Handler]:
    """Module-level decorator that registers into ``default_registry`` by default."""
    reg = registry or default_registry
    return reg.capability(name, clear_params=clear_params)
