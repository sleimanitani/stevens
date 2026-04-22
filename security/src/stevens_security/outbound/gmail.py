"""Gmail outbound client.

The Security Agent's Gmail adapter. Loads refresh tokens from the sealed
store, exchanges them for short-lived access tokens against Google's OAuth
token endpoint, caches those access tokens in memory until just before
expiry, and uses them to call the Gmail REST API.

Callers never touch the refresh or access token — they just invoke
capabilities like ``gmail.create_draft(account_id, thread_id, body)``.

Sealed-store secret-naming convention:

- ``<account_id>.refresh_token``   — per-account OAuth refresh token
                                     (account_id is already ``gmail.<slug>``
                                     per the PRD's naming, e.g. ``gmail.personal``)
- ``gmail.oauth_client.id``        — shared OAuth client_id
- ``gmail.oauth_client.secret``    — shared OAuth client_secret

All three must exist before the client can make a call.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .client import OutboundClient, OutboundError

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


@dataclass
class _CachedAccessToken:
    access_token: str
    expires_at: float  # unix seconds


class GmailClient:
    """Authenticated Gmail REST client scoped to one account at a time."""

    def __init__(self, outbound: OutboundClient) -> None:
        self._outbound = outbound
        self._cache: Dict[str, _CachedAccessToken] = {}

    async def _access_token(self, account_id: str) -> str:
        """Return a valid access token for ``account_id``, minting one if needed."""
        now = time.time()
        cached = self._cache.get(account_id)
        # Refresh if within 60s of expiry — small cushion for clock skew.
        if cached and cached.expires_at - now > 60:
            return cached.access_token

        refresh_token = self._outbound.sealed_store.get_by_name(
            f"{account_id}.refresh_token"
        ).decode("utf-8")
        client_id = self._outbound.sealed_store.get_by_name(
            "gmail.oauth_client.id"
        ).decode("utf-8")
        client_secret = self._outbound.sealed_store.get_by_name(
            "gmail.oauth_client.secret"
        ).decode("utf-8")

        body = await self._outbound.request(
            "POST",
            _TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        access_token = body.get("access_token")
        expires_in = int(body.get("expires_in") or 0)
        if not access_token or expires_in <= 0:
            raise OutboundError(f"malformed token response: {body!r}")
        self._cache[account_id] = _CachedAccessToken(
            access_token=access_token,
            expires_at=now + expires_in,
        )
        return access_token

    async def _api(
        self,
        method: str,
        account_id: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        token = await self._access_token(account_id)
        return await self._outbound.request(
            method,
            f"{_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            json=json,
        )

    # --- public surface used by capabilities ---

    async def search(
        self, account_id: str, query: str, max_results: int = 20
    ) -> Dict[str, Any]:
        """Return ``{"threads": [{"id": ...}, ...]}`` matching ``query``."""
        return await self._api(
            "GET",
            account_id,
            "/threads",
            params={"q": query, "maxResults": max_results},
        )

    async def get_thread(self, account_id: str, thread_id: str) -> Dict[str, Any]:
        return await self._api(
            "GET", account_id, f"/threads/{thread_id}", params={"format": "full"}
        )

    async def create_draft(
        self, account_id: str, thread_id: str, raw_rfc822: bytes
    ) -> Dict[str, Any]:
        """Create a draft reply to ``thread_id``. ``raw_rfc822`` is the full MIME
        message including headers; caller builds it and the client just ships it."""
        raw_b64 = base64.urlsafe_b64encode(raw_rfc822).decode("ascii").rstrip("=")
        return await self._api(
            "POST",
            account_id,
            "/drafts",
            json={"message": {"raw": raw_b64, "threadId": thread_id}},
        )

    async def add_label(
        self, account_id: str, thread_id: str, label_id: str
    ) -> Dict[str, Any]:
        return await self._api(
            "POST",
            account_id,
            f"/threads/{thread_id}/modify",
            json={"addLabelIds": [label_id]},
        )

    async def remove_label(
        self, account_id: str, thread_id: str, label_id: str
    ) -> Dict[str, Any]:
        return await self._api(
            "POST",
            account_id,
            f"/threads/{thread_id}/modify",
            json={"removeLabelIds": [label_id]},
        )

    async def list_history(
        self,
        account_id: str,
        start_history_id: str,
        *,
        history_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return changes since ``start_history_id``.

        Gmail's ``users.history.list`` is the right primitive for
        Pub/Sub-driven incremental sync: give it the last-seen historyId
        and it tells you what's been added / deleted / labeled since.
        """
        params: Dict[str, Any] = {"startHistoryId": start_history_id}
        if history_types:
            params["historyTypes"] = history_types
        return await self._api("GET", account_id, "/history", params=params)

    async def get_message(
        self, account_id: str, message_id: str, *, fmt: str = "full"
    ) -> Dict[str, Any]:
        return await self._api(
            "GET",
            account_id,
            f"/messages/{message_id}",
            params={"format": fmt},
        )

    async def watch(
        self, account_id: str, topic_name: str, label_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Register (or refresh) Gmail's Pub/Sub push for this account."""
        body: Dict[str, Any] = {"topicName": topic_name}
        if label_ids:
            body["labelIds"] = label_ids
        return await self._api("POST", account_id, "/watch", json=body)

    async def get_profile(self, account_id: str) -> Dict[str, Any]:
        return await self._api("GET", account_id, "/profile")
