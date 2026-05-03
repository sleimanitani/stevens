"""Tests for SignalCliClient (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest

from signal_adapter.client import (
    IncomingMessage,
    SignalCliClient,
    SignalCliError,
    _parse_envelope,
)


def _client(handler) -> SignalCliClient:
    return SignalCliClient(
        base_url="http://signal-daemon:8080",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_health_ok():
    def h(req):
        return httpx.Response(200, json={"version": "1.2.3"})

    out = await _client(h).health()
    assert out["version"] == "1.2.3"


@pytest.mark.asyncio
async def test_health_5xx_raises():
    def h(req):
        return httpx.Response(500, text="boom")

    with pytest.raises(SignalCliError, match="500"):
        await _client(h).health()


@pytest.mark.asyncio
async def test_send_text_payload_shape():
    captured = []

    def h(req):
        captured.append(req)
        return httpx.Response(201, json={"timestamp": 1730000000000})

    out = await _client(h).send_text(
        from_phone="+15551234567", to="+15557654321", body="hello",
    )
    assert "timestamp" in out
    assert captured[0].url.path == "/v2/send"


@pytest.mark.asyncio
async def test_qr_link_returns_bytes():
    def h(req):
        return httpx.Response(200, content=b"\x89PNG\r\n")

    out = await _client(h).qr_link(phone="+15551234567")
    assert out.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_receive_filters_non_message_envelopes():
    """Receipts / typing indicators (no dataMessage.message) get dropped."""

    def h(req):
        return httpx.Response(200, json=[
            {
                "envelope": {
                    "source": "+15557654321",
                    "sourceUuid": "abcd-1234",
                    "sourceName": "Alice",
                    "timestamp": 1730000000000,
                    "dataMessage": {
                        "timestamp": 1730000000000,
                        "message": "hello world",
                    },
                }
            },
            {"envelope": {"timestamp": 0, "receiptMessage": {}}},  # dropped
        ])

    out = await _client(h).receive(phone="+15551234567")
    assert len(out) == 1
    assert out[0].text == "hello world"
    assert out[0].source_phone == "+15557654321"
    assert out[0].is_group is False


@pytest.mark.asyncio
async def test_receive_parses_group_message():
    def h(req):
        return httpx.Response(200, json=[
            {
                "envelope": {
                    "source": "+15557654321",
                    "timestamp": 1730000000000,
                    "dataMessage": {
                        "timestamp": 1730000000000,
                        "message": "group hello",
                        "groupInfo": {"groupId": "GROUP_ABC123"},
                    },
                }
            },
        ])

    out = await _client(h).receive(phone="+15551234567")
    assert len(out) == 1
    assert out[0].is_group is True
    assert out[0].group_id == "GROUP_ABC123"


@pytest.mark.asyncio
async def test_receive_malformed_json():
    def h(req):
        return httpx.Response(200, content=b"not json")

    with pytest.raises(SignalCliError, match="malformed"):
        await _client(h).receive(phone="+15551234567")


def test_parse_envelope_drops_non_dict():
    assert _parse_envelope("nope") is None
    assert _parse_envelope([]) is None


def test_parse_envelope_drops_empty_text():
    assert _parse_envelope({"envelope": {"dataMessage": {"message": ""}}}) is None
