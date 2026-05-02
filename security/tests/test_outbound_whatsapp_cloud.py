"""Tests for the WhatsApp Cloud API outbound path — mock HTTP stands in for
``graph.facebook.com``. Verifies the auth header, the body shape, and that
capabilities return only non-sensitive metadata."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import List

import httpx
import pytest

from demiurge.capabilities.registry import CapabilityRegistry
from demiurge.capabilities.whatsapp_cloud import (  # noqa: F401
    wac_get_media_url,
    wac_mark_read,
    wac_send_template,
    wac_send_text,
    wac_verify_webhook_signature,
)
from demiurge.context import CapabilityContext
from demiurge.outbound.client import OutboundClient, OutboundError
from demiurge.outbound.whatsapp_cloud import WhatsAppCloudClient
from demiurge.sealed_store import initialize_store


PASSPHRASE = b"test-passphrase"


def _populate(store, *, account="wac.business1"):
    store.add(f"{account}.access_token", b"token-123")
    store.add(f"{account}.phone_number_id", b"999888777")
    store.add("whatsapp_cloud.app_secret", b"app-secret-xyz")


def _transport(calls, *, response):
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_send_text_builds_proper_body(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(
        calls,
        response={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "+15551234567", "wa_id": "15551234567"}],
            "messages": [{"id": "wamid.abc"}],
        },
    )
    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.whatsapp_cloud = WhatsAppCloudClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        result = await wac_send_text(
            None,
            {"account_id": "wac.business1", "to": "+15551234567", "body": "hi"},
            context,
        )
    finally:
        await outbound.aclose()

    assert result["message_ids"] == ["wamid.abc"]
    assert len(calls) == 1
    req = calls[0]
    assert req.url.path == "/v20.0/999888777/messages"
    assert req.headers["authorization"] == "Bearer token-123"
    body = json.loads(req.content.decode("utf-8"))
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "+15551234567"
    assert body["type"] == "text"
    assert body["text"]["body"] == "hi"


@pytest.mark.asyncio
async def test_send_template(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(calls, response={"messages": [{"id": "wamid.t1"}]})
    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.whatsapp_cloud = WhatsAppCloudClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        result = await wac_send_template(
            None,
            {
                "account_id": "wac.business1",
                "to": "+15551234567",
                "name": "appointment_confirmation",
                "language_code": "en_US",
                "components": [{"type": "body", "parameters": [{"type": "text", "text": "3pm"}]}],
            },
            context,
        )
    finally:
        await outbound.aclose()

    assert result["message_ids"] == ["wamid.t1"]
    body = json.loads(calls[0].content.decode("utf-8"))
    assert body["type"] == "template"
    assert body["template"]["name"] == "appointment_confirmation"
    assert body["template"]["components"][0]["type"] == "body"


@pytest.mark.asyncio
async def test_mark_read(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    calls: List[httpx.Request] = []
    transport = _transport(calls, response={"success": True})
    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.whatsapp_cloud = WhatsAppCloudClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        await wac_mark_read(
            None,
            {"account_id": "wac.business1", "message_id": "wamid.in"},
            context,
        )
    finally:
        await outbound.aclose()

    body = json.loads(calls[0].content.decode("utf-8"))
    assert body == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.in",
    }


@pytest.mark.asyncio
async def test_get_media_url_returns_temporary_url(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)

    def handler(request):
        return httpx.Response(
            200, json={"url": "https://lookaside.fbsbx.com/long-signed-url"}
        )

    transport = httpx.MockTransport(handler)
    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.whatsapp_cloud = WhatsAppCloudClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        result = await wac_get_media_url(
            None,
            {"account_id": "wac.business1", "media_id": "media-123"},
            context,
        )
    finally:
        await outbound.aclose()

    assert result["url"].startswith("https://lookaside.fbsbx.com/")


@pytest.mark.asyncio
async def test_verify_webhook_signature_accepts_valid_hmac(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    raw = b'{"object":"whatsapp_business_account","entry":[]}'
    secret = b"app-secret-xyz"
    sig = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()

    outbound = OutboundClient(
        sealed_store=store,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    outbound.whatsapp_cloud = WhatsAppCloudClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        result = await wac_verify_webhook_signature(
            None,
            {"signature": sig, "raw_body": raw},
            context,
        )
    finally:
        await outbound.aclose()
    assert result == {"valid": True}


@pytest.mark.asyncio
async def test_verify_webhook_signature_rejects_bad_hmac(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    raw = b'{"object":"whatsapp_business_account","entry":[]}'
    # Wrong secret.
    sig = "sha256=" + hmac.new(b"wrong", raw, hashlib.sha256).hexdigest()

    outbound = OutboundClient(
        sealed_store=store,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    outbound.whatsapp_cloud = WhatsAppCloudClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        result = await wac_verify_webhook_signature(
            None,
            {"signature": sig, "raw_body": raw},
            context,
        )
    finally:
        await outbound.aclose()
    assert result == {"valid": False}


@pytest.mark.asyncio
async def test_verify_webhook_signature_rejects_missing_prefix(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)
    outbound = OutboundClient(
        sealed_store=store,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    outbound.whatsapp_cloud = WhatsAppCloudClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        result = await wac_verify_webhook_signature(
            None, {"signature": "not-a-signature", "raw_body": b"x"}, context
        )
    finally:
        await outbound.aclose()
    assert result == {"valid": False}


@pytest.mark.asyncio
async def test_http_error_propagates(tmp_path):
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    _populate(store)

    def handler(request):
        return httpx.Response(
            401, json={"error": {"message": "bad token", "code": 190}}
        )

    transport = httpx.MockTransport(handler)
    outbound = OutboundClient(sealed_store=store, transport=transport)
    outbound.whatsapp_cloud = WhatsAppCloudClient(outbound)  # type: ignore[attr-defined]
    try:
        context = CapabilityContext(sealed_store=store, outbound=outbound)
        with pytest.raises(OutboundError, match="http 401"):
            await wac_send_text(
                None,
                {"account_id": "wac.business1", "to": "+1555", "body": "hi"},
                context,
            )
    finally:
        await outbound.aclose()
