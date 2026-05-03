"""Tests for the signal-adapter inbound poll loop."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from signal_adapter.client import SignalCliClient
from signal_adapter.main import poll_loop


@pytest.mark.asyncio
async def test_poll_publishes_events_for_each_message():
    def handler(req):
        return httpx.Response(200, json=[
            {"envelope": {
                "source": "+15557654321",
                "timestamp": 1730000000000,
                "dataMessage": {"timestamp": 1730000000000, "message": "hi"},
            }},
            {"envelope": {
                "source": "+15557654322",
                "timestamp": 1730000001000,
                "dataMessage": {"timestamp": 1730000001000, "message": "yo"},
            }},
        ])

    client = SignalCliClient(
        base_url="http://daemon:8080", transport=httpx.MockTransport(handler),
    )
    published = []

    async def publisher(event):
        published.append(event)

    await poll_loop(
        client=client, account_id="signal.personal", phone="+15551234567",
        interval_seconds=0.0, max_iterations=1, publisher=publisher,
    )
    assert len(published) == 2
    assert published[0].text == "hi"
    assert published[1].text == "yo"
    assert all(e.account_id == "signal.personal" for e in published)


@pytest.mark.asyncio
async def test_poll_backs_off_on_daemon_error():
    """Daemon 500 → no events published, loop continues with backoff."""
    def handler(req):
        return httpx.Response(500, text="boom")

    client = SignalCliClient(
        base_url="http://daemon:8080", transport=httpx.MockTransport(handler),
    )
    published = []

    async def publisher(event):
        published.append(event)

    # Two iterations of receive failure; nothing published.
    await poll_loop(
        client=client, account_id="signal.personal", phone="+15551234567",
        interval_seconds=0.0, max_iterations=2, publisher=publisher,
    )
    assert published == []


@pytest.mark.asyncio
async def test_poll_publish_failure_doesnt_crash():
    """If the bus rejects the event, the loop logs and continues."""
    def handler(req):
        return httpx.Response(200, json=[
            {"envelope": {
                "source": "+15557654321",
                "timestamp": 1,
                "dataMessage": {"timestamp": 1, "message": "x"},
            }},
        ])

    client = SignalCliClient(
        base_url="http://daemon:8080", transport=httpx.MockTransport(handler),
    )

    async def bad_publisher(event):
        raise RuntimeError("bus down")

    # Should complete without raising.
    await poll_loop(
        client=client, account_id="signal.personal", phone="+15551234567",
        interval_seconds=0.0, max_iterations=1, publisher=bad_publisher,
    )
