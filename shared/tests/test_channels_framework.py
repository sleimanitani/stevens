"""Tests for the channel-adapter framework."""

from __future__ import annotations

from dataclasses import replace

import pytest

from shared.channels import (
    AdapterCapabilities,
    ApprovalPrompt,
    Block,
    ChannelRoute,
    ChannelSession,
    Chunked,
    ContentKind,
    EchoAdapter,
    Markdown,
    PlainText,
    StreamingDelivery,
    TypingStart,
    synthesize,
)
from shared.channels.synthesis import split_chunked


# --- ChannelRoute ---


def test_route_equality_and_hashing():
    a = ChannelRoute(channel_type="gmail", account_id="gmail.x", target_id="t1")
    b = ChannelRoute(channel_type="gmail", account_id="gmail.x", target_id="t1")
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_route_validates_required_fields():
    with pytest.raises(ValueError, match="channel_type"):
        ChannelRoute(channel_type="", account_id="x", target_id="t")
    with pytest.raises(ValueError, match="account_id"):
        ChannelRoute(channel_type="gmail", account_id="", target_id="t")
    with pytest.raises(ValueError, match="target_id"):
        ChannelRoute(channel_type="gmail", account_id="x", target_id="")


# --- synthesis fallback ---


def _caps(*kinds, **kwargs) -> AdapterCapabilities:
    return AdapterCapabilities(
        supported_kinds=frozenset(kinds), max_chunk_chars=200, **kwargs,
    )


def test_synthesize_passthrough_when_supported():
    caps = _caps(ContentKind.MARKDOWN)
    out = synthesize(Markdown(text="hi"), caps)
    assert isinstance(out, Markdown)


def test_synthesize_block_to_markdown():
    caps = _caps(ContentKind.MARKDOWN)
    block = Block(blocks=[{"text": "header"}, {"text": "body"}], fallback_markdown="**header**\n\nbody")
    out = synthesize(block, caps)
    assert isinstance(out, Markdown)
    assert "header" in out.text


def test_synthesize_block_to_plain_text():
    caps = _caps(ContentKind.PLAIN_TEXT)
    block = Block(blocks=[{"text": "x"}], fallback_markdown="x")
    out = synthesize(block, caps)
    assert isinstance(out, PlainText)


def test_synthesize_chunked_to_plain_text():
    caps = _caps(ContentKind.PLAIN_TEXT)
    out = synthesize(Chunked(text="hello"), caps)
    assert isinstance(out, PlainText)


def test_synthesize_approval_with_block_support():
    caps = _caps(ContentKind.BLOCK, supports_modals=True)
    p = ApprovalPrompt(request_id="r-1", summary="install tesseract", rationale="OCR")
    out = synthesize(p, caps)
    assert isinstance(out, Block)
    assert any("Approval" in (b.get("text", {}).get("text") or "") for b in out.blocks)


def test_synthesize_approval_with_markdown_only():
    caps = _caps(ContentKind.MARKDOWN)
    p = ApprovalPrompt(request_id="r-1", summary="install tesseract")
    out = synthesize(p, caps)
    assert isinstance(out, Markdown)
    assert "/approve r-1" in out.text


def test_synthesize_approval_with_plain_text_only():
    caps = _caps(ContentKind.PLAIN_TEXT)
    p = ApprovalPrompt(request_id="r-1", summary="install tesseract")
    out = synthesize(p, caps)
    assert isinstance(out, PlainText)
    assert "approve" in out.text.lower()


def test_split_chunked_respects_cap():
    caps = _caps(ContentKind.PLAIN_TEXT)
    caps = replace(caps, max_chunk_chars=10)
    parts = split_chunked(Chunked(text="A" * 25), caps)
    assert len(parts) == 3
    assert all(len(p.text) <= 10 for p in parts)


# --- EchoAdapter ---


@pytest.mark.asyncio
async def test_echo_adapter_records_send():
    adapter = EchoAdapter()
    session = ChannelSession(
        route=ChannelRoute("echo", "echo.test", "ch1"),
        capabilities=adapter.capabilities,
    )
    ref = await adapter.send(session, PlainText(text="hello"))
    assert ref.provider_message_id == "echo-1"
    assert len(adapter.sent) == 1
    assert isinstance(adapter.sent[0][1], PlainText)


# --- StreamingDelivery ---


@pytest.mark.asyncio
async def test_streaming_with_edit_support_coalesces():
    adapter = EchoAdapter()  # supports_edits=True by default
    session = ChannelSession(
        route=ChannelRoute("echo", "echo.test", "ch1"),
        capabilities=adapter.capabilities,
    )
    sd = StreamingDelivery(adapter, session)
    await sd.start("hello ")
    await sd.append_chunk("world")
    await sd.append_chunk("!")
    await sd.finalize()
    # One initial send + two edits.
    assert len(adapter.sent) == 1
    assert len(adapter.edits) == 2
    final = adapter.edits[-1][2]
    assert final.text == "hello world!"


@pytest.mark.asyncio
async def test_streaming_without_edit_support_sends_multiple():
    adapter = EchoAdapter(
        capabilities=AdapterCapabilities(
            supported_kinds=frozenset({ContentKind.PLAIN_TEXT}),
            max_chunk_chars=200,
            supports_edits=False,
        ),
    )
    session = ChannelSession(route=ChannelRoute("echo", "echo.test", "ch1"), capabilities=adapter.capabilities)
    sd = StreamingDelivery(adapter, session)
    await sd.start("first")
    await sd.append_chunk("second")
    await sd.append_chunk("third")
    await sd.finalize()
    assert len(adapter.sent) == 3
    assert "second" in adapter.sent[1][1].text
    assert "(continued)" in adapter.sent[1][1].text


@pytest.mark.asyncio
async def test_streaming_double_start_raises():
    adapter = EchoAdapter()
    session = ChannelSession(route=ChannelRoute("echo", "echo.test", "ch1"), capabilities=adapter.capabilities)
    sd = StreamingDelivery(adapter, session)
    await sd.start("x")
    with pytest.raises(Exception, match="already started"):
        await sd.start("y")
