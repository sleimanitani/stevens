"""Signal-side ChannelRoute construction."""

from __future__ import annotations

from shared.channels import ChannelRoute

from .client import IncomingMessage


def route_for_inbound(account_id: str, msg: IncomingMessage) -> ChannelRoute:
    """Build a ChannelRoute for an inbound Signal message.

    target_id = group id for groups, source phone for DMs (so a reply
    routes to the right place automatically).
    """
    if msg.is_group and msg.group_id:
        target = msg.group_id
    elif msg.source_phone:
        target = msg.source_phone
    elif msg.source_uuid:
        target = msg.source_uuid
    else:
        target = "unknown"
    return ChannelRoute(
        channel_type="signal",
        account_id=account_id,
        target_id=target,
        peer_id=msg.source_uuid or msg.source_phone,
    )
