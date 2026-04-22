"""``ping`` — the first end-to-end capability.

Takes no params (beyond the optional ``account_id`` that all capabilities
tolerate), returns ``{"pong": true, "server_time": <unix_seconds>}``.

The real reason this exists is to exercise the full pipeline — identity
verification → policy check → capability dispatch → audit write — end-to-
end before any sensitive capability ships. If ``ping`` doesn't round-trip
cleanly, nothing else will either.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from ..identity import RegisteredAgent
from .registry import capability


@capability("ping")
async def ping(agent: RegisteredAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"pong": True, "server_time": int(time.time())}
