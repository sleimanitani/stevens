"""AdminClient — operator-side helper that signs admin-capability calls.

Used by the ``demiurge approval`` CLI to nudge a running Enkidu after grants /
revokes / approvals (refresh the in-memory matcher, mark a request_id as
approved-replay-ready). Best-effort: if the operator key file is missing or
the UDS isn't there, the helper logs and returns silently — admin nudges
are convenience, not correctness. The next Enkidu boot will pick the new
state up from the DB regardless.

Operator key path resolution (in order):
  1. ``$DEMIURGE_OPERATOR_PRIVATE_KEY_PATH``
  2. ``~/.config/demiurge/agents/operator.key``
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional


log = logging.getLogger(__name__)


def _operator_key_path() -> Path:
    env = os.environ.get("DEMIURGE_OPERATOR_PRIVATE_KEY_PATH")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "demiurge" / "agents" / "operator.key"


def _socket_path() -> str:
    return os.environ.get("DEMIURGE_SECURITY_SOCKET", "/run/demiurge/security.sock")


class AdminClient:
    """Wraps a SecurityClient signed with the operator key.

    Construct via ``AdminClient.try_create()`` which returns None if the key
    file is missing — that's the common dev-mode case (operator hasn't
    provisioned an operator identity yet).
    """

    def __init__(self, client) -> None:
        self._client = client

    @classmethod
    def try_create(cls) -> Optional["AdminClient"]:
        """Return an AdminClient if the operator key is available, else None."""
        key_path = _operator_key_path()
        if not key_path.exists():
            log.debug("operator key not found at %s; admin nudges disabled", key_path)
            return None
        from shared.security_client import SecurityClient, TransportError

        try:
            client = SecurityClient.from_key_file(
                socket_path=_socket_path(),
                caller_name="operator",
                private_key_path=str(key_path),
            )
        except TransportError as e:
            log.warning("operator key at %s unusable: %s", key_path, e)
            return None
        return cls(client)

    async def refresh_approvals(self) -> Optional[Dict[str, Any]]:
        """Tell Enkidu to reload its standing-approval matcher from the store."""
        return await self._call("_admin.refresh_approvals", {})

    async def mark_request_approved(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Tell Enkidu to expect a replay envelope for ``request_id``."""
        return await self._call(
            "_admin.mark_request_approved", {"request_id": request_id},
        )

    async def _call(
        self, capability: str, params: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        from shared.security_client import (
            ResponseError,
            TransportError,
        )

        try:
            return await self._client.call(capability, params)
        except TransportError as e:
            # Enkidu likely not running. Not an error from the operator's
            # perspective — they'll restart Enkidu and the DB state will
            # be picked up.
            log.info("admin %s skipped (Enkidu not reachable): %s", capability, e)
            return None
        except ResponseError as e:
            log.warning("admin %s failed: %s", capability, e)
            return None
