"""EchoAdapter — reference adapter for tests.

Records every send into an in-memory log; supports all content kinds so
synthesis is a no-op. Useful for testing the framework without wiring a
real provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .adapter import DeliveryRef, OutboundAdapter
from .capabilities import AdapterCapabilities
from .content import Content, ContentKind
from .session import ChannelSession


_ALL_KINDS = frozenset(ContentKind)


@dataclass
class EchoAdapter:
    channel_type: str = "echo"
    capabilities: AdapterCapabilities = field(
        default_factory=lambda: AdapterCapabilities(
            supported_kinds=_ALL_KINDS,
            max_chunk_chars=10000,
            supports_edits=True,
            supports_threads=True,
            supports_modals=True,
        )
    )
    sent: List[Tuple[ChannelSession, Content]] = field(default_factory=list)
    edits: List[Tuple[ChannelSession, DeliveryRef, Content]] = field(default_factory=list)
    _id_counter: int = 0

    async def send(self, session: ChannelSession, content: Content) -> DeliveryRef:
        self._id_counter += 1
        ref = DeliveryRef(provider_message_id=f"echo-{self._id_counter}")
        self.sent.append((session, content))
        return ref

    async def edit(
        self, session: ChannelSession, ref: DeliveryRef, content: Content,
    ) -> DeliveryRef:
        self.edits.append((session, ref, content))
        return ref
