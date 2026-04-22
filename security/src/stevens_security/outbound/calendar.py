"""Google Calendar outbound client.

Mirrors :mod:`stevens_security.outbound.gmail` in every way that matters —
same OAuth refresh-token → access-token path, same in-memory token cache,
same error discipline. Different API endpoint.

Sealed-store secret-naming convention:

- ``<account_id>.refresh_token``   — per-account OAuth refresh token
                                     (account_id is ``calendar.<slug>``)
- ``calendar.oauth_client.id``     — OAuth client_id (may be the same value
                                     as ``gmail.oauth_client.id`` if Sol
                                     uses one Google OAuth client across
                                     services; stored separately on purpose
                                     — one rotation shouldn't force the
                                     other)
- ``calendar.oauth_client.secret`` — OAuth client_secret
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .client import OutboundClient, OutboundError

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://www.googleapis.com/calendar/v3"


@dataclass
class _CachedAccessToken:
    access_token: str
    expires_at: float


class CalendarClient:
    def __init__(self, outbound: OutboundClient) -> None:
        self._outbound = outbound
        self._cache: Dict[str, _CachedAccessToken] = {}

    async def _access_token(self, account_id: str) -> str:
        now = time.time()
        cached = self._cache.get(account_id)
        if cached and cached.expires_at - now > 60:
            return cached.access_token

        refresh_token = self._outbound.sealed_store.get_by_name(
            f"{account_id}.refresh_token"
        ).decode("utf-8")
        client_id = self._outbound.sealed_store.get_by_name(
            "calendar.oauth_client.id"
        ).decode("utf-8")
        client_secret = self._outbound.sealed_store.get_by_name(
            "calendar.oauth_client.secret"
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
            access_token=access_token, expires_at=now + expires_in
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

    # --- public surface ---

    async def list_calendars(self, account_id: str) -> Dict[str, Any]:
        return await self._api("GET", account_id, "/users/me/calendarList")

    async def list_events(
        self,
        account_id: str,
        calendar_id: str = "primary",
        *,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        q: Optional[str] = None,
        max_results: int = 50,
        single_events: bool = True,
        order_by: str = "startTime",
        sync_token: Optional[str] = None,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "maxResults": max_results,
            "singleEvents": "true" if single_events else "false",
        }
        if sync_token:
            params["syncToken"] = sync_token
        else:
            if time_min:
                params["timeMin"] = time_min
            if time_max:
                params["timeMax"] = time_max
            if q:
                params["q"] = q
            if order_by and single_events:
                params["orderBy"] = order_by
        if page_token:
            params["pageToken"] = page_token
        return await self._api(
            "GET",
            account_id,
            f"/calendars/{calendar_id}/events",
            params=params,
        )

    async def get_event(
        self, account_id: str, calendar_id: str, event_id: str
    ) -> Dict[str, Any]:
        return await self._api(
            "GET", account_id, f"/calendars/{calendar_id}/events/{event_id}"
        )

    async def insert_event(
        self,
        account_id: str,
        calendar_id: str,
        event: Dict[str, Any],
        *,
        send_updates: str = "none",
    ) -> Dict[str, Any]:
        return await self._api(
            "POST",
            account_id,
            f"/calendars/{calendar_id}/events",
            params={"sendUpdates": send_updates},
            json=event,
        )

    async def patch_event(
        self,
        account_id: str,
        calendar_id: str,
        event_id: str,
        patch: Dict[str, Any],
        *,
        send_updates: str = "none",
    ) -> Dict[str, Any]:
        return await self._api(
            "PATCH",
            account_id,
            f"/calendars/{calendar_id}/events/{event_id}",
            params={"sendUpdates": send_updates},
            json=patch,
        )

    async def delete_event(
        self,
        account_id: str,
        calendar_id: str,
        event_id: str,
        *,
        send_updates: str = "none",
    ) -> Dict[str, Any]:
        return await self._api(
            "DELETE",
            account_id,
            f"/calendars/{calendar_id}/events/{event_id}",
            params={"sendUpdates": send_updates},
        )

    async def watch_events(
        self,
        account_id: str,
        calendar_id: str,
        channel_id: str,
        webhook_url: str,
        *,
        channel_token: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Register a push channel for a calendar (returns resourceId + expiration)."""
        body: Dict[str, Any] = {
            "id": channel_id,
            "type": "web_hook",
            "address": webhook_url,
        }
        if channel_token:
            body["token"] = channel_token
        if ttl_seconds:
            body["params"] = {"ttl": str(ttl_seconds)}
        return await self._api(
            "POST",
            account_id,
            f"/calendars/{calendar_id}/events/watch",
            json=body,
        )

    async def stop_channel(
        self, account_id: str, channel_id: str, resource_id: str
    ) -> Dict[str, Any]:
        return await self._api(
            "POST",
            account_id,
            "/channels/stop",
            json={"id": channel_id, "resourceId": resource_id},
        )
