"""Capability context — the handle capability handlers get to the world.

Handlers receive a :class:`CapabilityContext` as their third positional
argument. It carries references to the sealed secret store, the outbound
HTTP client, and a clock (for tests). Capabilities that don't need these
can ignore the argument.

This is the only blessed way for capabilities to reach shared state.
Module-level globals would work but invite drift — a capability that
touches the sealed store outside this interface is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .sealed_store import SealedStore
    from .outbound.client import OutboundClient


@dataclass(frozen=True)
class CapabilityContext:
    """Read-only handle passed to every capability handler."""

    sealed_store: Optional["SealedStore"] = None
    outbound: Optional["OutboundClient"] = None
    clock: Callable[[], datetime] = field(
        default=lambda: datetime.now(timezone.utc)
    )
    extra: dict = field(default_factory=dict)
