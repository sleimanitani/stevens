"""Channel-adapter framework — extracted from OpenClaw, written in our shape.

The single normalized vocabulary every channel adapter (Gmail, Calendar,
WhatsApp Cloud, future Signal/iMessage/Slack/Discord/Telegram) speaks:

- ``ChannelRoute`` — where a message is going / came from
- ``ChannelSession`` — per-thread state envelope around a route
- ``OutboundAdapter`` — the protocol every adapter implements for sends
- ``ContentKind`` + ``Content`` payloads — what we're sending (plain
  text / markdown / chunked / block / approval prompt / typing)
- ``synthesize`` — given a content + adapter capabilities, downgrade to
  the closest supported representation
- ``StreamingDelivery`` — start / append_chunk / finalize, mapped to
  edit-in-place where supported

See ``plans/v0.4.1-channels-framework.md`` and the OpenClaw read-through
in the prior conversation for the rationale and what we deliberately
didn't borrow.
"""

from .adapter import (
    DeliveryRef,
    OutboundAdapter,
)
from .capabilities import AdapterCapabilities
from .content import (
    ApprovalPrompt,
    Block,
    Chunked,
    Content,
    ContentKind,
    Markdown,
    PlainText,
    TypingStart,
    TypingStop,
)
from .echo import EchoAdapter
from .route import ChannelRoute
from .session import ChannelSession
from .streaming import StreamingDelivery
from .synthesis import synthesize

__all__ = [
    "AdapterCapabilities",
    "ApprovalPrompt",
    "Block",
    "ChannelRoute",
    "ChannelSession",
    "Chunked",
    "Content",
    "ContentKind",
    "DeliveryRef",
    "EchoAdapter",
    "Markdown",
    "OutboundAdapter",
    "PlainText",
    "StreamingDelivery",
    "TypingStart",
    "TypingStop",
    "synthesize",
]
