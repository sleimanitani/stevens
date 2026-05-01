"""OutboundAdapter Protocol + DeliveryRef.

``AdapterCapabilities`` lives in ``capabilities.py`` (separate module so
both this and ``session.py`` can depend on it without circular imports).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from .capabilities import AdapterCapabilities  # re-exported via __init__
from .content import Content
from .session import ChannelSession


@dataclass(frozen=True)
class DeliveryRef:
    """Reference to a sent message — for follow-up edits / reactions."""

    provider_message_id: str
    extra: Dict[str, Any] = field(default_factory=dict)


class OutboundAdapter(Protocol):
    """Per-channel outbound adapter. Implementations live under
    ``channels/<name>/.../adapter.py``."""

    channel_type: str
    capabilities: AdapterCapabilities

    async def send(
        self, session: ChannelSession, content: Content,
    ) -> DeliveryRef: ...

    async def edit(
        self, session: ChannelSession, ref: DeliveryRef, content: Content,
    ) -> DeliveryRef:
        """Optional — only adapters with supports_edits=True must implement."""
