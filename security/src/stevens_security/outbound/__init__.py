"""Outbound HTTP proxy — the Security Agent's own egress surface.

Only the Security Agent talks to external APIs (Gmail, Anthropic,
payment processors, ...). Other agents request capabilities through
the UDS broker; the Security Agent loads the right credential from the
sealed store, attaches it to an outbound HTTP call, and returns the
non-sensitive result. Raw tokens never leave this process.

In v0.1-sec we ship the general shape (:class:`OutboundClient`) plus
one consumer (:mod:`.gmail`). Future channels (Anthropic, payment,
Google Calendar, WhatsApp Cloud) plug into the same shape.
"""

from .client import OutboundClient, OutboundError  # noqa: F401
