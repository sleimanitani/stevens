"""Tests for the Gmail outbound path.

These are NOT end-to-end against Google — they use an ``httpx.MockTransport``
to stand in for oauth2.googleapis.com and gmail.googleapis.com. The tests
verify the flow: sealed store → access-token fetch → API call with
Authorization header → capability returns only non-sensitive fields.
"""

from __future__ import annotations

import base64
import json
from typing import List, Tuple

import httpx
import pytest

from demiurge.capabilities.gmail import (  # noqa: F401 — registers caps
    gmail_create_draft,
    gmail_get_thread,
    gmail_search,
)
from demiurge.capabilities.registry import CapabilityRegistry
from demiurge.context import CapabilityContext
from demiurge.outbound.client import OutboundClient
from demiurge.outbound.gmail import GmailClient
from demiurge.sealed_store import initialize_store


PASSPHRASE = b"test-passphrase"


def _populate_store(store) -> None:
    store.add("gmail.oauth_client.id", b"client-123")
    store.add("gmail.oauth_client.secret", b"secret-456")
    store.add("gmail.personal.refresh_token", b"refresh-abc")


def _make_transport(
    calls: List[httpx.Request],
    *,
    token_response: dict,
    api_response: dict,
    api_status: int = 200,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json=token_response)
        return httpx.Response(api_status, json=api_response)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_gmail_search_attaches_bearer_and_returns_threads(tmp_path):
    store = initialize_store(tmp_path / "vault", PASSPHRASE)
    _populate_store(store)

    calls: List[httpx.Request] = []
    transport = _make_transport(
        calls,
        token_response={"access_token": "AT-1", "expires_in": 3600},
        api_response={"threads": [{"id": "t-1"}, {"id": "t-2"}]},
    )

    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.gmail = GmailClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        result = await gmail_search(
            None, {"account_id": "gmail.personal", "query": "label:inbox"}, context
        )
    finally:
        await outbound.aclose()

    assert result == {"threads": [{"id": "t-1"}, {"id": "t-2"}]}
    # Two calls: oauth token, then API.
    assert len(calls) == 2
    oauth, api = calls
    assert "oauth2.googleapis.com/token" in str(oauth.url)
    assert "gmail.googleapis.com" in str(api.url)
    assert api.headers["authorization"] == "Bearer AT-1"


@pytest.mark.asyncio
async def test_gmail_create_draft_returns_only_non_sensitive_metadata(tmp_path):
    store = initialize_store(tmp_path / "vault", PASSPHRASE)
    _populate_store(store)

    calls: List[httpx.Request] = []
    transport = _make_transport(
        calls,
        token_response={"access_token": "AT-2", "expires_in": 3600},
        api_response={"id": "draft-7", "message": {"id": "msg-1", "threadId": "t-1"}},
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.gmail = GmailClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        result = await gmail_create_draft(
            None,
            {
                "account_id": "gmail.personal",
                "thread_id": "t-1",
                "raw_rfc822": b"From: me\r\nSubject: hi\r\n\r\nhello",
            },
            context,
        )
    finally:
        await outbound.aclose()

    assert result == {
        "id": "draft-7",
        "message_id": "msg-1",
        "thread_id": "t-1",
    }
    # Verify the raw MIME was base64-encoded and sent in the body.
    api_call = calls[1]
    body = json.loads(api_call.content.decode("utf-8"))
    raw_b64 = body["message"]["raw"]
    # urlsafe_b64encode strips padding — re-add and decode.
    pad = "=" * ((4 - len(raw_b64) % 4) % 4)
    assert base64.urlsafe_b64decode(raw_b64 + pad) == b"From: me\r\nSubject: hi\r\n\r\nhello"


@pytest.mark.asyncio
async def test_access_token_cached_across_calls(tmp_path):
    store = initialize_store(tmp_path / "vault", PASSPHRASE)
    _populate_store(store)
    calls: List[httpx.Request] = []
    transport = _make_transport(
        calls,
        token_response={"access_token": "AT-3", "expires_in": 3600},
        api_response={"threads": []},
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.gmail = GmailClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        await gmail_search(None, {"account_id": "gmail.personal", "query": "a"}, context)
        await gmail_search(None, {"account_id": "gmail.personal", "query": "b"}, context)
    finally:
        await outbound.aclose()

    # One token exchange + two API calls = 3, not 4.
    assert len(calls) == 3
    assert "oauth2.googleapis.com/token" in str(calls[0].url)
    assert "oauth2.googleapis.com/token" not in str(calls[1].url)
    assert "oauth2.googleapis.com/token" not in str(calls[2].url)


@pytest.mark.asyncio
async def test_api_error_propagates_as_outbound_error(tmp_path):
    from demiurge.outbound.client import OutboundError

    store = initialize_store(tmp_path / "vault", PASSPHRASE)
    _populate_store(store)
    transport = _make_transport(
        [],
        token_response={"access_token": "AT", "expires_in": 3600},
        api_response={"error": "forbidden"},
        api_status=403,
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.gmail = GmailClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        with pytest.raises(OutboundError, match="http 403"):
            await gmail_search(None, {"account_id": "gmail.personal", "query": "x"}, context)
    finally:
        await outbound.aclose()


@pytest.mark.asyncio
async def test_missing_outbound_raises(tmp_path):
    store = initialize_store(tmp_path / "vault", PASSPHRASE)
    _populate_store(store)
    context = CapabilityContext(sealed_store=store, outbound=None)
    with pytest.raises(RuntimeError, match="no outbound client"):
        await gmail_search(None, {"account_id": "gmail.personal"}, context)


@pytest.mark.asyncio
async def test_missing_gmail_attr_on_outbound_raises(tmp_path):
    store = initialize_store(tmp_path / "vault", PASSPHRASE)
    _populate_store(store)
    outbound = OutboundClient(
        sealed_store=store, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    # Note: no outbound.gmail attached.
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        with pytest.raises(RuntimeError, match="not a GmailClient"):
            await gmail_search(None, {"account_id": "gmail.personal"}, context)
    finally:
        await outbound.aclose()
