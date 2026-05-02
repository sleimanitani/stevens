"""Tests for the Google Calendar outbound path — mock HTTP stands in for
``oauth2.googleapis.com`` and ``www.googleapis.com/calendar/v3``."""

from __future__ import annotations

import json
from typing import List

import httpx
import pytest

from demiurge.capabilities.calendar import (  # noqa: F401
    cal_delete_event,
    cal_get_event,
    cal_insert_event,
    cal_list_calendars,
    cal_list_events,
    cal_patch_event,
    cal_stop_channel,
    cal_watch_events,
)
from demiurge.context import CapabilityContext
from demiurge.outbound.calendar import CalendarClient
from demiurge.outbound.client import OutboundClient, OutboundError
from demiurge.sealed_store import initialize_store


PASSPHRASE = b"test-passphrase"


def _populate(store, *, account="calendar.personal"):
    store.add(f"{account}.refresh_token", b"refresh-abc")
    store.add("calendar.oauth_client.id", b"cid-123")
    store.add("calendar.oauth_client.secret", b"csecret-456")


def _transport(calls, *, token_response, api_response, api_status=200):
    def handler(request):
        calls.append(request)
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json=token_response)
        return httpx.Response(api_status, json=api_response)

    return httpx.MockTransport(handler)


def _make(outbound, store):
    outbound.calendar = CalendarClient(outbound)  # type: ignore[attr-defined]
    return CapabilityContext(sealed_store=store, outbound=outbound)


@pytest.mark.asyncio
async def test_list_events_builds_query(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(
        calls,
        token_response={"access_token": "AT-1", "expires_in": 3600},
        api_response={"items": [{"id": "e1"}, {"id": "e2"}]},
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    try:
        context = _make(outbound, store)
        result = await cal_list_events(
            None,
            {
                "account_id": "calendar.personal",
                "time_min": "2026-04-23T00:00:00Z",
                "time_max": "2026-04-24T00:00:00Z",
                "max_results": 25,
            },
            context,
        )
    finally:
        await outbound.aclose()

    assert [e["id"] for e in result["items"]] == ["e1", "e2"]
    api = calls[1]
    assert "calendar/v3/calendars/primary/events" in str(api.url)
    assert api.headers["authorization"] == "Bearer AT-1"
    assert "timeMin=2026-04-23T00%3A00%3A00Z" in str(api.url)
    assert "maxResults=25" in str(api.url)


@pytest.mark.asyncio
async def test_insert_event_sends_body(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(
        calls,
        token_response={"access_token": "AT-2", "expires_in": 3600},
        api_response={"id": "evt-xyz", "htmlLink": "https://..."},
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    try:
        context = _make(outbound, store)
        event = {
            "summary": "1:1 with Atheer",
            "start": {"dateTime": "2026-04-23T15:00:00-07:00"},
            "end": {"dateTime": "2026-04-23T15:30:00-07:00"},
        }
        result = await cal_insert_event(
            None,
            {
                "account_id": "calendar.personal",
                "calendar_id": "primary",
                "event": event,
                "send_updates": "all",
            },
            context,
        )
    finally:
        await outbound.aclose()

    assert result["id"] == "evt-xyz"
    body = json.loads(calls[1].content.decode("utf-8"))
    assert body["summary"] == "1:1 with Atheer"
    assert "sendUpdates=all" in str(calls[1].url)


@pytest.mark.asyncio
async def test_patch_event(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(
        calls,
        token_response={"access_token": "AT", "expires_in": 3600},
        api_response={"id": "evt-xyz", "summary": "updated"},
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    try:
        context = _make(outbound, store)
        result = await cal_patch_event(
            None,
            {
                "account_id": "calendar.personal",
                "calendar_id": "primary",
                "event_id": "evt-xyz",
                "patch": {"summary": "updated"},
            },
            context,
        )
    finally:
        await outbound.aclose()
    assert result["summary"] == "updated"
    assert calls[1].method == "PATCH"


@pytest.mark.asyncio
async def test_delete_event(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []

    def handler(request):
        calls.append(request)
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(
                200, json={"access_token": "AT", "expires_in": 3600}
            )
        # Calendar delete returns 204 No Content.
        return httpx.Response(204)

    outbound = OutboundClient(sealed_store=store, transport=httpx.MockTransport(handler))
    try:
        context = _make(outbound, store)
        result = await cal_delete_event(
            None,
            {
                "account_id": "calendar.personal",
                "calendar_id": "primary",
                "event_id": "evt-xyz",
            },
            context,
        )
    finally:
        await outbound.aclose()
    # Empty body returns as {}.
    assert result == {}
    assert calls[1].method == "DELETE"


@pytest.mark.asyncio
async def test_watch_events(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(
        calls,
        token_response={"access_token": "AT", "expires_in": 3600},
        api_response={
            "id": "chan-123",
            "resourceId": "res-xyz",
            "expiration": "1800000000000",
        },
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    try:
        context = _make(outbound, store)
        result = await cal_watch_events(
            None,
            {
                "account_id": "calendar.personal",
                "calendar_id": "primary",
                "channel_id": "chan-123",
                "webhook_url": "https://stevens.example/calendar/push",
                "channel_token": "some-verify-token",
                "ttl_seconds": 604800,
            },
            context,
        )
    finally:
        await outbound.aclose()
    assert result["resourceId"] == "res-xyz"
    body = json.loads(calls[1].content.decode("utf-8"))
    assert body["id"] == "chan-123"
    assert body["type"] == "web_hook"
    assert body["token"] == "some-verify-token"
    assert body["params"] == {"ttl": "604800"}


@pytest.mark.asyncio
async def test_list_events_with_sync_token_omits_time_bounds(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(
        calls,
        token_response={"access_token": "AT", "expires_in": 3600},
        api_response={"items": []},
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    try:
        context = _make(outbound, store)
        await cal_list_events(
            None,
            {
                "account_id": "calendar.personal",
                "sync_token": "syncTok-abc",
                # These are ignored when sync_token is present.
                "time_min": "2026-01-01T00:00:00Z",
            },
            context,
        )
    finally:
        await outbound.aclose()
    url = str(calls[1].url)
    assert "syncToken=syncTok-abc" in url
    # Ensure the mutually-exclusive params were not sent.
    assert "timeMin=" not in url
    assert "orderBy=" not in url


@pytest.mark.asyncio
async def test_access_token_cached_across_calendar_calls(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(
        calls,
        token_response={"access_token": "AT", "expires_in": 3600},
        api_response={"items": []},
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    try:
        context = _make(outbound, store)
        await cal_list_events(None, {"account_id": "calendar.personal"}, context)
        await cal_list_calendars(None, {"account_id": "calendar.personal"}, context)
    finally:
        await outbound.aclose()
    # 1 token + 2 API = 3, not 4.
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_api_error_propagates(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    transport = _transport(
        [],
        token_response={"access_token": "AT", "expires_in": 3600},
        api_response={"error": {"code": 403, "message": "forbidden"}},
        api_status=403,
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    try:
        context = _make(outbound, store)
        with pytest.raises(OutboundError, match="http 403"):
            await cal_list_events(None, {"account_id": "calendar.personal"}, context)
    finally:
        await outbound.aclose()
