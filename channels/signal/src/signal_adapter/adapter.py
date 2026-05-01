"""SignalAdapter — implements OutboundAdapter (shared.channels)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from shared.channels import (
    AdapterCapabilities,
    ChannelSession,
    Content,
    ContentKind,
    DeliveryRef,
    PlainText,
    synthesize,
)
from shared.channels.synthesis import split_chunked

from .client import SignalCliClient


_SIGNAL_CAPABILITIES = AdapterCapabilities(
    supported_kinds=frozenset({
        ContentKind.PLAIN_TEXT,
        ContentKind.CHUNKED,
    }),
    max_chunk_chars=2000,    # signal-cli limit per message
    supports_edits=False,    # Signal supports edits via the protocol but the REST wrapper doesn't
    supports_threads=False,
    supports_modals=False,
)


@dataclass
class SignalAdapter:
    channel_type: str = "signal"
    capabilities: AdapterCapabilities = _SIGNAL_CAPABILITIES

    def __init__(self, *, from_phone: str, client: SignalCliClient) -> None:
        self.channel_type = "signal"
        self.capabilities = _SIGNAL_CAPABILITIES
        self._from_phone = from_phone
        self._client = client

    async def send(
        self, session: ChannelSession, content: Content,
    ) -> DeliveryRef:
        # Synthesize unsupported kinds (markdown / blocks / approval prompts)
        # down to plain_text / chunked first.
        normalized = synthesize(content, self.capabilities)
        # Chunked → split into multiple sends; return ref of the first.
        from shared.channels.content import Chunked, PlainText as _PT

        if isinstance(normalized, Chunked):
            parts = split_chunked(normalized, self.capabilities)
        elif isinstance(normalized, _PT):
            parts = [normalized]
        else:
            # Typing indicators or anything we can't downgrade — no-op.
            return DeliveryRef(provider_message_id="(unsupported)")

        first_ref: Optional[DeliveryRef] = None
        for part in parts:
            resp = await self._client.send_text(
                from_phone=self._from_phone,
                to=session.route.target_id,
                body=part.text,
            )
            ref = DeliveryRef(
                provider_message_id=str(resp.get("timestamp") or "0"),
                extra={"signal_response": resp},
            )
            if first_ref is None:
                first_ref = ref
        return first_ref or DeliveryRef(provider_message_id="0")

    async def edit(self, session, ref, content):
        # Not supported by signal-cli-rest-api; raise rather than silently
        # losing the edit (caller should check capabilities.supports_edits).
        raise NotImplementedError("signal-cli-rest-api doesn't support edits")
