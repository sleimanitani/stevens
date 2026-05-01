"""StreamingDelivery — start / append_chunk / finalize for streamed responses."""

from __future__ import annotations

from typing import List, Optional

from .adapter import DeliveryRef, OutboundAdapter
from .content import PlainText
from .session import ChannelSession


class StreamingError(Exception):
    pass


class StreamingDelivery:
    """One streamed message lifecycle.

    Behavior depends on adapter capabilities:
    - ``supports_edits=True``: accumulates the buffer and edits a single
      message in place on each append.
    - ``supports_edits=False``: each append sends a separate message,
      with a header marker showing it's a continuation.
    """

    def __init__(
        self,
        adapter: OutboundAdapter,
        session: ChannelSession,
        *,
        chunk_header: str = "(continued)",
    ) -> None:
        self._adapter = adapter
        self._session = session
        self._chunk_header = chunk_header
        self._first_ref: Optional[DeliveryRef] = None
        self._buffer: List[str] = []
        self._chunks_sent: int = 0
        self._closed = False

    async def start(self, initial_text: str = "") -> None:
        if self._first_ref is not None:
            raise StreamingError("already started")
        self._buffer.append(initial_text)
        self._first_ref = await self._adapter.send(
            self._session, PlainText(text=initial_text or "…"),
        )

    async def append_chunk(self, text: str) -> None:
        if self._closed:
            raise StreamingError("stream is closed")
        if self._first_ref is None:
            await self.start(text)
            return
        self._buffer.append(text)
        if self._session.capabilities.supports_edits:
            full = "".join(self._buffer)
            await self._adapter.edit(
                self._session, self._first_ref, PlainText(text=full),
            )
        else:
            self._chunks_sent += 1
            header = f"{self._chunk_header} {self._chunks_sent}\n" if self._chunk_header else ""
            await self._adapter.send(
                self._session, PlainText(text=f"{header}{text}"),
            )

    async def finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._first_ref is None:
            return  # nothing was ever sent
        # No-op for now; adapters that need a "done" marker (e.g. typing-stop)
        # should handle it here in a future revision.

    @property
    def first_ref(self) -> Optional[DeliveryRef]:
        return self._first_ref
