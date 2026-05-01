"""ChannelSession — per-thread state envelope."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .capabilities import AdapterCapabilities
from .route import ChannelRoute


@dataclass(frozen=True)
class ChannelSession:
    """Everything an outbound caller needs to address one thread.

    Frozen because the framework treats sessions as values; mutable state
    (recent message log, pending approvals) lives elsewhere. Adapters can
    use the ``extra`` dict for provider-specific scratch (last-edited
    message id, typing-stop deadline, etc.).
    """

    route: ChannelRoute
    capabilities: AdapterCapabilities
    extra: Dict[str, Any] = field(default_factory=dict)
