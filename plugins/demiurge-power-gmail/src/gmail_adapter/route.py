"""Gmail-side helpers for the channel-adapter framework.

Today's Gmail adapter publishes ``EmailReceivedEvent`` directly. This
helper shows how its inbound state maps onto the framework's
``ChannelRoute`` so the pattern is concrete; future migration of the
adapter onto the framework picks this up.
"""

from __future__ import annotations

from typing import Optional

from shared.channels import ChannelRoute
from shared.events import EmailReceivedEvent


def route_for_inbound(event: EmailReceivedEvent) -> ChannelRoute:
    """Build a ChannelRoute from an inbound EmailReceivedEvent.

    Gmail's "thread" is the canonical conversation; we treat thread_id as
    both target_id and thread_id (target = the thread; thread = the
    same thread for reply-chain semantics).
    """
    return ChannelRoute(
        channel_type="gmail",
        account_id=event.account_id,
        target_id=event.thread_id,
        thread_id=event.thread_id,
        peer_id=event.from_,
    )
