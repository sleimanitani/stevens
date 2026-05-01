"""ChannelRoute — the normalized address tuple every adapter speaks.

A route uniquely identifies "where this message lives" across all
channels. Adapters convert their provider-specific identifiers (Gmail
thread_id, Slack channel + thread_ts, WhatsApp wa_id, etc.) into and
out of this shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ChannelRoute:
    channel_type: str         # e.g. "gmail" / "whatsapp_cloud" / "slack"
    account_id: str           # the Stevens account_id (e.g. "gmail.personal")
    target_id: str            # provider-side conversation id (channel id, thread id, group id, …)
    thread_id: Optional[str] = None   # provider-side thread / reply-chain id when distinct from target
    peer_id: Optional[str] = None     # for DMs: the other party's stable id

    def __post_init__(self) -> None:
        if not self.channel_type:
            raise ValueError("channel_type required")
        if not self.account_id:
            raise ValueError("account_id required")
        if not self.target_id:
            raise ValueError("target_id required")
