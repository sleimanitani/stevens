"""Tests for SignalAdapter + route_for_inbound."""

from __future__ import annotations

from typing import List

import httpx
import pytest

from shared.channels import (
    ChannelRoute,
    ChannelSession,
    Markdown,
    PlainText,
)
from shared.channels.content import Chunked

from signal_adapter.adapter import SignalAdapter
from signal_adapter.client import IncomingMessage, SignalCliClient
from signal_adapter.route import route_for_inbound


def _adapter() -> tuple[SignalAdapter, list]:
    captured: list = []

    def handler(req):
        captured.append(req)
        return httpx.Response(201, json={"timestamp": 1730000000000})

    client = SignalCliClient(
        base_url="http://daemon:8080",
        transport=httpx.MockTransport(handler),
    )
    return SignalAdapter(from_phone="+15551234567", client=client), captured


def _session(target="+15557654321") -> ChannelSession:
    adapter, _ = _adapter()
    return ChannelSession(
        route=ChannelRoute(
            channel_type="signal", account_id="signal.personal", target_id=target,
        ),
        capabilities=adapter.capabilities,
    )


# --- send ---


@pytest.mark.asyncio
async def test_send_plain_text_calls_client_with_right_shape():
    adapter, captured = _adapter()
    session = _session(target="+15557654321")
    ref = await adapter.send(session, PlainText(text="hello"))
    assert ref.provider_message_id == "1730000000000"
    body = captured[0].read()
    assert b'"recipients":["+15557654321"]' in body
    assert b'"message":"hello"' in body


@pytest.mark.asyncio
async def test_send_markdown_synthesizes_to_plain_text():
    adapter, captured = _adapter()
    session = _session()
    await adapter.send(session, Markdown(text="*bold*"))
    body = captured[0].read()
    # Markdown isn't supported by Signal; gets sent as plain text.
    assert b"bold" in body


@pytest.mark.asyncio
async def test_send_chunked_splits_at_max_chunk_chars():
    adapter, captured = _adapter()
    session = _session()
    big = "X" * 4500   # > 2000 cap, so 3 chunks
    await adapter.send(session, Chunked(text=big))
    assert len(captured) == 3


# --- route ---


def test_route_for_dm():
    msg = IncomingMessage(
        msg_id="1", source_phone="+15557654321", source_uuid="u-1",
        source_name="Alice", group_id=None, is_group=False,
        text="hi", timestamp=0,
    )
    r = route_for_inbound("signal.personal", msg)
    assert r.channel_type == "signal"
    assert r.account_id == "signal.personal"
    assert r.target_id == "+15557654321"
    assert r.peer_id == "u-1"


def test_route_for_group():
    msg = IncomingMessage(
        msg_id="1", source_phone="+15557654321", source_uuid="u-1",
        source_name="Alice", group_id="GROUP_ABC", is_group=True,
        text="hi", timestamp=0,
    )
    r = route_for_inbound("signal.personal", msg)
    assert r.target_id == "GROUP_ABC"
